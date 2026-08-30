"""Score a judge against its own labelled testset, offline, before provisioning it.

`data/testsets/` ships 60 labelled samples per judge and, until this module,
nothing read them: the folder carried a testing strategy and the data for it with
no code joining the two, so the only way to learn whether a rubric edit helped was
to create the evaluators against a live account and read CloudWatch afterwards.

What is scored here is THIS FOLDER'S artifact, not the rubric in the abstract.
Every item that produces a verdict takes the path a real invocation takes —
`spans.Conversation.render` for the transcript, `judge.build_request` for the
prompt, `boundary.agentcore_result` for the return boundary (the same function
`handler` returns, imported not copied), `verify` for the payload — so the layers
that exist only on the AgentCore path are the ones under test. A rubric that
scores well through a hand-rolled prompt and badly through the Lambda's own
render is precisely the failure this exists to catch, and a hand-rolled prompt
would hide it.

An item that produces NO verdict — every sample lost to a connection reset, a
429, an expired key — reaches no boundary at all, because there is nothing to
compact. Those items are reported on their own `transport` line and excluded from
every column of the table. That is the one deliberate hole in "every item takes
the real path", and naming it here is cheaper than a docstring that overstates
the fidelity this folder exists to provide.

The testsets cannot establish validity: they are model-written and model-labelled,
so agreement with them is agreement with a model that read the same rubric. They
are a regression instrument. `docs/TESTING.md` says what that does and does not buy.
"""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import BAND_COLUMNS, BANDS
from .boundary import SCOPE, _compact, agentcore_result
from .judge import K, AllSamplesFailed, Verdict, build_request, run
from .spans import Conversation, Role, Turn
from .spec import ROOT, Rubric, deployable, load_rubrics
from .verify import Result, verify

TESTSET_DIR = ROOT / "data" / "testsets"

# BANDS (lowest first) and BAND_COLUMNS (highest first) are imported from the
# package root, where they are derived from SCALE. They are re-exported here
# because this is where they are read, but they are not defined twice.
BAND_NAME = dict(BAND_COLUMNS)

# Everything the Anthropic SDK raises when we never got an answer, plus the httpx
# and builtin exceptions underneath it. Matched by CLASS NAME, walking the MRO,
# rather than by importing `anthropic` and `httpx`: every offline command in this
# package has to work with the SDK uninstalled, and a transport taxonomy that
# needs the SDK installed to classify an SDK error would defeat itself.
TRANSPORT_ERRORS = frozenset({
    "APIConnectionError", "APITimeoutError", "APIStatusError", "RateLimitError",
    "AuthenticationError", "PermissionDeniedError", "InternalServerError",
    "ServiceUnavailableError", "OverloadedError",
    "TransportError", "TimeoutException", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "TimeoutError", "ConnectionError",
})

# Either satisfies `anthropic.Anthropic()`; neither present means no live run is
# possible and --dry-run becomes the default rather than a stack trace.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

ROLES: tuple[Role, ...] = ("customer", "assistant")


@dataclass(frozen=True)
class Item:
    """One labelled sample, already in the shape the Lambda would receive it."""

    id: str
    judge_id: str
    expected: float
    conversation: Conversation
    target_turn: int | None
    scope: str

    @property
    def transcript(self) -> str:
        return self.conversation.render(self.target_turn)


@dataclass
class ItemResult:
    """One item's outcome, with the two ways of having no score kept apart.

    `transport` means we never got an answer. `unparseable` means we did, and it
    broke this path's output contract. They look identical from here — both have
    `score is None` — and conflating them is the single most misleading thing
    this tool could do, because one is a fact about the judge and the other is a
    fact about the network.
    """

    item: Item
    score: float | None = None
    label: str | None = None
    verdict: Verdict | None = None
    verification: Result | None = None
    explanation: str | None = None
    error: str | None = None
    transport: bool = False

    @property
    def unparseable(self) -> bool:
        return self.score is None and not self.transport


@dataclass
class Tally:
    """One judge's counts. `n` is measured items only — transport is outside it.

    `transport` is deliberately not folded into `n`, `exact`, `within`, `fp0`,
    `fn0` or the bands: an item we never got an answer for is not evidence about
    the judge in either direction, and counting it as a miss would report an
    outage as "this judge missed a hard failure".
    """

    judge_id: str
    n: int = 0
    exact: int = 0
    within: int = 0
    fp0: int = 0
    fn0: int = 0
    unparseable: int = 0
    transport: int = 0
    band_hits: dict[float, int] = field(default_factory=lambda: {b: 0 for b in BANDS})
    band_total: dict[float, int] = field(default_factory=lambda: {b: 0 for b in BANDS})
    diverged: list[str] = field(default_factory=list)
    invalid_payloads: list[str] = field(default_factory=list)


def is_transport_error(exc: BaseException) -> bool:
    """Did we fail to get an answer at all, as opposed to getting a bad one?

    Walks the MRO by class name so that `RateLimitError` and
    `AuthenticationError` — both `APIStatusError` subclasses in the Anthropic SDK
    — are caught by their base as well as by name, and so that a caller's own
    stand-in class named like a transport error classifies the same way.
    """
    return any(c.__name__ in TRANSPORT_ERRORS for c in type(exc).__mro__)


def level_of(value: Any) -> float | None:
    """Coerce a reported score to one of the three levels, or refuse.

    `Verdict.label` indexes a three-key dict, so anything else is a KeyError deep
    inside the handler rather than a bad grade here. The median of an EVEN number
    of surviving samples is the realistic way to get there — k=5 with one API
    error leaves four, and [0.5, 0.5, 1.0, 1.0] medians to 0.75.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v in BANDS else None


def _turn(raw: dict[str, Any], item_id: str) -> Turn:
    role = raw.get("role")
    if role not in ROLES:
        raise ValueError(f"{item_id}: turn role {role!r} not in {ROLES}; the renderer "
                         f"would silently label it ASSISTANT")
    return Turn(role=role, text=raw.get("text", ""),
                tool_trace=list(raw.get("tool_trace") or []))


def _expected(raw: Any, *, src: Path, item_id: Any) -> float:
    """Coerce a row's label to a band, or refuse by name.

    `Tally.band_total` and `band_hits` are keyed by exactly the three bands, so a
    row labelled 0.7 — or "0.5", or None — does not fail on the row that carries
    it. It fails several hundred lines later as a KeyError inside `tally()`,
    after the draw, after the confirmation, after the money. The file, the row id
    and the offending value are all known HERE, so they are said here.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = None
    if value not in BANDS:
        raise ValueError(
            f"{src}: row {item_id!r} has expected_score {raw!r}; the scale is "
            f"{list(BANDS)}. The band counters are keyed by those three values, "
            f"so anything else surfaces as a KeyError inside tally() long after "
            f"this row was read."
        )
    return value


def load_testset(rubric: Rubric, *, path: Path | None = None) -> list[Item]:
    """Read one judge's labelled samples into renderable conversations.

    The turns are wrapped in `spans.Conversation` rather than rendered here, so
    the testset and CloudWatch reach the model through the same renderer. The
    rubrics name that renderer's markers by hand — `--- turn N | CUSTOMER ---`,
    ` >>> TARGET` — and a second renderer would drift from them unnoticed.
    """
    src = path or (TESTSET_DIR / f"{rubric.id}.jsonl")
    items: list[Item] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        conv = Conversation(
            session_id=rec["id"],
            turns=[_turn(t, rec["id"]) for t in rec["turns"]],
        )
        target = None if rubric.unit == "conversation" else rec.get("target_turn")
        items.append(Item(id=rec["id"], judge_id=rubric.id,
                          expected=_expected(rec.get("expected_score"), src=src,
                                             item_id=rec.get("id")),
                          conversation=conv, target_turn=target,
                          scope=SCOPE[rubric.unit]))
    return items


def band_sizes(limit: int) -> dict[float, int]:
    """How many samples to draw from each expected-score band."""
    if limit <= 0:
        return {b: 0 for b in BANDS}
    per, remainder = divmod(limit, 3)
    return {b: per + (1 if i < remainder else 0) for i, b in enumerate(BANDS)}


def select(items: list[Item], *, judge_id: str, limit: int, seed: int) -> list[Item]:
    """Draw a band-balanced subset.

    Balance is not a nicety on this data: the sets are 20/20/20 by construction,
    so an unbalanced draw moves the exact-match rate without any judge behaviour
    changing, and two runs of `--limit 6` would not be comparable. The RNG is
    keyed per band as well as per judge so that raising --limit extends a draw
    band by band instead of reshuffling the whole sample.
    """
    if limit <= 0:
        return sorted(items, key=lambda i: i.id)
    sizes = band_sizes(limit)
    drawn: list[Item] = []
    for band in BANDS:
        pool = sorted((i for i in items if i.expected == band), key=lambda i: i.id)
        n = min(sizes[band], len(pool))
        drawn.extend(random.Random(f"{seed}:{judge_id}:{band}").sample(pool, n))
    return sorted(drawn, key=lambda i: i.id)


@dataclass(frozen=True)
class Shortfall:
    """A band that could not fill its quota, and by how much."""

    judge_id: str
    band: float
    wanted: int
    drew: int

    def __str__(self) -> str:
        return (f"{self.judge_id} {BAND_NAME[self.band]}: asked for {self.wanted}, "
                f"drew {self.drew} — the set holds only {self.drew} item(s) "
                f"labelled {self.band}")


def shortfalls(items: list[Item], *, judge_id: str, limit: int) -> list[Shortfall]:
    """Which bands `select` will silently under-draw.

    Under-drawing is the right behaviour — a missing item cannot be conjured —
    but it must not be silent. A `fail` column reading `4/4` looks like a clean
    sweep of six, and a reader comparing it against another judge's `4/6` would
    be comparing a full quota against a short one without being told.
    """
    if limit <= 0:
        return []
    sizes = band_sizes(limit)
    out = []
    for band in BANDS:
        have = sum(1 for i in items if i.expected == band)
        if have < sizes[band]:
            out.append(Shortfall(judge_id, band, sizes[band], have))
    return out


def grade(rubric: Rubric, item: Item, verdict: Verdict) -> ItemResult:
    """Put the verdict through the return boundary before believing its score.

    The Lambda does not hand AgentCore a `Verdict`; it hands back the three-field
    dict `boundary.agentcore_result` builds, and the compacted `explanation` in
    it is all any consumer ever sees. Grading the in-memory verdict would skip
    the only step on this path that has ever lost data, so an in-scale verdict is
    put through THAT FUNCTION — the one `handler` returns, imported rather than
    reimplemented, label and value included — and an item whose score does not
    survive the trip is counted unparseable rather than quietly scored on a
    number nothing downstream would receive.

    An off-scale score is the one place the two paths must differ. `Verdict.label`
    indexes the three-point scale, so production raises and the invocation dies;
    the scorer instead compacts what it can and records an unparseable item with
    no label, because inventing a label the Lambda could never have returned
    would hide exactly the defect this run was bought to find. The median of an
    EVEN number of surviving samples is the realistic way to get here.

    `_compact` indexes the payload's `checks` directly, which is safe in the
    Lambda because Anthropic validated the shape at generation time — but a
    scorer that inherited that assumption would abort the whole run on the one
    item it was built to report.
    """
    score = level_of(verdict.score)
    try:
        if score is None:
            explanation, label = (
                _compact(verdict, item.conversation, item.target_turn), None)
        else:
            out = agentcore_result(verdict, item.conversation, item.target_turn)
            explanation, label = out["explanation"], out["label"]
    except (KeyError, TypeError, IndexError) as e:
        return ItemResult(item=item, verdict=verdict,
                          error=f"payload did not survive compaction: "
                                f"{type(e).__name__}: {e}")
    res = verify(rubric, explanation, label, transcript=item.transcript)
    return ItemResult(item=item, score=score, label=label, verdict=verdict,
                      verification=res, explanation=explanation)


def run_item(rubric: Rubric, item: Item, *, k: int, client: Any = None) -> ItemResult:
    """One live item. A failed item is recorded, never raised — and classified.

    `judge.run` raises when all k samples fail, and one dead item must not throw
    away the run that preceded it — the whole point of the exercise is the table
    at the end. But WHICH kind of failure decides whether the item belongs in the
    table at all, so the causes are read rather than stringified:

      * every cause a transport error -> we never got an answer. Not evidence
        about the judge; excluded from the metrics, counted on its own line.
      * anything else among them -> at least one answer came back and broke the
        contract. That is the failure this scorer exists to catch, so it stays
        in `n` and in the band denominators.

    "Any non-transport cause makes the item unparseable" is the conservative
    direction: it can only ever move an item INTO the metrics, never quietly out.
    """
    try:
        verdict = run(rubric, item.transcript, item.scope, k=k, client=client)
    except AllSamplesFailed as e:
        if e.causes and all(is_transport_error(c) for c in e.causes):
            first = e.causes[0]
            return ItemResult(item=item, transport=True,
                              error=f"{type(first).__name__}: {first}")
        return ItemResult(item=item, error=str(e))
    except Exception as e:  # noqa: BLE001 — the error IS the result for this item
        return ItemResult(item=item, transport=is_transport_error(e),
                          error=f"{type(e).__name__}: {e}")
    return grade(rubric, item, verdict)


def tally(judge_id: str, results: Iterable[ItemResult]) -> Tally:
    """Aggregate one judge's items.

    Unparseable items stay in `n` and in their band's denominator. They are a
    failure of this folder's own output contract, and dropping them would let the
    thing this scorer exists to detect improve the score it reports. An
    unparseable item on a 0-labelled sample also counts as `fn0`: the bright line
    went uncaught, and the reason it went uncaught does not change that.

    Transport failures do the opposite, for the opposite reason. We never got an
    answer, so there is no output contract to have broken and no judgement to
    grade; the item is counted on `transport` and touches nothing else — not `n`,
    not `fn0`, not the band denominators. An outage reported as "this judge
    missed a hard failure" would be a measurement tool lying about its subject.
    """
    t = Tally(judge_id=judge_id)
    for r in results:
        if r.transport:
            t.transport += 1
            continue
        if r.item.expected not in t.band_total:
            raise ValueError(f"{r.item.id}: expected {r.item.expected!r} is not "
                             f"one of {list(BANDS)}")
        t.n += 1
        t.band_total[r.item.expected] += 1
        if r.unparseable:
            t.unparseable += 1
            if r.item.expected == 0.0:
                t.fn0 += 1
            continue
        assert r.score is not None
        if r.score == r.item.expected:
            t.exact += 1
            t.band_hits[r.item.expected] += 1
        if abs(r.score - r.item.expected) <= 0.5:
            t.within += 1
        if r.score == 0.0 and r.item.expected != 0.0:
            t.fp0 += 1
        if r.item.expected == 0.0 and r.score != 0.0:
            t.fn0 += 1
        v = r.verification
        if v is not None and v.diverged:
            t.diverged.append(f"{r.item.id}: scored {v.label}, its own checks imply "
                              f"{v.recomputed}")
        if v is not None and not v.ok:
            t.invalid_payloads.append(f"{r.item.id}: {v.errors[0]}")
    return t


HEADER = (f"{'judge':<33}{'n':<4}{'exact':<6} {'±1 lvl':<6}{'fp0':>6}{'fn0':>6}   "
          f"{'pass':<6}{'warn':<6}{'fail'}")
RULE = "-" * 78
LEGEND = ("fp0 = judge scored 0 where the label was not 0 (false alarm)\n"
          "fn0 = label was 0 and the judge did not catch it (missed bright line)")


def _pct(hit: int, n: int) -> str:
    return "-" if not n else f"{100.0 * hit / n:.1f}%"


def format_report(tallies: list[Tally],
                  shorts: list[Shortfall] | None = None) -> str:
    """The table, identical in shape across all three integration folders.

    Comparability is the reason it is pinned: the same rubrics run on three
    platforms, and a difference in the numbers is only evidence about a platform
    if the two runs were counted the same way.

    Two lines sit under it, and they mean opposite things. `unparseable` counts
    items that ARE in the table — answers that broke the contract. `transport`
    counts items that are NOT, because no answer arrived; they are printed rather
    than dropped, so a shrunken `n` has a stated cause instead of being a silent
    hole in the denominator.
    """
    lines = [HEADER, RULE]
    for t in tallies:
        bands = "".join(
            f"{f'{t.band_hits[b]}/{t.band_total[b]}':<6}" for b, _ in BAND_COLUMNS
        ).rstrip()
        lines.append(f"{t.judge_id:<33}{t.n:<4}{_pct(t.exact, t.n):<6} "
                     f"{_pct(t.within, t.n):<6}{t.fp0:>6}{t.fn0:>6}   {bands}")
    bad = sum(t.unparseable for t in tallies)
    detail = ", ".join(f"{t.judge_id} x{t.unparseable}"
                       for t in tallies if t.unparseable)
    lines.append(f"{'unparseable':<33}{bad:<4}{detail}".rstrip())
    lost = sum(t.transport for t in tallies)
    tdetail = ", ".join(f"{t.judge_id} x{t.transport}"
                        for t in tallies if t.transport)
    lines.append(f"{'transport':<33}{lost:<4}{tdetail}"
                 f"{'   ' if tdetail else ''}"
                 f"(excluded from the table — no answer received)")
    if lost and not any(t.n for t in tallies):
        lines.append("")
        lines.append("NOTHING WAS MEASURED. Every item failed in transport, so the "
                     "rates above are empty, not perfect — fix the connection, the "
                     "key or the rate limit and re-run.")
    lines.append("")
    lines.append(LEGEND)

    diverged = [d for t in tallies for d in t.diverged]
    if diverged:
        lines.append("")
        lines.append(f"{len(diverged)} item(s) where the stated score and the payload's "
                     f"own checks disagree:")
        lines.extend(f"  {d}" for d in diverged[:10])
    invalid = [e for t in tallies for e in t.invalid_payloads]
    if invalid:
        lines.append("")
        lines.append(f"{len(invalid)} payload(s) rejected by verify:")
        lines.extend(f"  {e}" for e in invalid[:10])
    lines.extend(_shortfall_lines(shorts))
    return "\n".join(lines)


def _shortfall_lines(shorts: list[Shortfall] | None) -> list[str]:
    """Say which columns are short, wherever a draw is described.

    Printed under the dry run AND under the report, because they are read by
    different people at different times and the one who reads only the table is
    exactly the one who would misread a short column as a full one.
    """
    if not shorts:
        return []
    return ["",
            f"{len(shorts)} band(s) drew fewer items than --limit asked for — read "
            f"those columns as the counts they print, not as full quotas:"] + \
           [f"  {s}" for s in shorts[:10]]


def dry_run_report(plan: list[tuple[Rubric, list[Item]]], *, k: int,
                   have_key: bool, shorts: list[Shortfall] | None = None) -> str:
    """What a live run would cost, with every prompt actually built.

    Building the prompts is the point rather than a formality: `build_request`
    resolves the scoring tail's `{{scope}}`, renders the rubric body and derives
    the json_schema from the declared checks, so a rubric that cannot produce a
    request fails here — free, offline — instead of on a provisioned evaluator.
    """
    lines = [f"{'judge':<33}{'items':<7}{'k':<4}{'calls':<7}{'prompt chars':<14}unit",
             RULE]
    total = 0
    for rubric, items in plan:
        sizes = []
        for it in items:
            req = build_request(rubric, it.transcript, it.scope)
            sizes.append(sum(len(b["text"]) for b in req["system"])
                         + len(req["messages"][0]["content"]))
        calls = len(items) * k
        total += calls
        span = (f"{min(sizes)}-{max(sizes)}" if sizes else "-")
        lines.append(f"{rubric.id:<33}{len(items):<7}{k:<4}{calls:<7}{span:<14}"
                     f"{rubric.unit}")
    lines.append(RULE)
    lines.append(f"{len(plan)} judge(s), {sum(len(i) for _, i in plan)} item(s), "
                 f"k={k} -> {total} Anthropic call(s)")
    lines.append("every prompt built successfully; nothing was sent")
    if k != K:
        lines.append(f"note: production runs k={K} (the deployed Lambda's default); "
                     f"this plan is k={k}")
    else:
        lines.append(f"note: k={K} is what production runs — the deployed Lambda's default")
    if not have_key:
        lines.append("")
        lines.append("dry run is the default because no API key is set. For a live run, "
                     "export one of:")
        lines.extend(f"  {v}" for v in API_KEY_VARS)
    lines.extend(_shortfall_lines(shorts))
    return "\n".join(lines)


def have_api_key(env: dict[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    return any(e.get(v) for v in API_KEY_VARS)


def open_client() -> Any:
    """One client for the whole run, imported at the last possible moment.

    Same reason `judge.run` defers it: every offline command in this package has
    to work with the SDK uninstalled, and a module-level import would end that
    for the CLI as a whole, not just for `score`.
    """
    import anthropic
    return anthropic.Anthropic()


def confirm(calls: int, *, yes: bool, stream: Any = None) -> bool:
    """Gate on the call count before spending it.

    A full sweep is every judge over all 60 samples at k=5 — thousands of Opus
    calls at effort:high — and the failure mode is a bill, not an exception, so
    nothing here defaults to yes. Non-interactive means no one is present to
    answer the prompt, and an unanswered prompt is not consent.

    `stream` defaults to None, not to `sys.stderr`, because a default argument is
    evaluated once at import. Binding the stderr that existed at import time
    while reading `sys.stdin` at call time makes the two halves of this gate
    disagree under any harness that redirects them — the tests that drive the
    paid path do exactly that — and the half that goes missing is the one that
    tells the operator what is about to be spent.
    """
    out = sys.stderr if stream is None else stream
    print(f"this run will make {calls} Anthropic call(s)", file=out)
    if yes:
        return True
    if not sys.stdin.isatty():
        print("refusing: not an interactive terminal. Re-run with --yes to confirm.",
              file=out)
        return False
    return input(f"proceed with {calls} call(s)? [y/N] ").strip().lower() in ("y", "yes")


def prepare_out(path: Path) -> Path:
    """Resolve, create and prove the --out path writable BEFORE anything is spent.

    Creating the parent only after the run means an unwritable --out costs the
    operator the entire run and then throws away every result it bought. The
    directory is made and the file opened for append here, at the top, where the
    only thing a failure costs is a re-run with a better path.

    A file that did not exist and is still empty is removed again: proving a path
    writable must not leave a stray artifact behind when the operator answers no
    at the confirmation.
    """
    p = path.expanduser().resolve()
    existed = p.exists()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8"):
            pass
        if not existed and p.stat().st_size == 0:
            p.unlink()
    except OSError as e:
        raise ValueError(f"--out {p} is not writable ({type(e).__name__}: "
                         f"{e.strerror or e}); refusing to start a paid run whose "
                         f"results would have nowhere to go") from e
    return p


def resolve_rubrics(judge_ids: list[str] | None, *, traced: bool) -> list[Rubric]:
    """Default to the deployable set, so a bare `score` cannot double-count.

    `capability-honesty-traced` and `capability-honesty` are the same defect read
    two ways; scoring both by default would put a judge in the table twice and
    make any suite-level mean meaningless. Name either explicitly to get it.
    """
    if judge_ids:
        return load_rubrics(judge_ids)
    return deployable(load_rubrics(), prefer_traced=traced)


def write_results(path: Path, results: list[ItemResult]) -> None:
    """Per-item JSONL, in the shape `agentcore-judges verify` already reads.

    Same keys, so the output of a scoring run is a valid input to the verifier
    without a conversion step in between. `label` is the one the return boundary
    produced, carried through rather than re-derived here — a second copy of the
    value-to-label mapping is a second thing to get wrong.
    """
    with path.open("w", encoding="utf-8") as fh:
        for r in results:
            v = r.verification
            fh.write(json.dumps({
                "id": r.item.id,
                "judge_id": r.item.judge_id,
                "expected_score": r.item.expected,
                "label": r.label,
                "value": r.score,
                "explanation": r.explanation,
                "transcript": r.item.transcript,
                "samples": r.verdict.samples if r.verdict else [],
                "unanimous": r.verdict.unanimous if r.verdict else None,
                "needs_human_review": (r.verdict.needs_human_review
                                       if r.verdict else None),
                "recomputed": v.recomputed if v else None,
                "diverged": v.diverged if v else None,
                "verify_errors": v.errors if v else [],
                "verify_warnings": v.warnings if v else [],
                "error": r.error,
                # Flagged, not omitted: `verify` reads this file back, and a
                # record with no answer in it is not an invalid payload.
                "transport": r.transport,
            }, default=str) + "\n")

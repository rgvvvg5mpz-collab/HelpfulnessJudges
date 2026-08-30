"""Score a rubric against its labelled testset before it is ever deployed.

`data/testsets/` ships 60 labelled samples per judge and, until this module,
nothing in the folder read them. A team handed this folder standalone could not
answer "did my rubric edit make this judge better or worse?" without provisioning
into a live AX space first — the most expensive available way to discover that a
clause is ambiguous.

This is the only place in the package that calls a model directly, and on a path
whose whole premise is that Arize owns execution that needs justifying. What is
validated here is the rubric and the *rendered template*: the template filled is
byte-for-byte the string `provision` uploads, and it is filled the way AX fills
it — one single-brace variable substituted, everything else already escaped by
`render._escape`. What is NOT validated is Arize itself: sampling, cadence,
write-back, and the adapter's own invocation defaults stay out of reach.

Two numbers come out, and they answer different questions:

  * **label vs expected_score** — is the judge right. Computed identically in the
    sibling deployment folders, which is the point of the shared report format.
  * **payload well-formedness** — did the free-text `explanation` actually carry
    a parseable verdict, or did it silently degrade. Arize constrains the label
    to `classification_choices` and constrains the explanation to nothing at all
    (verify.py), so this is the number only the AX path can produce, and it is
    the failure that lands on a span looking perfectly normal.

A third thing can happen and is deliberately not a number: the call never
returned. A rate limit, a dropped connection or a rejected key says nothing
about the rubric, so transport failures are excluded from `n` and from every
column derived from it, and printed on their own line instead. Folding them in
would report an infrastructure outage as "this judge missed a hard failure",
which is the one wrong answer a measurement tool must never give.

Deliberate deviations from the deployed configuration, both in the direction of
*flattering* the rubric — read them before quoting a score as a prediction:
`EFFORT` and adaptive thinking cannot be requested through AX's evaluator schema
at all (docs/LIMITS.md), and `MAX_TOKENS` is the parent harness's budget rather
than `provision`'s, because 8,000 tokens sized for a no-thinking adapter would
starve a thinking run and truncate it into a missing tool call.
"""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO

from . import SCALE_CHOICES
from .instrument import Turn, render_transcript
from .provision import LlmSettings
from .render import RenderedTemplate, render, unescape
from .spec import ROOT, Rubric, deployable, load_rubrics
from .verify import verify

TESTSET_DIR = ROOT / "data" / "testsets"

BANDS = (0.0, 0.5, 1.0)
BAND_COLUMNS = ((1.0, "pass"), (0.5, "warn"), (0.0, "fail"))
"""Bands in report order, which is not `BANDS` order.

Sampling walks upward so the remainder lands on the hard failures; the table
reads downward, best first. Keeping the two orders separate is cheaper than
reversing one of them at every use and getting it wrong once.
"""

HEADER = (f"{'judge':<33}{'n':<4}{'exact':<6} {'±1 lvl':<6}{'fp0':>6}{'fn0':>6}   "
          f"{'pass':<6}{'warn':<6}{'fail'}")
RULE = "-" * 78
LEGEND = ("fp0 = judge scored 0 where the label was not 0 (false alarm)\n"
          "fn0 = label was 0 and the judge did not catch it (missed bright line)")
TRANSPORT_NOTE = "(excluded from the table — no answer received)"
"""Why the transport count sits outside every column rather than inside `n`.

An unparseable output is a judge failure and belongs in the denominators. A
connection error, a rate limit or a rejected key is not: nothing was asked and
nothing was answered, so charging it to `fn0` states that a judge missed a
bright line when the truth is that no judge ever saw the item.
"""
PAYLOAD_LEGEND = (
    "payload = explanations that parsed as a valid verdict (verify.py). Arize\n"
    "constrains the label to classification_choices and the explanation to nothing,\n"
    "so a judge can score correctly while its structured payload degrades to prose:"
)

TOOL_NAME = "record_evaluation"

OFFLINE_LLM = LlmSettings(ai_integration_id="(unused — direct API, not an AX integration)")
"""Where the judge-model choice comes from.

Only `model_name` is read. Taking it from `LlmSettings` rather than restating a
string means a model change made for the deployment is a model change here too;
a scorer that quietly kept scoring the old model would be worse than no scorer.
`temperature` is deliberately *not* forwarded — Opus 5 rejects sampling
parameters, so the one invocation parameter AX does let us pin is the one this
path cannot mirror.
"""

MAX_TOKENS = 16_000
EFFORT = "high"


class RenderBug(ValueError):
    """A rendered template that cannot be filled the way Arize fills it.

    Raised loudly rather than worked around. A template with no variable scores a
    prompt containing no transcript; a template with two fills both, and neither
    errors on the platform — the judge just quietly scores the wrong text.
    """


@dataclass(frozen=True)
class Sample:
    id: str
    judge_id: str
    expected: float
    turns: list[Turn]
    target_turn: int | None
    trigger_present: bool


@dataclass(frozen=True)
class Choice:
    """AX's two forced tool arguments, as read back off one call.

    `transport` separates "we never got an answer" from "we got an answer and it
    broke the output contract". Both leave `label` empty, and folding them
    together would let a rate limit or a dropped connection be reported as the
    judge missing a bright line — an infrastructure problem printed as a
    measurement. Only the second kind is a judge result.
    """

    label: str | None
    explanation: str | None
    error: str | None = None
    transport: bool = False

    @property
    def score(self) -> float | None:
        return SCALE_CHOICES.get(self.label) if self.label is not None else None


@dataclass
class ItemResult:
    judge_id: str
    sample_id: str
    expected: float
    draw: int
    label: str | None
    score: float | None
    payload_ok: bool
    parse_error: str | None = None
    payload_errors: list[str] = field(default_factory=list)
    explanation: str | None = None
    transport_error: str | None = None

    @property
    def transport(self) -> bool:
        """No answer was received, so this draw measured nothing about the judge."""
        return self.transport_error is not None

    def as_row(self) -> dict[str, Any]:
        return {
            "judge_id": self.judge_id, "sample_id": self.sample_id,
            "expected_score": self.expected, "draw": self.draw,
            "label": self.label, "score": self.score,
            "exact": self.score == self.expected,
            "payload_ok": self.payload_ok, "parse_error": self.parse_error,
            "payload_errors": self.payload_errors, "explanation": self.explanation,
            "transport_error": self.transport_error, "transport": self.transport,
        }


@dataclass
class JudgeScore:
    judge_id: str
    n: int = 0
    exact: int = 0
    near: int = 0
    fp0: int = 0
    fn0: int = 0
    payload_ok: int = 0
    unparseable: int = 0
    transport: int = 0
    band_n: dict[float, int] = field(default_factory=lambda: dict.fromkeys(BANDS, 0))
    band_hits: dict[float, int] = field(default_factory=lambda: dict.fromkeys(BANDS, 0))


def load_testset(judge_id: str, *, path: Path | None = None) -> list[Sample]:
    """Read one judge's labelled samples, turns already in `instrument.Turn` form.

    An empty file is refused rather than returned as an empty list. A judge with
    no samples silently vanishes from the report, and a missing row reads as "not
    run" when it actually means "this folder's data is broken" — the one reading
    of the table that cannot be corrected by looking harder at it.
    """
    path = path or TESTSET_DIR / f"{judge_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no testset for {judge_id} at {path}")
    out: list[Sample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out.append(Sample(
            id=rec["id"],
            judge_id=rec["judge_id"],
            expected=float(rec["expected_score"]),
            turns=[Turn(t["role"], t["text"], list(t.get("tool_trace") or []))
                   for t in rec["turns"]],
            target_turn=rec.get("target_turn"),
            trigger_present=bool(rec.get("trigger_present", False)),
        ))
    if not out:
        raise ValueError(
            f"testset for {judge_id} at {path} is empty. An empty testset is a broken "
            f"folder, not an absent judge — scoring it would drop {judge_id} from the "
            f"report entirely and the gap would read as 'not selected'. Restore the "
            f"file from git, or remove the judge from the run with --judge."
        )
    return out


def band_quota(limit: int) -> dict[float, int]:
    """Split `limit` across the three expected-score bands.

    The remainder goes to the lowest band first. An odd limit therefore buys an
    extra hard-failure sample, which is the right bias: a 1.0-vs-0.5 disagreement
    is about polish, a 0.5-vs-0 disagreement is about whether something broke.
    """
    base, rem = divmod(max(limit, 0), len(BANDS))
    return {b: base + (1 if i < rem else 0) for i, b in enumerate(BANDS)}


def select(samples: list[Sample], limit: int = 6, seed: int = 0,
           judge_id: str | None = None) -> list[Sample]:
    """Draw a band-balanced subset. `limit <= 0` takes everything.

    The RNG is keyed per (seed, judge, band) rather than shared across the three
    draws. A single stream couples the bands: the number of items taken from 0.0
    shifts the randomness the 0.5 and 1.0 draws consume, so raising --limit
    reshuffles every band instead of extending it and two runs stop being
    comparable for a reason that has nothing to do with the judge. Keyed per
    band, `--limit 9` is `--limit 6` plus one more item per band.

    The key format is the one `agentcore-integration.score.select` uses, over the
    same testset files, so the same --seed and --limit draw the same items on
    both paths — cross-path disagreement is only evidence about a platform if
    both paths scored the same items.
    """
    key = judge_id if judge_id is not None else (samples[0].judge_id if samples else "")
    if limit <= 0:
        return sorted(samples, key=lambda s: s.id)
    quota = band_quota(limit)
    drawn: list[Sample] = []
    for b in BANDS:
        pool = sorted((s for s in samples if s.expected == b), key=lambda s: s.id)
        n = min(quota[b], len(pool))
        drawn.extend(random.Random(f"{seed}:{key}:{b}").sample(pool, n))
    return sorted(drawn, key=lambda s: s.id)


def band_shortfall(samples: list[Sample], limit: int) -> dict[float, int]:
    """Bands the data cannot fill, band -> how many samples are missing.

    Under-drawing is the right behaviour — there is nothing else to draw — but it
    must not be silent. A `fail` column printed as `2/4` when --limit 6 asked for
    6 items reads as a six-item column that happened to score badly, and the
    denominator is the only place the difference shows.
    """
    if limit <= 0:
        return {}
    quota = band_quota(limit)
    have = {b: sum(1 for s in samples if s.expected == b) for b in BANDS}
    return {b: quota[b] - have[b] for b in BANDS if have[b] < quota[b]}


def fill(template: RenderedTemplate, transcript: str) -> str:
    """Substitute the one mapped variable, the way AX substitutes it.

    A real `.format` pass over the whole template would be correct — everything
    else was doubled by `render._escape` precisely to survive one — but it is
    fragile against a single missed escape, and a formatter failure here would
    look like a judge failure. Replace the exact token instead, and refuse to
    proceed unless it occurred exactly once.
    """
    token = "{" + template.variable + "}"
    seen = template.template.count(token)
    if seen != 1:
        raise RenderBug(
            f"{template.judge_id}: template contains {seen} occurrences of {token!r}, "
            f"expected exactly 1 — "
            + ("the judge would score a prompt with no transcript in it"
               if seen == 0 else
               "AX fills every occurrence, so the transcript would be duplicated")
        )
    # Un-escape around the substitution, never through it. `.format` collapses
    # the doubled braces the renderer added, but it does that to the template
    # only — the value it interpolates is inserted verbatim. Un-escaping the
    # joined string instead would corrupt any `{{` a transcript happened to
    # contain.
    head, _, tail = template.template.partition(token)
    style = template.brace_style
    return unescape(head, style) + transcript + unescape(tail, style)


def build_prompt(template: RenderedTemplate, sample: Sample) -> str:
    """Render the sample with this folder's own renderer, then fill the template.

    Conversation-unit judges get no `>>> TARGET` marker, matching what
    `instrument.span_attributes` writes to `judge.conversation_transcript`.
    """
    target = sample.target_turn if template.unit == "turn" else None
    return fill(template, render_transcript(sample.turns, target))


def classification_tool(rubric: Rubric) -> dict[str, Any]:
    """The forced tool, shaped like the one AX builds from the evaluator config.

    `label` is an enum over `classification_choices`; `explanation` is a bare
    string with no schema behind it. That asymmetry is not a simplification for
    the offline path — it is exactly what the platform enforces, and reproducing
    it is the only way the payload-degradation number means anything.
    """
    return {
        "name": TOOL_NAME,
        "description": f"Record the {rubric.id} evaluation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "explanation": {"type": "string", "description": "Brief explanation."},
                "label": {"type": "string", "enum": list(SCALE_CHOICES)},
            },
            "required": ["explanation", "label"],
        },
    }


def read_choice(tool_input: Any) -> Choice:
    """Pull the label and explanation out of one tool call.

    A label outside `classification_choices` is a parse failure, not a score of
    zero. Coercing it would invent a verdict the judge never gave and hide the
    output-contract break this scorer exists to surface.
    """
    if not isinstance(tool_input, dict):
        return Choice(None, None,
                      f"tool input is {type(tool_input).__name__}, expected an object")
    explanation = tool_input.get("explanation")
    explanation = explanation if isinstance(explanation, str) else None
    label = tool_input.get("label")
    if not isinstance(label, str):
        return Choice(None, explanation,
                      f"`label` is {label!r}, expected one of {tuple(SCALE_CHOICES)}")
    label = label.strip()
    if label not in SCALE_CHOICES:
        return Choice(None, explanation,
                      f"`label` {label!r} is outside classification_choices "
                      f"{tuple(SCALE_CHOICES)}")
    return Choice(label, explanation)


Caller = Callable[[str, Rubric], Choice]


def call_model(prompt: str, rubric: Rubric, *, client: Any = None) -> Choice:
    """One judge call. Imports the SDK lazily so every offline path works without it.

    Everything the SDK raises here is a transport failure — the call never
    produced an answer, so there is nothing about the judge to grade. That
    includes an auth rejection: it used to abort the whole run, which was
    defensible but threw away every item already scored, and the transport line
    now says the same thing without discarding the results.

    `client` is injectable so the transport path can be exercised offline. It is
    the only branch in this module that cannot be reached without the network.
    """
    import anthropic

    if client is None:
        client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model=OFFLINE_LLM.model_name,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=[classification_tool(rubric)],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as e:
        # Every remaining call will fail the same way, so name the fix once.
        return Choice(None, None,
                      f"{type(e).__name__}: {e} — set ANTHROPIC_API_KEY (or "
                      f"ANTHROPIC_AUTH_TOKEN), or run with --dry-run",
                      transport=True)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        # Rate limits, 5xx, refused connections and timeouts all land here.
        return Choice(None, None, f"{type(e).__name__}: {e}", transport=True)

    block = next((b for b in msg.content if b.type == "tool_use"), None)
    if block is None:
        return Choice(None, None,
                      f"no tool call in the response (stop_reason {msg.stop_reason!r})")
    return read_choice(block.input)


def score_sample(
    rubric: Rubric,
    template: RenderedTemplate,
    sample: Sample,
    *,
    k: int = 1,
    call: Caller = call_model,
) -> list[ItemResult]:
    """Score one sample k times. Each draw is its own row — see `tally`."""
    prompt = build_prompt(template, sample)
    transcript = render_transcript(
        sample.turns, sample.target_turn if template.unit == "turn" else None)
    out: list[ItemResult] = []
    for draw in range(k):
        choice = call(prompt, rubric)
        if choice.transport:
            # No answer arrived, so there is no payload to verify and nothing to
            # grade. Recorded, never verified — running verify on a None
            # explanation would manufacture a payload failure out of a network one.
            out.append(ItemResult(
                judge_id=rubric.id, sample_id=sample.id, expected=sample.expected,
                draw=draw, label=None, score=None, payload_ok=False,
                parse_error=choice.error, transport_error=choice.error,
            ))
            continue
        v = verify(rubric, choice.explanation, choice.label, transcript=transcript)
        out.append(ItemResult(
            judge_id=rubric.id, sample_id=sample.id, expected=sample.expected,
            draw=draw, label=choice.label, score=choice.score,
            payload_ok=v.ok, parse_error=choice.error, payload_errors=v.errors,
            explanation=choice.explanation,
        ))
    return out


def tally(results: Iterable[ItemResult]) -> dict[str, JudgeScore]:
    """Aggregate per judge.

    Every draw counts as its own row. An unparseable draw stays in `n` — dropping
    it would turn a broken output contract into a smaller denominator — and it
    counts against `fn0` when the label was 0, because no 0 reached the platform
    however the output failed. It cannot count against `fp0`: an output that
    produced no verdict never raised a false alarm.

    A TRANSPORT failure is the opposite case and is excluded from everything: no
    answer was received, so the judge neither passed nor failed, and counting it
    as `fn0` would print "this judge missed a hard failure" when what actually
    happened was a rate limit. The judge still gets a row — `setdefault` runs
    first — so a run that was entirely transport shows n=0 rather than
    disappearing and reading as a judge nobody selected.
    """
    out: dict[str, JudgeScore] = {}
    for r in results:
        s = out.setdefault(r.judge_id, JudgeScore(r.judge_id))
        if r.transport:
            s.transport += 1
            continue
        s.n += 1
        s.band_n[r.expected] = s.band_n.get(r.expected, 0) + 1
        if r.payload_ok:
            s.payload_ok += 1
        if r.score is None:
            s.unparseable += 1
            if r.expected == 0.0:
                s.fn0 += 1
            continue
        if r.score == r.expected:
            s.exact += 1
            s.band_hits[r.expected] = s.band_hits.get(r.expected, 0) + 1
        if abs(r.score - r.expected) <= 0.5:
            s.near += 1
        if r.score == 0.0 and r.expected != 0.0:
            s.fp0 += 1
        if r.expected == 0.0 and r.score != 0.0:
            s.fn0 += 1
    return out


def _pct(hit: int, n: int) -> str:
    return "-" if not n else f"{100.0 * hit / n:.1f}%"


def format_report(scores: dict[str, JudgeScore]) -> str:
    """The shared table, then the number only this folder can produce.

    The table is pinned character-for-character to the one the sibling
    deployment folders print. The same rubrics run on three platforms, and a
    difference in the numbers is only evidence *about a platform* if both runs
    were counted and laid out the same way — a column this folder widened on its
    own turns a cross-path diff into a formatting diff.

    Payload well-formedness is therefore a block below the table rather than a
    tenth column: it has no counterpart on the other two paths, because they get
    a schema and this one gets an unconstrained string (verify.py).
    """
    ranked = [scores[jid] for jid in sorted(scores)]
    lines = [HEADER, RULE]
    for s in ranked:
        bands = "".join(
            f"{f'{s.band_hits[b]}/{s.band_n[b]}':<6}" for b, _ in BAND_COLUMNS
        ).rstrip()
        lines.append(f"{s.judge_id:<33}{s.n:<4}{_pct(s.exact, s.n):<6} "
                     f"{_pct(s.near, s.n):<6}{s.fp0:>6}{s.fn0:>6}   {bands}")
    bad = sum(s.unparseable for s in ranked)
    detail = ", ".join(f"{s.judge_id} x{s.unparseable}" for s in ranked if s.unparseable)
    lines.append(f"{'unparseable':<33}{bad:<4}{detail}".rstrip())
    # Transport gets its own line, below the table and outside every column in
    # it. Excluded but never omitted: a call that failed to happen is invisible
    # otherwise, and an invisible exclusion is how a half-completed run comes to
    # be read as a whole one.
    lost = sum(s.transport for s in ranked)
    lost_detail = ", ".join(f"{s.judge_id} x{s.transport}" for s in ranked if s.transport)
    lines.append(f"{'transport':<33}{lost:<4}{lost_detail}"
                 f"{'   ' if lost_detail else ''}{TRANSPORT_NOTE}")
    if lost and not any(s.n for s in ranked):
        lines.append(f"NOTHING WAS MEASURED — all {lost} call(s) failed in transport. "
                     f"Every column above is empty, not perfect.")
    lines += ["", LEGEND, "", PAYLOAD_LEGEND]
    for s in ranked:
        lines.append(f"  {s.judge_id:<31}{_pct(s.payload_ok, s.n):<8}{s.payload_ok}/{s.n}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Plan:
    """One judge's share of a run, drawn but not yet called.

    `short` is carried alongside the samples rather than recomputed at print
    time: the shortfall is a property of the draw, and the draw is the only
    place that knows what the quota asked for.
    """

    rubric: Rubric
    template: RenderedTemplate
    samples: list[Sample]
    short: dict[float, int] = field(default_factory=dict)


def plan_run(
    judge_ids: list[str] | None,
    limit: int,
    seed: int,
) -> list[Plan]:
    """Resolve judges, render templates and draw samples. Pure — calls nothing."""
    rubrics = load_rubrics(judge_ids) if judge_ids else deployable(load_rubrics())
    out: list[Plan] = []
    for r in rubrics:
        samples = load_testset(r.id)
        out.append(Plan(r, render(r), select(samples, limit, seed, judge_id=r.id),
                        band_shortfall(samples, limit)))
    return out


def shortfall_note(plans: list[Plan]) -> str:
    """The bands that could not be filled, or "" when every quota was met.

    Printed by the dry run and again under the report, because the two are read
    by different people at different times and the second one is the one quoted.
    A short band is not an error — there is nothing else to draw — but a `2/4`
    where --limit promised 6 has to be attributable to the data rather than to
    the judge.
    """
    names = dict(BAND_COLUMNS)
    rows = [
        f"  {p.rubric.id:<31}{names[b]:<6}{n} fewer than the quota"
        for p in plans for b, n in sorted(p.short.items(), reverse=True)
    ]
    if not rows:
        return ""
    return ("\n".join(["SHORT BANDS — the testset holds fewer items than --limit asked for, so "
                       "these\ncolumns have smaller denominators than the flag implies:"] + rows))


def credentials_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _k_caveat(k: int) -> str:
    return (
        f"k={k} measures the stability of the RUBRIC, not of the deployed system.\n"
        f"Arize has no repetition primitive (docs/LIMITS.md), so production runs k=1\n"
        f"whatever you set here."
    )


def _call_count(work: list[Plan], k: int) -> str:
    """`judges x items x k`, degrading to a plain total if the bands ran short."""
    calls = sum(len(p.samples) for p in work) * k
    sizes = {len(p.samples) for p in work}
    shape = f"{len(work)} judge(s) x {sizes.pop()} item(s) x k={k} = " if len(sizes) == 1 else ""
    return f"{shape}{calls} call(s) to {OFFLINE_LLM.model_name}"


def _dry_run_report(work: list[Plan], k: int, stream: TextIO) -> None:
    print("DRY RUN — every prompt was built, nothing was called.\n", file=stream)
    print(f"{'judge':30} {'unit':13} {'items':>5}  "
          f"{'prompt chars min/med/max':>26}  template", file=stream)
    print("-" * 96, file=stream)
    for p in work:
        sizes = sorted(len(build_prompt(p.template, s)) for s in p.samples)
        span = f"{sizes[0]:,} / {sizes[len(sizes) // 2]:,} / {sizes[-1]:,}" if sizes else "-"
        print(f"{p.rubric.id:30} {p.rubric.unit:13} {len(p.samples):>5}  {span:>26}  "
              f"{p.template.hash}", file=stream)
    print(f"\nwould issue {_call_count(work, k)}", file=stream)
    if note := shortfall_note(work):
        print(f"\n{note}", file=stream)
    print(f"\n{_k_caveat(k)}", file=stream)
    print(f"\neffort={EFFORT} and adaptive thinking are requested here and CANNOT be requested\n"
          f"through AX's evaluator schema (docs/LIMITS.md), so an offline score is an upper\n"
          f"bound on the deployed one, not a prediction of it.", file=stream)
    if not credentials_present():
        print("\nno ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN in the environment — dry run is "
              "the\ndefault without one. Set ANTHROPIC_API_KEY and re-run to score for real.",
              file=stream)


def confirm(count: str, yes: bool, stream: TextIO) -> bool:
    """Gate a live run behind an explicit count. Never assume a full sweep is wanted."""
    print(f"about to issue {count} at effort {EFFORT}.", file=stream)
    if yes:
        return True
    if not sys.stdin.isatty():
        print("refusing to spend without confirmation on a non-interactive terminal — "
              "pass --yes.", file=stream)
        return False
    return input("proceed? [y/N] ").strip().lower() in ("y", "yes")


def prepare_out(path: Path) -> Path:
    """Resolve `--out`, create its parent and prove it writable. Before any spend.

    Creating the directory was already done up front; proving the FILE can be
    written was not, and a read-only directory or a path under a file still cost
    a whole sweep and then threw every result away at the last line of the run.
    The probe opens the real target rather than a neighbour, because a
    permission that holds for the directory need not hold for an existing file
    in it, and removes it again when it did not already exist so a refused
    confirmation leaves nothing behind.
    """
    out = path.expanduser().resolve()
    existed = out.exists()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8"):
            pass
        if not existed:
            out.unlink()
    except OSError as e:
        raise ValueError(
            f"--out {out} cannot be written ({type(e).__name__}: {e}). Refusing to "
            f"start a paid run whose results would have nowhere to land."
        ) from None
    return out


def run(
    judge_ids: list[str] | None = None,
    *,
    limit: int = 6,
    k: int = 1,
    seed: int = 0,
    dry_run: bool = False,
    out: Path | None = None,
    yes: bool = False,
    stream: TextIO = sys.stdout,
    call: Caller = call_model,
) -> int:
    """Run the scorer. Dry by default whenever there are no credentials to spend.

    `--dry-run` exits 0 — nothing was asked for and nothing was spent. An
    explicit `--yes` with no credentials does NOT: it is an operator asking to
    spend, and answering that request with a silent dry run and a success code
    is how a scheduled job reports "scored" for weeks without ever having
    scored anything.
    """
    if k < 1:
        raise ValueError(f"--k must be at least 1, got {k}")
    if out is not None:
        # First, ahead of the draw and far ahead of the confirmation. An
        # unwritable --out found on the way out costs the whole sweep and then
        # discards every result it paid for.
        out = prepare_out(out)
    work = plan_run(judge_ids, limit, seed)

    if dry_run:
        _dry_run_report(work, k, stream)
        return 0

    if not credentials_present():
        if yes:
            print("--yes asks to spend, but neither ANTHROPIC_API_KEY nor "
                  "ANTHROPIC_AUTH_TOKEN is set,\nso nothing can be scored. Export "
                  "ANTHROPIC_API_KEY, or pass --dry-run to build every\nprompt "
                  "without calling anything.", file=stream)
            return 2
        _dry_run_report(work, k, stream)
        return 0

    if not confirm(_call_count(work, k), yes, stream):
        return 2

    results: list[ItemResult] = []
    for p in work:
        for sample in p.samples:
            results.extend(score_sample(p.rubric, p.template, sample, k=k, call=call))

    print(f"\n{format_report(tally(results))}", file=stream)
    if note := shortfall_note(work):
        print(f"\n{note}", file=stream)
    print(f"\n{_k_caveat(k)}", file=stream)
    if out:
        out.write_text("\n".join(json.dumps(r.as_row()) for r in results) + "\n")
        print(f"\n{len(results)} row(s) -> {out}", file=stream)
    return 0

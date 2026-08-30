"""Offline tests. No Arize account, no network, no SDK required.

Run: .venv/bin/python -m tests.test_suite
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arize_judges import SCALE_CHOICES
from arize_judges.instrument import Turn, render_transcript, span_attributes
from arize_judges.naming import SAFE_EVAL_NAME, to_ax_name, to_judge_id
from arize_judges.provision import LlmSettings, plan, task_plan
from arize_judges.render import render, render_all
from arize_judges.spec import deployable, load_rubrics
from arize_judges.verify import recompute_label, verify

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


RUBRICS = load_rubrics()
DEPLOY = deployable(RUBRICS)

# --- naming: the trap that silently drops columns ---------------------------
check("every ax name passes the SDK eval-name regex",
      all(SAFE_EVAL_NAME.match(to_ax_name(r.id)) for r in RUBRICS))
check("ax names round-trip", all(to_judge_id(to_ax_name(r.id)) == r.id for r in RUBRICS))
check("hyphens are removed", "-" not in to_ax_name("capability-honesty-traced"))

# --- variant mutual exclusion ----------------------------------------------
ids = {r.id for r in DEPLOY}
check("variant excluded by default", "capability-honesty-traced" not in ids)
check("base judge kept", "capability-honesty" in ids)
check("traced variant swaps in",
      "capability-honesty-traced" in {r.id for r in deployable(RUBRICS, prefer_traced=True)})
check("never both at once",
      not ({"capability-honesty", "capability-honesty-traced"} <=
           {r.id for r in deployable(RUBRICS, prefer_traced=True)}))

# --- rendering --------------------------------------------------------------
for style in ("single", "double"):
    ts = render_all(DEPLOY, style)
    check(f"{style}: all judges render", len(ts) == len(DEPLOY))
    check(f"{style}: templates are deterministic",
          [t.hash for t in ts] == [t.hash for t in render_all(DEPLOY, style)])
    check(f"{style}: each template carries exactly one variable",
          all(t.template.count("{" if style == "single" else "{{") >= 1 for t in ts))
    check(f"{style}: no unrendered scope placeholder",
          all("{{scope}}" not in t.template for t in ts))

t_turn = render([r for r in DEPLOY if r.unit == "turn"][0])
t_conv = render([r for r in DEPLOY if r.unit == "conversation"][0])
check("turn judges read judge.transcript", t_turn.variable == "transcript")
check("conversation judges read judge.conversation_transcript",
      t_conv.variable == "conversation_transcript")
check("preamble survives byte-identical into the template",
      "The scale: 0, 0.5, 1.0" in t_turn.template)
check("tail instructs JSON-in-explanation",
      "MUST be exactly one JSON object" in t_turn.template)

# --- provisioning plan ------------------------------------------------------
p = plan("SPACE", LlmSettings(ai_integration_id="INT"))
check("plan covers every deployable judge", len(p) == len(DEPLOY))
check("classification_choices are the numerals",
      all(i["template_config"]["classification_choices"] == SCALE_CHOICES for i in p),
      "must stay 1.0/0.5/0 so the preamble needs no edit")
check("include_explanations is on everywhere",
      all(i["template_config"]["include_explanations"] for i in p),
      "without it the forced tool has no explanation parameter at all")
check("every evaluator is SPAN scope",
      all(i["template_config"]["data_granularity"] == "SPAN" for i in p))
check("temperature is explicit",
      all("temperature" in i["template_config"]["llm_config"]["invocation_parameters"] for i in p),
      "there is no documented server default")

# --- tasks refuse to run unpinned ------------------------------------------
try:
    task_plan("PROJ", {"t": {"sampling_rate": 1.0, "judges": ["actionability"]}},
              lock={"judges": {"actionability": {"evaluator_id": "e", "evaluator_version_id": None,
                                                 "variable": "transcript"}}})
    check("unpinned evaluator version is refused", False, "no error raised")
except ValueError as e:
    check("unpinned evaluator version is refused", "always latest" in str(e).lower()
          or "pinned" in str(e).lower())

# --- verifier ---------------------------------------------------------------
sd = [r for r in RUBRICS if r.id == "signal-density"][0]


def payload(**over):
    base = {"v": 1, "j": "signal-density", "trig": True, "inj": False, "bl": None,
            "chk": [{"id": i, "ok": True, "sp": "brown fox"} for i in sd.check_ids],
            "ev": ["brown fox"], "why": "ok", "conf": "high",
            "x": {"removable_fraction": "none"}}
    base.update(over)
    return json.dumps(base)


check("valid payload passes", verify(sd, payload(), "1.0").ok)
check("prose is rejected", not verify(sd, "It was fine.", "1.0").ok)
check("missing checks rejected",
      not verify(sd, payload(chk=[{"id": "C1", "ok": True, "sp": ""}]), "1.0").ok)
check("non-boolean verdict rejected",
      not verify(sd, payload(chk=[{"id": i, "ok": "yes", "sp": ""} for i in sd.check_ids]), "1.0").ok)
check("bad enum rejected", not verify(sd, payload(x={"removable_fraction": "lots"}), "1.0").ok)
check("label 0 without a bright line rejected", not verify(sd, payload(), "0").ok)
check("bright line with label 1.0 rejected", not verify(sd, payload(bl="Mostly filler"), "1.0").ok)
check("forbidden key `label` rejected", not verify(sd, payload(label="x"), "1.0").ok)

failing = json.loads(payload()); failing["chk"][0]["ok"] = False
v = verify(sd, json.dumps(failing), "1.0")
check("label divergence detected", v.label_diverged and v.recomputed_label == "0.5")
check("recompute: bright line wins", recompute_label({"bl": "x", "chk": [{"ok": True}]}) == "0")
check("recompute: all pass -> 1.0", recompute_label({"bl": None, "chk": [{"ok": True}]}) == "1.0")
check("recompute: any fail -> 0.5", recompute_label({"bl": None, "chk": [{"ok": False}]}) == "0.5")

check("evidence spans verified against transcript",
      any("not found in transcript" in w
          for w in verify(sd, payload(), "1.0", transcript="unrelated text").warnings))
check("a stated absence is not mistaken for a bad quote",
      not any("not found in transcript" in w for w in verify(
          sd, payload(ev=[], chk=[{"id": i, "ok": True, "sp": "no padding present"}
                                  for i in sd.check_ids]),
          "1.0", transcript="unrelated text").warnings),
      "short absence statements must not be flagged as confabulated quotes")
check("evidence spans accepted when present",
      not any("not found in transcript" in w
              for w in verify(sd, payload(), "1.0", transcript="a brown fox ran").warnings))

# --- instrumentation --------------------------------------------------------
turns = [Turn("customer", "hi"), Turn("assistant", "hello", ["a() -> 1"]),
         Turn("customer", "more"), Turn("assistant", "sure")]
tx = render_transcript(turns, 1)
check("transcript marks the target turn", ">>> TARGET" in tx and tx.count(">>> TARGET") == 1)
check("transcript opens with the marker the rubrics name",
      tx.startswith("[BEGIN TRANSCRIPT]"))
check("assistant turn with no calls still gets the sentinel",
      "[tool trace: none recorded]" in tx)
check("conversation render marks no turn", ">>> TARGET" not in render_transcript(turns, None))
attrs = span_attributes(turns, 3, "c1", is_final_turn=True)
check("final turn carries the conversation transcript",
      "judge.conversation_transcript" in attrs)
check("renderer version is recorded", "judge.renderer_version" in attrs)

# --- offline scorer ---------------------------------------------------------
# Everything here is offline: no key, no network, no anthropic SDK. The model
# call is the only part that is stubbed; the prompt build, the parse and the
# metric math are the real functions.
from arize_judges.render import RenderedTemplate  # noqa: E402
from arize_judges import score as score_mod  # noqa: E402
from arize_judges.score import (  # noqa: E402
    BANDS, TRANSPORT_NOTE, Choice, ItemResult, Plan, RenderBug, band_quota,
    band_shortfall, build_prompt, call_model, confirm, fill, format_report,
    load_testset, prepare_out, read_choice, run, score_sample, select,
    shortfall_note, tally,
)

acty = load_testset("actionability")
check("testset loads 60 samples", len(acty) == 60)
check("testset is 20/20/20 by construction",
      [sum(1 for s in acty if s.expected == b) for b in BANDS] == [20, 20, 20])

check("limit 6 splits evenly across the bands", band_quota(6) == {0.0: 2, 0.5: 2, 1.0: 2})
check("an odd limit gives the remainder to the lowest band first",
      band_quota(7) == {0.0: 3, 0.5: 2, 1.0: 2} and band_quota(8) == {0.0: 3, 0.5: 3, 1.0: 2},
      "a 0.5-vs-0 disagreement matters more than a 1.0-vs-0.5 one")

sel = select(acty, 6, seed=0)
check("sampling honours the bands",
      [sum(1 for s in sel if s.expected == b) for b in BANDS] == [2, 2, 2])
check("same seed draws the same items",
      [s.id for s in sel] == [s.id for s in select(acty, 6, seed=0)])
check("a different seed draws different items",
      [s.id for s in sel] != [s.id for s in select(acty, 6, seed=7)])
check("limit 0 means all 60", len(select(acty, 0, seed=0)) == 60)
check("limit above a band's supply takes what exists", len(select(acty, 90, seed=0)) == 60)
check("raising --limit extends each band instead of reshuffling it",
      set(s.id for s in sel) < set(s.id for s in select(acty, 9, seed=0)),
      "the RNG is keyed per (seed, judge, band); one shared stream would let the size "
      "of the 0.0 draw move the 0.5 and 1.0 draws")
check("the draw is keyed per judge, not per run",
      [s.id for s in select(acty, 6, seed=0, judge_id="some-other-judge")]
      != [s.id for s in sel],
      "one judge's draw must not depend on which judges ran alongside it")

# A short band under-draws — correct, there is nothing else to draw — but it must
# never be silent: a 1/2 column where --limit promised 2 per band has to be
# attributable to the data rather than to the judge.
short_set = [s for s in acty if s.expected == 1.0][:3] + \
            [s for s in acty if s.expected == 0.5][:1] + \
            [s for s in acty if s.expected == 0.0][:1]
check("a band with fewer items than the quota is reported as short",
      band_shortfall(short_set, 6) == {0.5: 1, 0.0: 1})
check("a fully-supplied draw reports no shortfall", band_shortfall(acty, 6) == {})
check("limit 0 cannot be short — it asks for everything there is",
      band_shortfall(short_set, 0) == {})
note = shortfall_note([Plan(RUBRICS[0], render(RUBRICS[0]), short_set,
                            band_shortfall(short_set, 6))])
check("the shortfall note names the judge, the band and the gap",
      "SHORT BANDS" in note and "fail" in note and "warn" in note
      and "1 fewer than the quota" in note,
      "a 1/1 fail column must not be read as the 2/2 that --limit 6 implies")
check("nothing is printed when every quota was met",
      shortfall_note([Plan(RUBRICS[0], render(RUBRICS[0]), acty, {})]) == "")

# --- prompt construction ----------------------------------------------------
for rb in RUBRICS:
    tpl = render(rb)
    sample = select(load_testset(rb.id), 3, seed=0)[0]
    prompt = build_prompt(tpl, sample)
    token = "{" + tpl.variable + "}"
    tx_ = render_transcript(sample.turns,
                            sample.target_turn if tpl.unit == "turn" else None)
    check(f"{rb.id}: prompt builds, transcript substituted verbatim",
          token not in prompt and tx_ in prompt,
          "the mapped variable must be gone and the transcript present unaltered")
    check(f"{rb.id}: filled prompt is byte-identical to AX's own .format",
          prompt == tpl.template.format(**{tpl.variable: tx_}),
          "AX substitutes server-side with .format, which also collapses the doubled "
          "braces the renderer added. Scoring the template without collapsing them "
          "grades a prompt AX will never send — the rubrics carry `{}` in their JSON "
          "examples, so the two differ in practice.")

t_actionability = render([r for r in DEPLOY if r.id == "actionability"][0])
conv_rubric = [r for r in DEPLOY if r.unit == "conversation"][0]
conv_prompt = build_prompt(render(conv_rubric), select(load_testset(conv_rubric.id), 3)[0])
check("conversation judges get an unmarked transcript", ">>> TARGET" not in conv_prompt,
      "matches what span_attributes writes to judge.conversation_transcript")
check("turn judges get the target marked",
      ">>> TARGET" in build_prompt(t_actionability, sel[0]))

for bad, why in ((0, "no variable"), (2, "duplicated variable")):
    broken = RenderedTemplate(judge_id="x", unit="turn", variable="transcript",
                              template="{transcript}" * bad)
    try:
        fill(broken, "T")
        check(f"a template with a {why} is refused", False, "no error raised")
    except RenderBug as e:
        check(f"a template with a {why} is refused", "expected exactly 1" in str(e))

# --- the AX output contract: label enum + unconstrained explanation ---------
check("a well-formed tool call parses",
      read_choice({"label": "0.5", "explanation": "{}"}) == Choice("0.5", "{}"))
check("a parsed label maps to its score", read_choice({"label": "0", "explanation": ""}).score == 0.0)
check("a missing label is unparseable", read_choice({"explanation": "{}"}).label is None)
check("prose instead of a tool call is unparseable",
      read_choice("It was fine.").label is None)
oor = read_choice({"label": "0.75", "explanation": "{}"})
check("a label outside classification_choices is unparseable, not coerced",
      oor.label is None and oor.score is None and "classification_choices" in oor.error)
check("the explanation survives an unparseable label",
      oor.explanation == "{}", "the payload is still worth verifying")

# --- metric math ------------------------------------------------------------
def item(expected, score, payload_ok=True):
    label = None if score is None else {1.0: "1.0", 0.5: "0.5", 0.0: "0"}[score]
    return ItemResult(judge_id="j", sample_id=f"s{expected}-{score}", expected=expected,
                      draw=0, label=label, score=score, payload_ok=payload_ok)


m = tally([
    item(1.0, 1.0), item(1.0, 0.5), item(0.5, 0.5), item(0.5, 0.0),
    item(0.0, 0.0), item(0.0, 0.5, payload_ok=False),
])
check("exact counts only exact agreement", m["j"].exact == 3)
check("±1 lvl counts everything within half a level", m["j"].near == 6)
check("fp0 fires on a 0 where the label was not 0", m["j"].fp0 == 1)
check("fn0 fires on a missed bright line", m["j"].fn0 == 1)
check("bands are reported as hits/total",
      (m["j"].band_hits[1.0], m["j"].band_hits[0.5], m["j"].band_hits[0.0]) == (1, 1, 1))
check("payload well-formedness is counted apart from the label", m["j"].payload_ok == 5)

edge = tally([item(0.0, 0.0), item(0.5, 0.0), item(1.0, 0.0)])
check("fp0 ignores the correctly-caught 0", edge["j"].fp0 == 2 and edge["j"].fn0 == 0)
check("a 1.0 scored 0 is outside ±1 lvl", edge["j"].near == 2)

unp = tally([item(0.0, None, payload_ok=False), item(1.0, None, payload_ok=False)])
check("unparseable draws stay in n", unp["j"].n == 2 and unp["j"].unparseable == 2,
      "an unparseable output is a real failure of the output contract, not a dropped row")
check("unparseable draws never count as exact or ±1 lvl",
      unp["j"].exact == 0 and unp["j"].near == 0)
check("an unparseable output on a 0 is still a missed bright line",
      unp["j"].fn0 == 1 and unp["j"].fp0 == 0,
      "no 0 reached the platform, however the output failed")

rep = format_report(m)
rep_lines = rep.splitlines()
check("report header is the pinned cross-folder layout",
      rep_lines[0] == "judge                            n   exact  ±1 lvl   fp0   fn0   "
                      "pass  warn  fail",
      "a column widened here turns a cross-path diff into a formatting diff")
check("a judge row lines up under the header",
      rep_lines[2] == "j                                6   50.0%  100.0%     1     1   "
                      "1/2   1/2   1/2")
check("report legend matches the parent's wording",
      "fp0 = judge scored 0 where the label was not 0 (false alarm)" in rep
      and "fn0 = label was 0 and the judge did not catch it (missed bright line)" in rep)
check("unparseable gets its own row even at zero",
      rep_lines[3] == f"{'unparseable':<33}0",
      "the row must be present at 0 so its absence is never mistaken for a clean run")
check("unparseable names the judges it hit",
      "unparseable" in (rr := format_report(unp)) and "j x2" in rr)
check("payload well-formedness is reported apart from the shared table",
      "payload = explanations that parsed as a valid verdict (verify.py)" in rep
      and "  j                              83.3%   5/6" in rep,
      "it has no counterpart on the other two paths, which get a schema")
check("the transport row prints even at zero",
      rep_lines[4] == f"{'transport':<33}0   {TRANSPORT_NOTE}",
      "an exclusion nobody can see is how a half-completed run gets read as a whole one")

# --- transport failures are not judge failures ------------------------------
# A rate limit, a dropped connection or a rejected key means no answer arrived.
# Counting one as fn0 would print "this judge missed a hard failure" about an
# item the judge never saw. The stub below is the only way to reach `call_model`'s
# error branches offline: the suite runs with the anthropic SDK uninstalled.
import types  # noqa: E402


def _fake_anthropic():
    """The SDK's exception hierarchy, in the shape `call_model` catches it."""
    m = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class AuthenticationError(APIStatusError):
        pass

    def _no_client(*a, **kw):
        raise AssertionError("call_model must use the injected client, not build one")

    m.APIError, m.APIConnectionError, m.APITimeoutError = APIError, APIConnectionError, APITimeoutError
    m.APIStatusError, m.RateLimitError, m.AuthenticationError = (
        APIStatusError, RateLimitError, AuthenticationError)
    m.Anthropic = _no_client
    return m


class _DeadClient:
    """A client whose every request dies before an answer exists."""

    def __init__(self, exc_name: str, msg: str):
        self.exc_name, self.msg = exc_name, msg
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.calls += 1
        raise getattr(sys.modules["anthropic"], self.exc_name)(self.msg)


_real_anthropic = sys.modules.get("anthropic")
sys.modules["anthropic"] = _fake_anthropic()
try:
    rb_a = [r for r in DEPLOY if r.id == "actionability"][0]
    for exc, why in (("APIConnectionError", "a dropped connection"),
                     ("RateLimitError", "a rate limit"),
                     ("APITimeoutError", "a timeout"),
                     ("AuthenticationError", "a rejected key")):
        c = call_model("prompt", rb_a, client=_DeadClient(exc, "boom"))
        check(f"{why} is a transport failure, not an unparseable answer",
              c.transport and c.label is None and exc in (c.error or ""))
    auth = call_model("prompt", rb_a, client=_DeadClient("AuthenticationError", "bad key"))
    check("a rejected key names the variable to set rather than aborting the run",
          "ANTHROPIC_API_KEY" in (auth.error or "") and auth.transport,
          "aborting threw away every item already scored")

    dead = _DeadClient("APIConnectionError", "connection reset by peer")
    zero_label = [s for s in acty if s.expected == 0.0][0]
    t_rows = score_sample(rb_a, render(rb_a), zero_label, k=2,
                          call=lambda p, r: call_model(p, r, client=dead))
    check("a transport failure is recorded per draw, never raised",
          len(t_rows) == 2 and dead.calls == 2 and all(r.transport for r in t_rows))
    check("a transport row is not verified as a payload",
          all(r.payload_ok is False and r.payload_errors == [] for r in t_rows),
          "verifying a None explanation would manufacture a payload failure out of a "
          "network one")
    check("the JSONL row says the answer never arrived",
          t_rows[0].as_row()["transport"] is True
          and "APIConnectionError" in t_rows[0].as_row()["transport_error"])

    tt = tally(t_rows)["actionability"]
    check("a transport failure is excluded from every quality metric",
          (tt.n, tt.exact, tt.near, tt.fp0, tt.fn0, tt.unparseable) == (0, 0, 0, 0, 0, 0),
          "the label was 0 — counting it fn0 would report an outage as a missed bright line")
    check("a transport failure is excluded from the band denominators",
          all(v == 0 for v in tt.band_n.values()))
    check("a transport failure is counted on its own line", tt.transport == 2)

    t_rep = format_report(tally(t_rows))
    check("the transport line names the judge and the count",
          f"{'transport':<33}2   actionability x2   {TRANSPORT_NOTE}" in t_rep)
    check("an all-transport run says plainly that nothing was measured",
          "NOTHING WAS MEASURED — all 2 call(s) failed in transport." in t_rep,
          "an empty table must not read as a perfect score")
    check("an all-transport run shows no percentage it did not earn",
          f"{'actionability':<33}0   -      -" in t_rep)

    mixed = tally(t_rows + [ItemResult(judge_id="actionability", sample_id="s", expected=0.0,
                                       draw=0, label="0", score=0.0, payload_ok=True)])
    check("a partly-transport run still scores the answers it did get",
          mixed["actionability"].n == 1 and mixed["actionability"].exact == 1
          and mixed["actionability"].transport == 2)
    check("a partly-transport run does not claim the lost calls",
          "NOTHING WAS MEASURED" not in format_report(mixed))
finally:
    if _real_anthropic is None:
        sys.modules.pop("anthropic", None)
    else:
        sys.modules["anthropic"] = _real_anthropic

# --- cost safety ------------------------------------------------------------
import io  # noqa: E402

buf = io.StringIO()
rc = run(["actionability"], limit=3, dry_run=True, stream=buf)
check("dry run exits 0 with no key and no SDK", rc == 0)
check("dry run reports the call count it would spend",
      "1 judge(s) x 3 item(s) x k=1 = 3 call(s)" in buf.getvalue())
check("dry run says what k means on this path",
      "not of the deployed system" in buf.getvalue(),
      "a local k=5 must never be read as something Arize does")

buf2 = io.StringIO()


def refuse(prompt, rubric):
    raise AssertionError("the model must not be called from a dry run")


check("a stubbed run never calls the model",
      run(["actionability"], limit=3, dry_run=True, stream=buf2, call=refuse) == 0)

half = payload(chk=[{"id": i, "ok": False, "sp": "brown fox"} for i in sd.check_ids])
scored = tally([
    r
    for s in select(load_testset(sd.id), 3, seed=1)
    for r in score_sample(sd, render(sd), s, call=lambda prompt, rubric: Choice("0.5", half))
])
check("a scored run threads build -> parse -> verify end to end",
      scored[sd.id].n == 3 and scored[sd.id].payload_ok == 3,
      "the payload column is computed by verify.py over the free-text explanation")
degraded = tally([
    r
    for s in select(load_testset(sd.id), 3, seed=1)
    for r in score_sample(sd, render(sd), s,
                          call=lambda prompt, rubric: Choice("0.5", "It was fine."))
])
check("a prose explanation scores the label but fails the payload column",
      degraded[sd.id].n == 3 and degraded[sd.id].payload_ok == 0,
      "this is the silent degradation the AX path cannot see and this column can")


class _NoTty:
    """A non-interactive stdin. Patched in rather than probed, so this check
    behaves the same run from a terminal as it does run from CI."""

    def isatty(self):
        return False


_stdin, sys.stdin = sys.stdin, _NoTty()
_key = __import__("os").environ.get("ANTHROPIC_API_KEY")
try:
    buf3 = io.StringIO()
    check("non-interactive without --yes refuses to spend",
          confirm("60 call(s)", False, buf3) is False and "--yes" in buf3.getvalue())
    buf4 = io.StringIO()
    check("--yes skips the prompt but still prints the exact call count",
          confirm("60 call(s)", True, buf4) and "60 call(s)" in buf4.getvalue())

    __import__("os").environ["ANTHROPIC_API_KEY"] = "not-a-real-key"
    buf5 = io.StringIO()
    check("a live run with a key but no --yes exits 2 and calls nothing",
          run(["actionability"], limit=3, stream=buf5, call=refuse) == 2
          and "--yes" in buf5.getvalue())

    import tempfile  # noqa: E402
    nested = Path(tempfile.mkdtemp()) / "runs" / "base.jsonl"
    buf6 = io.StringIO()
    check("an --out directory is created before the spend, not after it",
          run(["actionability"], limit=3, out=nested, stream=buf6, call=refuse) == 2
          and nested.parent.is_dir(),
          "a missing parent found on the way out costs the whole sweep")
finally:
    sys.stdin = _stdin
    if _key is None:
        __import__("os").environ.pop("ANTHROPIC_API_KEY", None)
    else:
        __import__("os").environ["ANTHROPIC_API_KEY"] = _key

# --- --out is proved writable before anything is spent ----------------------
import os  # noqa: E402
import tempfile  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
(_tmp / "not-a-directory").write_text("")
unwritable = _tmp / "not-a-directory" / "runs" / "base.jsonl"
try:
    run(["actionability"], limit=3, out=unwritable, yes=True, stream=io.StringIO(),
        call=refuse)
    check("an unwritable --out is refused before the run, not after it", False,
          "no error raised")
except ValueError as e:
    check("an unwritable --out is refused before the run, not after it",
          "cannot be written" in str(e) and "base.jsonl" in str(e),
          "the operator pays for a full sweep and then loses every result")

fresh = _tmp / "deep" / "nested" / "base.jsonl"
resolved = prepare_out(fresh)
check("--out creates its parent up front", resolved.parent.is_dir())
check("--out is resolved to an absolute path", resolved.is_absolute())
check("probing --out leaves no file behind", not resolved.exists(),
      "a refused confirmation must not litter an empty results file")
existing = _tmp / "deep" / "keep.jsonl"
existing.write_text("prior\n")
prepare_out(existing)
check("probing an existing --out does not truncate it", existing.read_text() == "prior\n")

# --- an explicit --yes with no key must fail loudly -------------------------
# `--yes` is an operator asking to spend. Answering that with a silent dry run
# and exit 0 is how a scheduled job reports "scored" for weeks having scored
# nothing.
_env = {v: os.environ.pop(v, None) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
try:
    buf7 = io.StringIO()
    rc7 = run(["actionability"], limit=3, yes=True, stream=buf7, call=refuse)
    check("--yes with no credentials exits non-zero", rc7 == 2)
    check("--yes with no credentials names the variable to set",
          "ANTHROPIC_API_KEY" in buf7.getvalue()
          and "DRY RUN" not in buf7.getvalue(),
          "a no-op that exits 0 is indistinguishable from a successful run")
    buf8 = io.StringIO()
    check("--dry-run with no credentials still exits 0",
          run(["actionability"], limit=3, dry_run=True, yes=True, stream=buf8,
              call=refuse) == 0
          and "DRY RUN" in buf8.getvalue(),
          "nothing was asked for and nothing was spent")
finally:
    for k_, v_ in _env.items():
        if v_ is not None:
            os.environ[k_] = v_

# --- a short band is visible, and an empty testset is an error --------------
empty = _tmp / "empty.jsonl"
empty.write_text("")
try:
    load_testset("actionability", path=empty)
    check("an empty testset is an error, not an absent judge", False, "no error raised")
except ValueError as e:
    check("an empty testset is an error, not an absent judge",
          "is empty" in str(e) and "broken folder" in str(e),
          "returning [] drops the judge from the report, which reads as 'not run'")

short_dir = _tmp / "short"
short_dir.mkdir()
_lines = (Path(__file__).resolve().parent.parent
          / "data" / "testsets" / "actionability.jsonl").read_text().splitlines()
_by_band = {b: [ln for ln in _lines
                if json.loads(ln)["expected_score"] == b] for b in BANDS}
(short_dir / "actionability.jsonl").write_text(
    "\n".join(_by_band[1.0][:3] + _by_band[0.5][:1] + _by_band[0.0][:1]) + "\n")

_real_dir, score_mod.TESTSET_DIR = score_mod.TESTSET_DIR, short_dir
_key2 = os.environ.get("ANTHROPIC_API_KEY")
try:
    buf9 = io.StringIO()
    run(["actionability"], limit=6, dry_run=True, stream=buf9)
    check("the dry run says which bands came up short",
          "SHORT BANDS" in buf9.getvalue()
          and "1 fewer than the quota" in buf9.getvalue(),
          "the operator must see the shortfall before agreeing to a call count")

    os.environ["ANTHROPIC_API_KEY"] = "not-a-real-key"
    buf10 = io.StringIO()
    rc10 = run(["actionability"], limit=6, yes=True, stream=buf10,
               call=lambda p, r: Choice("1.0", "It was fine."))
    check("the report repeats the shortfall under the table",
          rc10 == 0 and "SHORT BANDS" in buf10.getvalue(),
          "the table is what gets quoted; a 1/1 column must carry its own caveat")
    check("a short draw scores only what it drew",
          f"{'actionability':<33}4" in buf10.getvalue(),
          "4 items, not the 6 --limit asked for")
finally:
    score_mod.TESTSET_DIR = _real_dir
    if _key2 is None:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = _key2

# --- CLI entry point --------------------------------------------------------
# Regression: the shim used to run under the SYSTEM interpreter, which has no
# PyYAML, so every documented command died on import. The venv check compares
# sys.prefix, NOT sys.executable — `.venv/bin/python` is a symlink to the system
# interpreter, so resolving both paths makes them compare equal and the re-exec
# silently never happens.
import subprocess

SHIM = Path(__file__).resolve().parent.parent / "bin" / "arize-judges"
if SHIM.exists():
    env = {k: v for k, v in __import__("os").environ.items() if k != "ARIZE_JUDGES_REEXEC"}
    r = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "list"],
                       capture_output=True, text=True, env=env)
    check("shim runs under a bare system interpreter", r.returncode == 0,
          (r.stderr or r.stdout)[-160:])
    check("shim output is the suite listing", "deploy" in r.stdout, r.stdout[:120])
    r2 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "tasks", "--project", "P"],
                        capture_output=True, text=True, env=env)
    check("operator error exits 2 with no traceback",
          r2.returncode == 2 and "Traceback" not in r2.stderr, r2.stderr[-160:])

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

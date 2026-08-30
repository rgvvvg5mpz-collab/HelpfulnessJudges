"""Offline tests. No AWS account, no credentials, no network.

Run: .venv/bin/python -m tests.test_suite
Tests marked [boto3] are skipped when boto3 is absent.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agentcore_judges.boundary as boundary_mod
import agentcore_judges.handler as handler_mod
import agentcore_judges.score as score_mod
from agentcore_judges import (BANDS, LABEL_OF, LABELS, LEVELS,
                              MAX_EVALUATORS_PER_CONFIG, RESULT_FIELDS, SCALE,
                              UNIT_TO_LEVEL)
from agentcore_judges.boundary import agentcore_result
from agentcore_judges.cli import main as cli_main
from agentcore_judges.handler import SCOPE, _compact
from agentcore_judges.judge import AllSamplesFailed, Verdict, build_request
from agentcore_judges.naming import EVALUATOR_NAME, to_evaluator_name, to_judge_id
from agentcore_judges.provision import LambdaTarget, TIERS, online_config_payload, plan
from agentcore_judges.score import (HEADER, LEGEND, Item, ItemResult, Tally, band_sizes,
                                    confirm, dry_run_report, format_report, grade,
                                    is_transport_error, level_of, load_testset,
                                    prepare_out, resolve_rubrics, run_item, select,
                                    shortfalls, tally, write_results)
from agentcore_judges.spans import Conversation, Turn, from_session_spans, target_index
from agentcore_judges.spec import deployable, load_rubrics, output_schema
import agentcore_judges.verify as verify_mod
from agentcore_judges.verify import recompute_label, verify

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


RUBRICS = load_rubrics()
RUBRICS_BY_ID = {r.id: r for r in RUBRICS}
DEPLOY = deployable(RUBRICS)

# --- naming ----------------------------------------------------------------
check("evaluator names match AgentCore's pattern",
      all(EVALUATOR_NAME.fullmatch(to_evaluator_name(r.id)) for r in RUBRICS))
check("names round-trip", all(to_judge_id(to_evaluator_name(r.id)) == r.id for r in RUBRICS))
check("hyphenated names are rejected outright",
      not EVALUATOR_NAME.fullmatch("has-hyphens"))

# --- levels ----------------------------------------------------------------
check("every unit maps to a real AgentCore level",
      all(v in LEVELS for v in UNIT_TO_LEVEL.values()))
levels = [UNIT_TO_LEVEL[r.unit] for r in DEPLOY]
check("8 TRACE + 2 SESSION", levels.count("TRACE") == 8 and levels.count("SESSION") == 2,
      str(levels))
check("TOOL_CALL is deliberately unused", "TOOL_CALL" not in levels,
      "capability-honesty-traced runs at TRACE: TOOL_CALL sees only preceding calls")

# --- variant exclusion + the evaluator cap ---------------------------------
check("variant excluded", "capability-honesty-traced" not in {r.id for r in DEPLOY})
check("suite fits the per-config cap",
      all(len(t["judges"]) <= MAX_EVALUATORS_PER_CONFIG for t in TIERS.values()))
check("tiers partition the suite exactly",
      sorted(j for t in TIERS.values() for j in t["judges"]) == sorted(r.id for r in DEPLOY),
      "every deployable judge in exactly one tier")

# --- Anthropic request -----------------------------------------------------
sd = [r for r in RUBRICS if r.id == "signal-density"][0]
req = build_request(sd, "[BEGIN TRANSCRIPT]\nx", "the assistant turn marked `>>> TARGET`")
check("json_schema output is requested", req["output_config"]["format"]["type"] == "json_schema")
check("effort survives", req["output_config"]["effort"] == "high")
check("cache_control on the shared prefix", "cache_control" in req["system"][1])
check("preamble is first (stable cache prefix)", "The scale: 0, 0.5, 1.0" in req["system"][0]["text"])
check("scope placeholder is resolved", "{{scope}}" not in req["messages"][0]["content"])

sch = output_schema(sd)
check("schema pins the check count",
      sch["properties"]["checks"]["minItems"] == len(sd.check_ids) ==
      sch["properties"]["checks"]["maxItems"])
check("schema pins the three levels", sch["properties"]["score"]["enum"] == [0, 0.5, 1.0])
check("schema carries per-judge extras", "removable_fraction" in sch["properties"])
check("schema forbids extra keys", sch["additionalProperties"] is False)

# --- span reconstruction ---------------------------------------------------
spans = [
    {"traceId": "t1", "startTimeUnixNano": 1, "attributes": {
        "openinference.span.kind": "LLM", "input.value": "hi", "output.value": "hello"}},
    {"traceId": "t1", "startTimeUnixNano": 2, "attributes": {
        "openinference.span.kind": "TOOL", "tool.name": "get_balance",
        "tool.parameters": "acct=1", "output.value": "$5"}},
    {"traceId": "t2", "startTimeUnixNano": 3, "attributes": {
        "openinference.span.kind": "TOOL", "tool.name": "get_pending", "tool.parameters": "2026"},
     "status": {"code": "ERROR", "message": "timeout"}},
    {"traceId": "t2", "startTimeUnixNano": 4, "attributes": {
        "openinference.span.kind": "LLM", "input.value": "pending?", "output.value": "none"}},
]
conv = from_session_spans(spans, "s1")
check("turns reconstructed", len(conv.turns) == 4, str(len(conv.turns)))
check("tools attach to their OWN trace, not the latest turn",
      "get_balance" in conv.turns[1].tool_trace[0] and "get_pending" in conv.turns[3].tool_trace[0],
      f"t1={conv.turns[1].tool_trace} t3={conv.turns[3].tool_trace}")
check("failed tool calls are visibly marked",
      "ERROR timeout" in conv.turns[3].tool_trace[0],
      "check C5 fails silently if the trace only shows successes")
check("target turn resolves from traceIds",
      target_index(conv, ["t1"]) == 1 and target_index(conv, ["t2"]) == 3)
r = conv.render(3)
check("transcript opens with the marker the rubrics name", r.startswith("[BEGIN TRANSCRIPT]"))
check("exactly one turn is marked", r.count(">>> TARGET") == 1)
check("assistant turn with no tools gets the sentinel",
      "[tool trace: none recorded]" in Conversation("s", [Turn("assistant", "x")]).render(None))
check("conversation render marks nothing", ">>> TARGET" not in conv.render(None))
check("empty spans yield no turns", from_session_spans([], "s").turns == [])

# --- handler payload -------------------------------------------------------
v = Verdict("signal-density", 0.5, {
    "trigger_present": True, "injection_suspected": False,
    "checks": [{"id": i, "verdict": i != "C3", "span": "s"} for i in sd.check_ids],
    "evidence": ["e"], "reasoning": "C3 failed", "confidence": "high",
    "removable_fraction": "20_to_40pct"}, samples=[0.5, 0.5, 1.0, 0.5, 0.5])
payload = json.loads(_compact(v, conv, 3))
check("payload carries k provenance", payload["k"]["n"] == 5 and payload["k"]["s"] == v.samples)
check("payload carries per-judge extras", payload["x"]["removable_fraction"] == "20_to_40pct")
check("payload has no `label`/`explanation` keys",
      not {"label", "explanation"} & set(payload))
check("median is the reported score", v.score == 0.5 and v.label == "0.5")
check("split across the 0 boundary escalates",
      Verdict("x", 0.5, {}, [0.0, 0.5, 0.5]).needs_human_review)
check("unanimous non-zero does not escalate",
      not Verdict("x", 1.0, {"confidence": "high"}, [1.0] * 5).needs_human_review)

# --- verifier --------------------------------------------------------------
good = {"v": 1, "j": "signal-density", "trig": True, "inj": False,
        "chk": [{"id": i, "ok": True, "sp": "brown fox"} for i in sd.check_ids],
        "ev": ["brown fox"], "why": "ok", "conf": "high",
        "k": {"n": 5, "s": [1.0] * 5, "unan": True, "hf": 0, "review": False},
        "x": {"removable_fraction": "none"}}
check("valid payload passes", verify(sd, json.dumps(good), "1.0").ok)
check("prose is rejected", not verify(sd, "It was fine.", "1.0").ok)
check("wrong check ids rejected",
      not verify(sd, json.dumps({**good, "chk": [{"id": "C9", "ok": True, "sp": ""}]}), "1.0").ok)
check("k accounting mismatch caught",
      not verify(sd, json.dumps({**good, "k": {"n": 5, "s": [1.0]}}), "1.0").ok)
check("bright-line 0 with passing checks is allowed",
      verify(sd, json.dumps(good), "0").ok,
      "a bright line overrides the check count by definition")
check("1.0 with failing checks is an error",
      not verify(sd, json.dumps({**good, "chk": [{**c, "ok": False} for c in good["chk"]]}),
                 "1.0").ok)
check("evidence verified against transcript",
      any("not in transcript" in w
          for w in verify(sd, json.dumps(good), "1.0", transcript="nope").warnings))
check("recompute: all pass -> 1.0", recompute_label({"chk": [{"ok": True}]}) == "1.0")
check("recompute: any fail -> 0.5", recompute_label({"chk": [{"ok": False}]}) == "0.5")

# --- provisioning ----------------------------------------------------------
p = plan(LambdaTarget(arn="arn:aws:lambda:us-east-1:123456789012:function:judges"))
check("plan covers every deployable judge", len(p) == len(DEPLOY))
check("all code-based, none llmAsAJudge",
      all("codeBased" in x["evaluatorConfig"] for x in p),
      "the native LLM judge cannot receive a pre-rendered transcript")
check("timeout within AgentCore's 1-300s bound",
      all(1 <= x["evaluatorConfig"]["codeBased"]["lambdaConfig"]["lambdaTimeoutInSeconds"] <= 300
          for x in p))

try:
    import boto3  # noqa: F401
    from agentcore_judges.provision import validate_request
    bad = sum(len(validate_request("CreateEvaluator", x)) for x in p)
    check("[boto3] every CreateEvaluator payload validates", bad == 0, f"{bad} problems")
    lock = {"judges": {j: {"evaluatorId": f"{j.replace('-', '_')}-a1b2c3d4e5"}
                       for t in TIERS.values() for j in t["judges"]}}
    probs = sum(len(validate_request("CreateOnlineEvaluationConfig",
                                     online_config_payload(t, ["/lg"], ["svc"],
                                                           "arn:aws:iam::123456789012:role/R",
                                                           lock=lock)))
                for t in TIERS)
    check("[boto3] every online config validates", probs == 0, f"{probs} problems")
    check("[boto3] a bogus level is caught",
          bool(validate_request("CreateEvaluator", {**p[0], "level": "TURN"})))
    check("[boto3] a hyphenated name is caught (fullmatch, not match)",
          bool(validate_request("CreateEvaluator", {**p[0], "evaluatorName": "has-hyphens"})))
except ModuleNotFoundError:
    print("SKIP  [boto3] request validation — boto3 not installed")

# --- offline scoring: testsets ---------------------------------------------
SETS = {r.id: load_testset(r) for r in RUBRICS}
check("every judge has its 60 labelled samples",
      all(len(v) == 60 for v in SETS.values()),
      str({k: len(v) for k, v in SETS.items() if len(v) != 60}))
check("every set is 20/20/20 by construction",
      all(sorted(i.expected for i in v) == [0.0] * 20 + [0.5] * 20 + [1.0] * 20
          for v in SETS.values()))
check("scored items use the handler's own scope strings, not a second copy",
      {i.scope for v in SETS.values() for i in v} == set(SCOPE.values()),
      "a judge scored against a scope production never sends is measuring something else")
check("conversation judges are scored unmarked, as the handler scores them",
      all(i.target_turn is None and i.scope == SCOPE["conversation"]
          for r in RUBRICS if r.unit == "conversation" for i in SETS[r.id]))
check("turn judges mark exactly one turn",
      all(i.transcript.count(">>> TARGET") == 1
          for r in RUBRICS if r.unit == "turn" for i in SETS[r.id]))
check("the marked turn is an assistant turn",
      all(i.conversation.turns[i.target_turn].role == "assistant"
          for r in RUBRICS if r.unit == "turn" for i in SETS[r.id]))
check("items render through spans.py, markers and all",
      SETS["actionability"][0].transcript.startswith("[BEGIN TRANSCRIPT]")
      and "--- turn 0 | CUSTOMER ---" in SETS["actionability"][0].transcript)

# --- offline scoring: balanced sampling ------------------------------------
check("6 splits 2/2/2", band_sizes(6) == {0.0: 2, 0.5: 2, 1.0: 2})
check("the remainder goes to the lowest band first",
      band_sizes(7) == {0.0: 3, 0.5: 2, 1.0: 2} and
      band_sizes(8) == {0.0: 3, 0.5: 3, 1.0: 2})
draw = select(SETS["need-coverage"], judge_id="need-coverage", limit=6, seed=0)
check("a --limit 6 draw is 2 pass / 2 warning / 2 fail",
      sorted(i.expected for i in draw) == [0.0, 0.0, 0.5, 0.5, 1.0, 1.0],
      str([i.expected for i in draw]))
check("the same seed draws the same items",
      [i.id for i in select(SETS["need-coverage"], judge_id="need-coverage",
                            limit=6, seed=0)] == [i.id for i in draw])
check("a different seed draws different items",
      [i.id for i in select(SETS["need-coverage"], judge_id="need-coverage",
                            limit=6, seed=1)] != [i.id for i in draw])
check("--limit 0 takes all 60",
      len(select(SETS["need-coverage"], judge_id="need-coverage", limit=0, seed=0)) == 60)
check("a judge's draw is its own, not a shared shuffle",
      [i.id.split("-")[-1] for i in draw] !=
      [i.id.split("-")[-1] for i in select(SETS["actionability"],
                                           judge_id="actionability", limit=6, seed=0)])
check("raising --limit extends the draw rather than reshuffling it",
      set(i.id for i in draw) <= set(i.id for i in select(
          SETS["need-coverage"], judge_id="need-coverage", limit=12, seed=0)))

# --- offline scoring: the metric math --------------------------------------
def _item(expected, iid):
    return Item(id=iid, judge_id="fixture", expected=expected,
                conversation=Conversation("s", [Turn("assistant", "x")]),
                target_turn=0, scope=SCOPE["turn"])


FIXTURE = [
    ItemResult(_item(1.0, "f1"), score=1.0),   # exact
    ItemResult(_item(0.5, "f2"), score=0.0),   # fp0: cried failure on a warning
    ItemResult(_item(0.0, "f3"), score=0.5),   # fn0: missed the bright line by one
    ItemResult(_item(0.0, "f4"), score=1.0),   # fn0, and not within one level
    ItemResult(_item(0.0, "f5"), score=0.0),   # exact
    ItemResult(_item(1.0, "f6"), score=None),  # unparseable
    ItemResult(_item(0.0, "f7"), score=None),  # unparseable on a 0 label
]
T = tally("fixture", FIXTURE)
check("unparseable items stay in n", T.n == 7 and T.unparseable == 2, f"n={T.n}")
check("exact counts only equality", T.exact == 2, str(T.exact))
check("±1 level counts a half-step miss", T.within == 4, str(T.within))
check("fp0 is a 0 the label did not have", T.fp0 == 1, str(T.fp0))
check("fn0 counts a missed bright line, unparseable included",
      T.fn0 == 3, f"{T.fn0} — an unusable output on a 0 label is still a miss")
check("an unparseable item is never an fp0",
      tally("f", [ItemResult(_item(1.0, "x"), score=None)]).fp0 == 0)
check("band totals keep unparseable items in the denominator",
      T.band_total == {1.0: 2, 0.5: 1, 0.0: 4} and T.band_hits == {1.0: 1, 0.5: 0, 0.0: 1},
      f"{T.band_total} {T.band_hits}")

REPORT = format_report([T])
check("the report header is the shape all three folders print",
      REPORT.splitlines()[0] == HEADER)
check("the row carries the rates and the per-band hits",
      "28.6%" in REPORT and "57.1%" in REPORT and "1/2" in REPORT and "1/4" in REPORT,
      REPORT.splitlines()[2])
check("unparseable gets its own line", "\nunparseable" in REPORT and
      "fixture x2" in REPORT)
check("the legend matches the parent's wording", LEGEND in REPORT)
check("the table is byte-identical to the cross-folder contract",
      format_report([Tally("actionability", n=6, exact=5, within=6,
                           band_hits={1.0: 2, 0.5: 1, 0.0: 2},
                           band_total={1.0: 2, 0.5: 2, 0.0: 2})]).splitlines()[:3] ==
      ["judge                            n   exact  ±1 lvl   fp0   fn0   pass  warn  fail",
       "-" * 78,
       "actionability                    6   83.3%  100.0%     0     0   2/2   1/2   2/2"],
      "results are only comparable across the three folders if the columns are")

# --- offline scoring: this folder's own parse step -------------------------
def _verdict(rubric, score, ok, **over):
    p = {"trigger_present": True, "injection_suspected": False,
         "checks": [{"id": i, "verdict": ok, "span": "x"} for i in rubric.check_ids],
         "evidence": [], "reasoning": "r", "confidence": "high", "score": score}
    return Verdict(rubric.id, score, {**p, **over}, [score] * 5)


SD_ITEM = _item(1.0, "sd-1")
g_ok = grade(sd, SD_ITEM, _verdict(sd, 1.0, True))
check("a well-formed verdict survives compaction and verification",
      g_ok.score == 1.0 and g_ok.verification.ok and not g_ok.unparseable,
      str(g_ok.verification.errors[:1]))
check("grading goes through the payload the Lambda would actually return",
      json.loads(g_ok.explanation)["j"] == "signal-density")
g_bad = grade(sd, SD_ITEM, _verdict(sd, 0.75, True))
check("an out-of-range score is unparseable, not a KeyError",
      g_bad.unparseable and g_bad.score is None,
      "Verdict.label would raise on 0.75; a median over an even sample count reaches it")
check("an unparseable score is graded without a label rather than guessed",
      g_bad.verification.label is None)
g_none = grade(sd, SD_ITEM, _verdict(sd, None, True))
check("a missing score is unparseable", g_none.unparseable)
check("level_of admits only the three levels",
      [level_of(x) for x in (1, "0.5", 0.0, 0.75, None, "high")] ==
      [1.0, 0.5, 0.0, None, None, None])
g_malformed = grade(sd, SD_ITEM, Verdict(sd.id, 1.0, {"checks": "not a list"}, [1.0]))
check("a malformed payload is one unparseable item, not a dead run",
      g_malformed.unparseable and "compaction" in (g_malformed.error or ""),
      g_malformed.error or "")
g_thin = grade(sd, SD_ITEM, Verdict(sd.id, 1.0, {"checks": [], "score": 1.0}, [1.0]))
check("a payload that compacts but declares no checks is rejected by verify",
      not g_thin.verification.ok and g_thin.verification.errors)
g_div = grade(sd, SD_ITEM, _verdict(sd, 0.5, True))
check("a score that contradicts its own checks is reported as a divergence",
      g_div.verification.diverged and
      "disagree" in format_report([tally("signal-density", [g_div])]),
      "the model said 0.5 with every check passing")

# --- offline scoring: every deployable judge builds a prompt ---------------
built = []
for _r in DEPLOY:
    _i = select(SETS[_r.id], judge_id=_r.id, limit=3, seed=0)[0]
    _req = build_request(_r, _i.transcript, _i.scope)
    built.append(("{{scope}}" not in _req["messages"][0]["content"]
                  and _i.scope in _req["messages"][0]["content"]
                  and _req["output_config"]["format"]["schema"]["properties"]
                     ["checks"]["minItems"] == len(_r.check_ids)))
check("every deployable judge builds a request from real testset items",
      all(built) and len(built) == 10, f"{built.count(False)} failed")
check("the default judge set is the deployable one, not both variants",
      [r.id for r in resolve_rubrics(None, traced=False)] == [r.id for r in DEPLOY])
check("--traced swaps the variant in rather than adding it",
      "capability-honesty-traced" in {r.id for r in resolve_rubrics(None, traced=True)}
      and len(resolve_rubrics(None, traced=True)) == 10)
check("an explicit --judge can still name the traced variant",
      [r.id for r in resolve_rubrics(["capability-honesty-traced"], traced=False)]
      == ["capability-honesty-traced"])


# --- offline scoring: the whole item path, against a stub client -----------
class _StubMessages:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def create(self, **req):
        self.requests.append(req)
        return SimpleNamespace(content=[SimpleNamespace(
            type="text", text=json.dumps(self.payload))])


class _StubClient:
    def __init__(self, payload):
        self.messages = _StubMessages(payload)


_zero = [i for i in SETS["actionability"] if i.expected == 0.0][0]
_stub = _StubClient({"trigger_present": True, "injection_suspected": False,
                     "checks": [{"id": i, "verdict": False, "span": "x"}
                                for i in RUBRICS_BY_ID["actionability"].check_ids],
                     "evidence": [], "reasoning": "bright line", "confidence": "high",
                     "score": 0})
_res = run_item(RUBRICS_BY_ID["actionability"], _zero, k=3, client=_stub)
check("an item runs end to end with no network", _res.score == 0.0 and not _res.error,
      _res.error or "")
check("k fires k times", len(_stub.messages.requests) == 3)
check("the request the item sent carries the rendered transcript",
      "[BEGIN TRANSCRIPT]" in _stub.messages.requests[0]["messages"][0]["content"])
check("a caught bright line is neither fp0 nor fn0",
      (lambda t: t.exact == 1 and t.fp0 == 0 and t.fn0 == 0)(
          tally("actionability", [_res])))

with tempfile.TemporaryDirectory() as _d:
    _p = Path(_d) / "results.jsonl"
    write_results(_p, [_res])
    _rec = json.loads(_p.read_text().splitlines()[0])
    check("--out is readable by the verify verb without conversion",
          {"judge_id", "label", "explanation", "transcript"} <= set(_rec)
          and verify(RUBRICS_BY_ID["actionability"], _rec["explanation"],
                     _rec["label"]).ok)
    check("--out records the label alongside the expectation it was scored against",
          _rec["label"] == "0" and _rec["expected_score"] == 0.0
          and _rec["samples"] == [0.0, 0.0, 0.0])

# --- the scale, defined once -----------------------------------------------
# Six hand-written copies of "the scale is 0 / 0.5 / 1.0" is five chances for the
# table, the schema and the verifier to disagree about what a score is.
check("every spelling of the scale derives from SCALE",
      LABELS == tuple(SCALE) and LABEL_OF == {v: k for k, v in SCALE.items()}
      and BANDS == (0.0, 0.5, 1.0)
      and output_schema(sd)["properties"]["score"]["enum"] == list(BANDS)
      and Verdict("x", 0.0, {}).label == LABEL_OF[0.0]
      and verify_mod.LABELS is LABELS)

# --- the return boundary: one implementation, reached by both callers -------
check("the Lambda and the scorer compact through the same function object",
      handler_mod._compact is boundary_mod._compact is score_mod._compact,
      "'exactly as handler does it' has to be an import, not a promise")
_ac = agentcore_result(v, conv, 3)
check("the return boundary returns exactly AgentCore's three fields",
      tuple(_ac) == RESULT_FIELDS and _ac["label"] == "0.5" and _ac["value"] == 0.5,
      str(tuple(_ac)))
check("grade takes its label from that boundary rather than a private mapping",
      g_ok.label == "1.0" and g_bad.label is None,
      f"{g_ok.label!r} {g_bad.label!r}")

# --- importing the CLI must not reconfigure the root logger ----------------
# handler sets the ROOT logger's level from $LOG_LEVEL at module scope, which is
# right for a Lambda container and rude everywhere else. score used to import it
# for `_compact`, so `agentcore-judges list` silently reconfigured logging.
_probe = subprocess.run(
    [sys.executable, "-c",
     "import logging, sys;"
     f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r});"
     "before = logging.getLogger().level;"
     "import agentcore_judges.cli;"
     "print(before, logging.getLogger().level,"
     " 'agentcore_judges.handler' in sys.modules)"],
    capture_output=True, text=True, env={**os.environ, "LOG_LEVEL": "DEBUG"})
check("importing the CLI leaves the root logger alone",
      _probe.stdout.split() == ["30", "30", "False"],
      (_probe.stdout.strip() or _probe.stderr[-160:]))

# --- testset rows must be on the scale, and fail on the row that is not ----
with tempfile.TemporaryDirectory() as _d:
    _badset = Path(_d) / "bad.jsonl"
    _badset.write_text(json.dumps(
        {"id": "row-7", "expected_score": 0.7, "target_turn": 1,
         "turns": [{"role": "customer", "text": "q"},
                   {"role": "assistant", "text": "a"}]}) + "\n", encoding="utf-8")
    try:
        load_testset(sd, path=_badset)
        _load_err = ""
    except ValueError as e:
        _load_err = str(e)
    check("an off-scale label fails on load, naming the file, the row and the value",
          "bad.jsonl" in _load_err and "row-7" in _load_err and "0.7" in _load_err,
          _load_err or "no error raised — it would have been a KeyError in tally()")

# --- a short band is surfaced, not silently under-drawn --------------------
_thin = ([i for i in SETS["need-coverage"] if i.expected != 0.0]
         + [i for i in SETS["need-coverage"] if i.expected == 0.0][:4])
_short = shortfalls(_thin, judge_id="need-coverage", limit=18)
check("a band with fewer items than its quota is reported",
      len(_short) == 1 and (_short[0].band, _short[0].wanted, _short[0].drew)
      == (0.0, 6, 4), str(_short))
check("the under-draw itself is unchanged — it draws what exists",
      len(select(_thin, judge_id="need-coverage", limit=18, seed=0)) == 16)
check("no shortfall is reported when every band fills",
      shortfalls(SETS["need-coverage"], judge_id="need-coverage", limit=18) == [])
check("the shortfall is visible in the report a reader sees",
      "fail: asked for 6, drew 4" in format_report([T], _short)
      and "not as full quotas" in format_report([T], _short))
check("and in the dry run, before anyone has paid for the short column",
      "fail: asked for 6, drew 4" in
      dry_run_report([(RUBRICS_BY_ID["need-coverage"], _thin[:4])], k=1,
                     have_key=True, shorts=_short))

# --- transport failure is not a judge failure ------------------------------
class APIConnectionError(Exception):
    """Named as the Anthropic SDK names it — that name IS the classification."""


class _BoomMessages:
    def __init__(self, excs):
        self.excs = list(excs)
        self.calls = 0

    def create(self, **req):
        exc = self.excs[self.calls % len(self.excs)]
        self.calls += 1
        raise exc


class _BoomClient:
    def __init__(self, *excs):
        self.messages = _BoomMessages(excs)


check("transport errors are recognised by name, through the MRO, with no SDK",
      is_transport_error(APIConnectionError("down"))
      and is_transport_error(TimeoutError())
      and not is_transport_error(json.JSONDecodeError("x", "y", 0)))

_t_zero = [i for i in SETS["actionability"] if i.expected == 0.0][0]
_t_res = run_item(RUBRICS_BY_ID["actionability"], _t_zero, k=3,
                  client=_BoomClient(APIConnectionError("connection reset")))
check("an item we never got an answer for is transport, not unparseable",
      _t_res.transport and not _t_res.unparseable and _t_res.score is None,
      _t_res.error or "")
_TT = tally("actionability", [_t_res])
check("a transport failure is excluded from n, exact, ±1, fp0 and fn0",
      (_TT.n, _TT.exact, _TT.within, _TT.fp0, _TT.fn0, _TT.unparseable)
      == (0, 0, 0, 0, 0, 0) and _TT.transport == 1,
      f"n={_TT.n} fn0={_TT.fn0} unparseable={_TT.unparseable}")
check("a transport failure on a 0-labelled item is NOT a missed bright line",
      _TT.fn0 == 0,
      "an outage reported as 'this judge missed a hard failure' is the lie")
check("a transport failure is excluded from the band denominators",
      _TT.band_total == {1.0: 0, 0.5: 0, 0.0: 0}, str(_TT.band_total))
_TREP = format_report([_TT])
check("transport gets its own line, naming the judge and the count",
      "transport" in _TREP and "actionability x1" in _TREP
      and "no answer received" in _TREP,
      [ln for ln in _TREP.splitlines() if ln.startswith("transport")][:1])
check("a run that measured nothing does not print as a perfect one",
      "NOTHING WAS MEASURED" in _TREP and "100.0%" not in _TREP)
_mixed = run_item(RUBRICS_BY_ID["actionability"], _t_zero, k=2,
                  client=_BoomClient(APIConnectionError("reset"),
                                     ValueError("not JSON")))
check("an answer that broke the contract stays unparseable, transport or no",
      _mixed.unparseable and not _mixed.transport,
      "one sample DID answer and broke the contract; that is measurable")
check("a mixed failure is still counted in n and in fn0 on a 0 label",
      (lambda t: t.n == 1 and t.unparseable == 1 and t.fn0 == 1
       and t.transport == 0)(tally("actionability", [_mixed])))
check("the judge layer hands up the causes, not just their text",
      all(isinstance(c, BaseException)
          for c in AllSamplesFailed("j", [APIConnectionError("x")]).causes))

# --- --out is proved writable before a single call is bought ---------------
with tempfile.TemporaryDirectory() as _d:
    _wall = Path(_d) / "a-file-not-a-dir"
    _wall.write_text("x", encoding="utf-8")
    try:
        prepare_out(_wall / "runs" / "out.jsonl")
        _out_err = ""
    except ValueError as e:
        _out_err = str(e)
    check("an unwritable --out is refused, by name, before anything is spent",
          "not writable" in _out_err and "out.jsonl" in _out_err,
          _out_err or "no error raised")
    _fresh = Path(_d) / "deep" / "nested" / "out.jsonl"
    check("a writable --out has its parent created and is left uncreated",
          prepare_out(_fresh) == _fresh.resolve() and _fresh.parent.is_dir()
          and not _fresh.exists(),
          "proving a path writable must not leave a stray file behind")

# --- confirm() resolves its stream at call time ----------------------------
_cbuf = io.StringIO()
with contextlib.redirect_stderr(_cbuf):
    _confirmed = confirm(42, yes=True)
check("confirm prints to the stderr in force when it is called, not at import",
      _confirmed and "42 Anthropic call(s)" in _cbuf.getvalue(),
      _cbuf.getvalue() or "nothing reached the redirected stream")

# --- offline scoring: the paid path, with the SDK stubbed out --------------
# Everything past the cost confirmation is unreachable in a dry run, so nothing
# above this exercises it. A name that is only resolved after the operator has
# already approved the spend is the worst place in the file to have one wrong.
_sd_stub = _StubClient({"trigger_present": True, "injection_suspected": False,
                        "checks": [{"id": i, "verdict": True, "span": "x"}
                                   for i in sd.check_ids],
                        "evidence": [], "reasoning": "r", "confidence": "high",
                        "score": 1})
sys.modules["anthropic"] = SimpleNamespace(Anthropic=lambda *a, **kw: _sd_stub)
os.environ["ANTHROPIC_API_KEY"] = "sk-stub"
with tempfile.TemporaryDirectory() as _d:
    _out = Path(_d) / "runs" / "sd.jsonl"
    _buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(io.StringIO()):
            _rc = cli_main(["score", "--judge", "signal-density", "--limit", "3",
                            "--k", "1", "--yes", "--out", str(_out)])
    except Exception as e:  # noqa: BLE001 — an unbound name here IS the finding
        _rc = f"{type(e).__name__}: {e}"
    _live = _buf.getvalue()
    check("--yes carries the score verb past the confirmation and into the run",
          _rc == 0, str(_rc))
    check("the paid path opens a client and spends exactly the calls it announced",
          len(_sd_stub.messages.requests) == 3, str(len(_sd_stub.messages.requests)))
    check("a live run prints the same table the report contract pins",
          _live.lstrip("\n").splitlines()[:1] == [HEADER] and LEGEND in _live,
          _live.lstrip("\n").splitlines()[0] if _live.strip() else "no output")
    check("--out creates its directory and writes one record per item",
          _out.exists() and len(_out.read_text().splitlines()) == 3)

# An unwritable --out used to be discovered only after the run: the operator paid
# for every call and then watched the results go nowhere.
with tempfile.TemporaryDirectory() as _d:
    _wall = Path(_d) / "a-file-not-a-dir"
    _wall.write_text("x", encoding="utf-8")
    _spent_before = len(_sd_stub.messages.requests)
    _ebuf = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(_ebuf):
        _rc_out = cli_main(["score", "--judge", "signal-density", "--limit", "3",
                            "--k", "1", "--yes", "--out", str(_wall / "r.jsonl")])
    check("the score verb refuses an unwritable --out without spending a call",
          _rc_out == 2 and len(_sd_stub.messages.requests) == _spent_before
          and "not writable" in _ebuf.getvalue(),
          f"rc={_rc_out} spent={len(_sd_stub.messages.requests) - _spent_before}")

# A whole run lost to transport, driven through the CLI: the table must say so
# rather than print an empty row that reads like a clean sweep.
_down = _BoomClient(APIConnectionError("connection reset by peer"))
sys.modules["anthropic"] = SimpleNamespace(Anthropic=lambda *a, **kw: _down)
with tempfile.TemporaryDirectory() as _d:
    _tout = Path(_d) / "transport.jsonl"
    _tbuf = io.StringIO()
    with contextlib.redirect_stdout(_tbuf), contextlib.redirect_stderr(io.StringIO()):
        _trc = cli_main(["score", "--judge", "signal-density", "--limit", "3",
                         "--k", "1", "--yes", "--out", str(_tout)])
    _tlive = _tbuf.getvalue()
    check("a run lost to transport still completes and prints the table",
          _trc == 0 and HEADER in _tlive, str(_trc))
    check("the CLI reports the lost items on the transport line, not as fn0",
          "signal-density x3" in _tlive and "no answer received" in _tlive
          and "NOTHING WAS MEASURED" in _tlive,
          [ln for ln in _tlive.splitlines() if "transport" in ln][:1])
    _trecs = [json.loads(x) for x in _tout.read_text().splitlines()]
    check("--out marks the transport records so a reader can tell them apart",
          len(_trecs) == 3 and all(r["transport"] and r["label"] is None
                                   for r in _trecs))
    _vbuf = io.StringIO()
    with contextlib.redirect_stdout(_vbuf):
        _vrc = cli_main(["verify", str(_tout)])
    check("the verify verb skips them rather than calling them invalid payloads",
          _vrc == 0 and "0 invalid" in _vbuf.getvalue()
          and _vbuf.getvalue().count("skip") == 3,
          _vbuf.getvalue()[-160:])
del sys.modules["anthropic"]
del os.environ["ANTHROPIC_API_KEY"]

# --- CLI shim --------------------------------------------------------------
SHIM = Path(__file__).resolve().parent.parent / "bin" / "agentcore-judges"
env = {k: v for k, v in os.environ.items() if k != "ACJ_REEXEC"}
res = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "list"],
                     capture_output=True, text=True, env=env)
check("shim runs under a bare system interpreter", res.returncode == 0,
      (res.stderr or res.stdout)[-160:])
res2 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "render", "--judge", "nope"],
                      capture_output=True, text=True, env=env)
check("operator error exits 2 without a traceback",
      res2.returncode == 2 and "Traceback" not in res2.stderr, res2.stderr[-140:])

keyless = {k: v for k, v in env.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
res3 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--limit", "3"],
                      capture_output=True, text=True, env=keyless, stdin=subprocess.DEVNULL)
check("score with no API key dries out and exits 0",
      res3.returncode == 0 and "nothing was sent" in res3.stdout,
      (res3.stderr or res3.stdout)[-200:])
check("the missing key is named, not raised as an auth traceback",
      "ANTHROPIC_API_KEY" in res3.stdout and "Traceback" not in res3.stderr)
check("a dry run never defaults to the full 660",
      "30 item(s)" in res3.stdout and "150 Anthropic call(s)" in res3.stdout,
      res3.stdout.splitlines()[-4] if res3.stdout else "")
res4 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--limit", "3"],
                      capture_output=True, text=True, stdin=subprocess.DEVNULL,
                      env={**keyless, "ANTHROPIC_API_KEY": "sk-not-a-real-key"})
check("a live run refuses non-interactively without --yes",
      res4.returncode == 2 and "--yes" in res4.stderr and "150" in res4.stderr,
      (res4.stderr or res4.stdout)[-200:])

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

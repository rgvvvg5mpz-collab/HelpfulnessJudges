"""Offline tests. No AWS account, no credentials, no network.

Run: .venv/bin/python -m tests.test_suite
Tests marked [boto3] are skipped when boto3 is absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore_judges import LEVELS, MAX_EVALUATORS_PER_CONFIG, UNIT_TO_LEVEL
from agentcore_judges.handler import _compact
from agentcore_judges.judge import Verdict, build_request
from agentcore_judges.naming import EVALUATOR_NAME, to_evaluator_name, to_judge_id
from agentcore_judges.provision import LambdaTarget, TIERS, online_config_payload, plan
from agentcore_judges.spans import Conversation, Turn, from_session_spans, target_index
from agentcore_judges.spec import deployable, load_rubrics, output_schema
from agentcore_judges.verify import recompute_label, verify

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


RUBRICS = load_rubrics()
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

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

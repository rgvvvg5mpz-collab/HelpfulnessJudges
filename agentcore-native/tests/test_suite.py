"""Offline tests for the native llmAsAJudge path. No AWS account, no network."""
from __future__ import annotations

import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from native_judges import (LEVELS, MAX_EVALUATORS_PER_CONFIG, PLACEHOLDERS,
                           RATING_SCALE, UNIT_TO_LEVEL)
from native_judges.naming import EVALUATOR_NAME, to_evaluator_name
from native_judges.provision import ModelConfig, TIERS, online_config_payload, plan
from native_judges.spec import deployable, load_rubrics
from native_judges.transform import transform

FAILS: list[str] = []
def check(n, c, d=""):
    print(f"{'PASS' if c else 'FAIL'}  {n}" + (f" — {d}" if d and not c else ""))
    if not c: FAILS.append(n)

RUBRICS = load_rubrics(); DEPLOY = deployable(RUBRICS)

# --- the transform is the whole point of this folder ------------------------
for r in RUBRICS:
    t = transform(r)
    check(f"{r.id}: no transcript markers survive", not t.residual_markers,
          str(t.residual_markers))
    check(f"{r.id}: the correct placeholder is present",
          PLACEHOLDERS[t.level][0] in t.instructions)
    check(f"{r.id}: output-contract sections were stripped",
          "## How to score" not in t.instructions and "[END TRANSCRIPT]" not in t.instructions)
    check(f"{r.id}: rating labels match the scale exactly",
          all(x["label"] in t.instructions for x in RATING_SCALE))
    check(f"{r.id}: the check ids survive into the reasoning contract",
          all(c in t.instructions for c in r.check_ids))

t = transform([r for r in DEPLOY if r.unit == "turn"][0])
check("turn judges get {assistant_turn}", "{assistant_turn}" in t.instructions)
ts = transform([r for r in DEPLOY if r.unit == "conversation"][0])
check("conversation judges do NOT get {assistant_turn}",
      "{assistant_turn}" not in ts.instructions,
      "session scope has no per-turn placeholder")
check("the transform is deterministic",
      transform(RUBRICS[0]).instructions == transform(RUBRICS[0]).instructions)
check("what was removed is recorded, not silent",
      all(transform(r).removed for r in DEPLOY))

# --- scale ------------------------------------------------------------------
check("three rating levels", len(RATING_SCALE) == 3)
check("values are 0 / 0.5 / 1.0",
      sorted(x["value"] for x in RATING_SCALE) == [0.0, 0.5, 1.0])
check("every label within AgentCore's [1,100] bound",
      all(1 <= len(x["label"]) <= 100 for x in RATING_SCALE))
check("every level has a definition", all(x["definition"] for x in RATING_SCALE))

# --- naming and levels ------------------------------------------------------
check("names match the evaluatorName pattern",
      all(EVALUATOR_NAME.fullmatch(to_evaluator_name(r.id)) for r in RUBRICS))
lv = [UNIT_TO_LEVEL[r.unit] for r in DEPLOY]
check("8 TRACE + 2 SESSION", lv.count("TRACE") == 8 and lv.count("SESSION") == 2, str(lv))
check("all levels are real", set(lv) <= set(LEVELS))

# --- provisioning -----------------------------------------------------------
ps = plan(ModelConfig())
check("plan covers every deployable judge", len(ps) == len(DEPLOY))
check("all llmAsAJudge, none codeBased",
      all("llmAsAJudge" in p["evaluatorConfig"] for p in ps))
check("instructions are non-trivial",
      all(len(p["evaluatorConfig"]["llmAsAJudge"]["instructions"]) > 5000 for p in ps))
check("tiers fit the cap",
      all(len(t["judges"]) <= MAX_EVALUATORS_PER_CONFIG for t in TIERS.values()))
check("tiers partition the suite",
      sorted(j for t in TIERS.values() for j in t["judges"]) == sorted(r.id for r in DEPLOY))

try:
    import boto3  # noqa
    from native_judges.provision import validate_request
    bad = sum(len(validate_request("CreateEvaluator", p)) for p in ps)
    check("[boto3] every CreateEvaluator validates", bad == 0, f"{bad} problems")
    check("[boto3] temperature above 1.0 is caught (Bedrock bound is [0,1], not [0,2])",
          bool(validate_request("CreateEvaluator", plan(ModelConfig(temperature=1.5))[0])))
    check("[boto3] a bogus level is caught",
          bool(validate_request("CreateEvaluator", {**ps[0], "level": "TURN"})))
    lock = {"judges": {j: {"evaluatorId": f"{j.replace('-', '_')}-a1b2c3d4e5"}
                       for t in TIERS.values() for j in t["judges"]}}
    probs = sum(len(validate_request("CreateOnlineEvaluationConfig",
                online_config_payload(k, ["/lg"], ["s"],
                                      "arn:aws:iam::123456789012:role/R", lock=lock)))
                for k in TIERS)
    check("[boto3] online configs validate", probs == 0, f"{probs} problems")
except ModuleNotFoundError:
    print("SKIP  [boto3] request validation")

# --- CLI --------------------------------------------------------------------
SHIM = Path(__file__).resolve().parent.parent / "bin" / "native-judges"
env = {k: v for k, v in os.environ.items() if k != "NJ_REEXEC"}
r1 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "list"],
                    capture_output=True, text=True, env=env)
check("shim runs under a bare interpreter", r1.returncode == 0, (r1.stderr or "")[-140:])
r2 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "show", "--judge", "nope"],
                    capture_output=True, text=True, env=env)
check("operator error exits 2, no traceback",
      r2.returncode == 2 and "Traceback" not in r2.stderr)

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

"""Create the AgentCore Evaluators and the online evaluation config.

Every API shape here was verified against the botocore service model shipped in
boto3 1.43.83 — `bedrock-agentcore-control` operations, the `level` enum, and the
`evaluatorConfig` union — not against documentation.

Unlike Arize, AgentCore HAS a declarative surface: `AWS::BedrockAgentCore::Evaluator`
and `AWS::BedrockAgentCore::OnlineEvaluationConfig` are CloudFormation types with
Terraform and CDK equivalents. `infra/template.yaml` is generated from the same
plan, so you can take either route. This module is the imperative one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import MAX_EVALUATORS_PER_CONFIG, UNIT_TO_LEVEL
from .naming import to_evaluator_name
from .spec import ROOT, Rubric, deployable, load_rubrics

LOCKFILE = ROOT / "evaluators.lock.json"

# Two configs, so the bright-line judges run on everything while the relational
# ones — which the validation protocol already restricts to directional use — are
# sampled. Keep the critical tier at 100% so its cohort is a guaranteed superset;
# there is no seed to align cohorts otherwise.
TIERS: dict[str, dict[str, Any]] = {
    "critical": {"sampling": 100.0, "judges": [
        "capability-honesty", "need-coverage", "actionability", "calibrated-hedging"]},
    "directional": {"sampling": 10.0, "judges": [
        "empathic-attunement", "emotional-accuracy", "conduct-adaptation",
        "signal-density", "plain-language-clarity", "effort-and-resolution-path"]},
}


@dataclass
class LambdaTarget:
    arn: str
    # AgentCore allows 1-300s. k=5 concurrent Opus calls at effort:high is the
    # sizing risk; measure before trusting the default.
    timeout_seconds: int = 300


def evaluator_payload(rubric: Rubric, target: LambdaTarget) -> dict[str, Any]:
    """A CreateEvaluator request. Pure — no boto3 import, no network."""
    return {
        "evaluatorName": to_evaluator_name(rubric.id),
        "description": f"{rubric.id} — helpfulness judge ({rubric.tier}, unit={rubric.unit})",
        "level": UNIT_TO_LEVEL[rubric.unit],
        "evaluatorConfig": {
            "codeBased": {
                "lambdaConfig": {
                    "lambdaArn": target.arn,
                    "lambdaTimeoutInSeconds": target.timeout_seconds,
                }
            }
        },
        "tags": {"suite": "helpfulness-judges", "judge": rubric.id,
                 "unit": rubric.unit, "tier": rubric.tier},
    }


def plan(target: LambdaTarget, *, prefer_traced: bool = False) -> list[dict[str, Any]]:
    rubrics = deployable(load_rubrics(), prefer_traced=prefer_traced)
    return [evaluator_payload(r, target) for r in rubrics]


def online_config_payload(
    tier: str,
    log_group_names: list[str],
    service_names: list[str],
    execution_role_arn: str,
    *,
    lock: dict[str, Any] | None = None,
    enable_on_create: bool = False,
    session_timeout_minutes: int = 30,
) -> dict[str, Any]:
    """A CreateOnlineEvaluationConfig request.

    `dataSourceConfig.cloudWatchLogs` is what makes CloudWatch the trace source:
    AgentCore reads spans from these log groups, reconstructs sessions, and hands
    them to the evaluator.
    """
    lock = lock if lock is not None else read_lock()
    cfg = TIERS[tier]
    ids = []
    for jid in cfg["judges"]:
        entry = lock["judges"].get(jid)
        if not entry:
            raise ValueError(f"{jid} is not in the lockfile — run `provision --apply` first")
        ids.append({"evaluatorId": entry["evaluatorId"]})
    if len(ids) > MAX_EVALUATORS_PER_CONFIG:
        raise ValueError(
            f"tier {tier!r} has {len(ids)} evaluators; the cap is "
            f"{MAX_EVALUATORS_PER_CONFIG} per online config and it is not adjustable"
        )
    return {
        "onlineEvaluationConfigName": f"helpfulness_judges_{tier}",
        "description": f"Helpfulness judge suite — {tier} tier",
        "rule": {
            "samplingConfig": {"samplingPercentage": cfg["sampling"]},
            "sessionConfig": {"sessionTimeoutMinutes": session_timeout_minutes},
        },
        "dataSourceConfig": {
            "cloudWatchLogs": {
                "logGroupNames": log_group_names,
                "serviceNames": service_names,
            }
        },
        "evaluators": ids,
        "evaluationExecutionRoleArn": execution_role_arn,
        "enableOnCreate": enable_on_create,
        "tags": {"suite": "helpfulness-judges", "tier": tier},
    }


def read_lock() -> dict[str, Any]:
    return json.loads(LOCKFILE.read_text()) if LOCKFILE.exists() else {"judges": {}}


def write_lock(d: dict[str, Any]) -> None:
    LOCKFILE.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")


def apply(target: LambdaTarget, *, region: str | None = None,
          prefer_traced: bool = False, dry_run: bool = True) -> dict[str, Any]:
    """Create evaluators that do not exist yet. Idempotent by name."""
    payloads = plan(target, prefer_traced=prefer_traced)
    lock = read_lock()
    actions: list[dict[str, Any]] = []

    client = None
    if not dry_run:
        import boto3
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        existing = {e["evaluatorName"]: e for page in
                    client.get_paginator("ListEvaluators").paginate()
                    for e in page.get("evaluators", [])}
    else:
        existing = {}

    for p in payloads:
        name = p["evaluatorName"]
        if dry_run:
            actions.append({"evaluatorName": name, "action": "create", "request": p})
            continue
        if name in existing:
            # An evaluator referenced by an ENABLED online config is locked and
            # cannot be modified. Pause the config before updating a rubric.
            eid = existing[name].get("evaluatorId") or existing[name].get("id")
            lock["judges"][__import__("agentcore_judges.naming", fromlist=["x"])
                           .to_judge_id(name)] = {"evaluatorId": eid, "evaluatorName": name}
            actions.append({"evaluatorName": name, "action": "exists", "evaluatorId": eid})
            continue
        res = client.create_evaluator(**p)
        eid = res.get("evaluatorId") or res.get("id")
        from .naming import to_judge_id
        lock["judges"][to_judge_id(name)] = {
            "evaluatorId": eid, "evaluatorArn": res.get("evaluatorArn"),
            "evaluatorName": name, "level": p["level"],
        }
        actions.append({"evaluatorName": name, "action": "created", "evaluatorId": eid})

    if not dry_run:
        write_lock(lock)
    return {"dry_run": dry_run, "actions": actions}


# --------------------------------------------------------------------------
# Strict request validation
# --------------------------------------------------------------------------
def validate_request(operation: str, params: dict[str, Any]) -> list[str]:
    """Validate a request against the botocore service model, strictly.

    `botocore.validate.validate_parameters` checks structure and types but NOT
    enum values, patterns or numeric bounds — a `level` of "TURN" sails straight
    through it. This walks the shape and enforces all three.

    One subtlety worth stating: AWS shape patterns are implicitly FULL matches.
    Using `re.match` lets `has-hyphens` pass `[a-zA-Z][a-zA-Z0-9_]{0,47}` on the
    `has` prefix alone, so this uses `re.fullmatch`.

    Returns a list of problems; empty means valid. Needs boto3.
    """
    import re

    import boto3

    model = boto3.session.Session()._session.get_service_model("bedrock-agentcore-control")
    shape = model.operation_model(operation).input_shape
    problems: list[str] = []

    def walk(sh, value, path=""):
        if sh.type_name == "structure":
            for req in (sh.required_members or []):
                if req not in value:
                    problems.append(f"{path}.{req}: missing required member")
            for k, v in value.items():
                if k not in sh.members:
                    problems.append(f"{path}.{k}: not a member of {sh.name}")
                else:
                    walk(sh.members[k], v, f"{path}.{k}")
        elif sh.type_name == "list":
            for i, item in enumerate(value):
                walk(sh.member, item, f"{path}[{i}]")
        elif sh.type_name == "map":
            return
        else:
            md = sh.metadata
            if (enum := md.get("enum")) and value not in enum:
                problems.append(f"{path}: {value!r} not in {enum}")
            if (pat := md.get("pattern")) and isinstance(value, str):
                if not re.fullmatch(pat, value):
                    problems.append(f"{path}: {value!r} fails pattern {pat}")
            lo, hi = md.get("min"), md.get("max")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if lo is not None and value < lo:
                    problems.append(f"{path}: {value} below min {lo}")
                if hi is not None and value > hi:
                    problems.append(f"{path}: {value} above max {hi}")

    walk(shape, params)
    return problems

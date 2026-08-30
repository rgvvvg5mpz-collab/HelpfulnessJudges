"""Create native llmAsAJudge Evaluators and the online evaluation config.

Shapes verified against the botocore 1.43.83 service model for
`bedrock-agentcore-control`, including the bounds that bite:
`temperature [0,1]` on the Bedrock config (not [0,2]), `label [1,100]`, and the
`evaluatorName` pattern `[a-zA-Z][a-zA-Z0-9_]{0,47}`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import MAX_EVALUATORS_PER_CONFIG, RATING_SCALE, UNIT_TO_LEVEL
from .naming import to_evaluator_name
from .spec import ROOT, Rubric, deployable, load_rubrics
from .transform import transform

LOCKFILE = ROOT / "evaluators.lock.json"

TIERS: dict[str, dict[str, Any]] = {
    "critical": {"sampling": 100.0, "judges": [
        "capability-honesty", "need-coverage", "actionability", "calibrated-hedging"]},
    "directional": {"sampling": 10.0, "judges": [
        "empathic-attunement", "emotional-accuracy", "conduct-adaptation",
        "signal-density", "plain-language-clarity", "effort-and-resolution-path"]},
}


@dataclass
class ModelConfig:
    """Which model AgentCore uses to run the judge.

    Two shapes exist and they are not equivalent:

    * `bedrockEvaluatorModelConfig` — `modelId` plus `inferenceConfig`
      (maxTokens, temperature [0,1], topP, stopSequences) and, crucially, a
      free-form `additionalModelRequestFields`. On Bedrock that is the standard
      escape hatch for provider-specific parameters, so Anthropic extended
      thinking plausibly goes there. **Unverified — test it before relying on
      it**; see docs/LIMITS.md.
    * `responsesEvaluatorModelConfig` — `modelId`, `maxOutputTokens`,
      `temperature [0,2]`, `topP`, and an explicit `reasoning.effort`. Reasoning
      control is first-class here, but this is the Responses-API shape.
    """

    model_id: str = "anthropic.claude-opus-4-5-20251101-v1:0"
    max_tokens: int = 8000
    temperature: float = 0.0
    # Where Anthropic thinking would go on the Bedrock shape. Empty by default
    # because it is unconfirmed; `--thinking` on the CLI populates it.
    additional_fields: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "modelId": self.model_id,
            "inferenceConfig": {"maxTokens": self.max_tokens,
                                "temperature": self.temperature},
        }
        if self.additional_fields:
            cfg["additionalModelRequestFields"] = self.additional_fields
        return {"bedrockEvaluatorModelConfig": cfg}


def evaluator_payload(rubric: Rubric, model: ModelConfig) -> dict[str, Any]:
    t = transform(rubric)
    return {
        "evaluatorName": to_evaluator_name(rubric.id),
        "description": f"{rubric.id} — helpfulness judge (native llmAsAJudge, {rubric.unit})",
        "level": UNIT_TO_LEVEL[rubric.unit],
        "evaluatorConfig": {
            "llmAsAJudge": {
                "instructions": t.instructions,
                "ratingScale": {"numerical": list(RATING_SCALE)},
                "modelConfig": model.to_payload(),
            }
        },
        "tags": {"suite": "helpfulness-judges", "judge": rubric.id,
                 "path": "native", "unit": rubric.unit},
    }


def plan(model: ModelConfig, *, prefer_traced: bool = False) -> list[dict[str, Any]]:
    return [evaluator_payload(r, model)
            for r in deployable(load_rubrics(), prefer_traced=prefer_traced)]


def online_config_payload(tier: str, log_groups: list[str], services: list[str],
                          role_arn: str, *, lock: dict[str, Any] | None = None,
                          enable_on_create: bool = False,
                          session_timeout_minutes: int = 30) -> dict[str, Any]:
    lock = lock if lock is not None else read_lock()
    cfg = TIERS[tier]
    ids = []
    for jid in cfg["judges"]:
        e = lock["judges"].get(jid)
        if not e:
            raise ValueError(f"{jid} is not in the lockfile — run `provision --apply` first")
        ids.append({"evaluatorId": e["evaluatorId"]})
    if len(ids) > MAX_EVALUATORS_PER_CONFIG:
        raise ValueError(f"tier {tier!r}: {len(ids)} evaluators exceeds the "
                         f"non-adjustable cap of {MAX_EVALUATORS_PER_CONFIG}")
    return {
        "onlineEvaluationConfigName": f"helpfulness_native_{tier}",
        "description": f"Helpfulness judges (native) — {tier} tier",
        "rule": {"samplingConfig": {"samplingPercentage": cfg["sampling"]},
                 "sessionConfig": {"sessionTimeoutMinutes": session_timeout_minutes}},
        "dataSourceConfig": {"cloudWatchLogs": {"logGroupNames": log_groups,
                                                "serviceNames": services}},
        "evaluators": ids,
        "evaluationExecutionRoleArn": role_arn,
        "enableOnCreate": enable_on_create,
        "tags": {"suite": "helpfulness-judges", "path": "native", "tier": tier},
    }


def read_lock() -> dict[str, Any]:
    return json.loads(LOCKFILE.read_text()) if LOCKFILE.exists() else {"judges": {}}


def write_lock(d: dict[str, Any]) -> None:
    LOCKFILE.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")


def validate_request(operation: str, params: dict[str, Any]) -> list[str]:
    """Strict validation: enums, patterns and bounds.

    `botocore.validate.validate_parameters` checks structure and types only — a
    `level` of "TURN" passes it. AWS shape patterns are also implicitly FULL
    matches, so this uses `re.fullmatch`; `re.match` would let `has-hyphens` pass
    `[a-zA-Z][a-zA-Z0-9_]{0,47}` on its prefix.
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
            if (pat := md.get("pattern")) and isinstance(value, str) and not re.fullmatch(pat, value):
                problems.append(f"{path}: {value!r} fails pattern {pat}")
            lo, hi = md.get("min"), md.get("max")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if lo is not None and value < lo:
                    problems.append(f"{path}: {value} below min {lo}")
                if hi is not None and value > hi:
                    problems.append(f"{path}: {value} above max {hi}")
            if isinstance(value, str):
                if lo is not None and len(value) < lo:
                    problems.append(f"{path}: shorter than min {lo}")
                if hi is not None and len(value) > hi:
                    problems.append(f"{path}: longer than max {hi}")

    walk(shape, params)
    return problems

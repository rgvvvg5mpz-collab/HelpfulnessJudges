"""Create and reconcile the evaluators and tasks in Arize AX.

Idempotent: re-running is safe and is how you deploy a rubric change. Each
judge's rendered template is hashed; an unchanged hash is a no-op, a changed hash
cuts a new evaluator VERSION with the git SHA as its commit message. The
resulting ids are written to `evaluators.lock.json`, which is the manifest CI
reconciles against.

Every API shape here was verified against the installed `arize` 8.50.0 wheel, not
against the docs — the two disagree in places. See docs/DESIGN.md.

`--dry-run` emits the exact request payloads as JSON and calls nothing, so the
whole deployment can be reviewed before it touches a tenant.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import SCALE_CHOICES
from .naming import to_ax_name
from .render import RenderedTemplate, render_all
from .spec import ROOT, Rubric, deployable, load_rubrics

LOCKFILE = ROOT / "evaluators.lock.json"


@dataclass
class LlmSettings:
    """Judge-model configuration.

    NOTE on `effort`: the parent suite runs every judge at Anthropic effort
    "high". `InvocationParamsRequest` exposes `thinking_level` (Gemini 3.x),
    `thinking_budget` (Gemini 2.5) and `reasoning_effort` (OpenAI o-series/GPT-5)
    — and nothing for Anthropic. Verified against the 8.50.0 wheel. There is no
    way to request Anthropic extended thinking through this surface. Read
    docs/LIMITS.md before choosing a model; this is the one unresolved issue that
    can change the answer to "should we do this at all".
    """

    ai_integration_id: str
    model_name: str = "claude-opus-5"
    temperature: float = 0.0
    max_tokens: int = 8000
    reasoning_effort: str | None = None  # OpenAI-family only; ignored by Anthropic


def git_sha(default: str = "unknown") -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout.strip() or default
    except Exception:
        return default


def template_config_kwargs(t: RenderedTemplate, llm: LlmSettings) -> dict[str, Any]:
    """The TemplateConfigInput payload for one judge, as plain data.

    Kept dict-shaped so `--dry-run` can print it and tests can assert on it
    without importing the Arize SDK.
    """
    invocation: dict[str, Any] = {"temperature": llm.temperature, "max_tokens": llm.max_tokens}
    if llm.reasoning_effort:
        invocation["reasoning_effort"] = llm.reasoning_effort
    return {
        "name": to_ax_name(t.judge_id),
        "template": t.template,
        # Required. Without it the forced tool has no `explanation` parameter at
        # all, and the structured payload has nowhere to go.
        "include_explanations": True,
        "use_function_calling": True,
        "use_structured_output": True,
        "classification_choices": dict(SCALE_CHOICES),
        "direction": "MAXIMIZE",
        "data_granularity": "SPAN",  # all judges, including conversation-unit ones
        "llm_config": {
            "ai_integration_id": llm.ai_integration_id,
            "model_name": llm.model_name,
            "invocation_parameters": invocation,
            "provider_parameters": {},
        },
    }


def plan(
    space: str,
    llm: LlmSettings,
    *,
    brace_style: str = "single",
    prefer_traced: bool = False,
) -> list[dict[str, Any]]:
    """Compute the desired state. Pure — no network, no SDK import."""
    rubrics = deployable(load_rubrics(), prefer_traced=prefer_traced)
    out = []
    for t in render_all(rubrics, brace_style):
        cfg = template_config_kwargs(t, llm)
        out.append({
            "judge_id": t.judge_id,
            "ax_name": cfg["name"],
            "unit": t.unit,
            "variable": t.variable,
            "template_hash": t.hash,
            "template_chars": t.chars,
            "space": space,
            "template_config": cfg,
        })
    return out


def read_lock() -> dict[str, Any]:
    return json.loads(LOCKFILE.read_text()) if LOCKFILE.exists() else {"judges": {}}


def write_lock(data: dict[str, Any]) -> None:
    LOCKFILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def apply(
    space: str,
    llm: LlmSettings,
    *,
    brace_style: str = "single",
    prefer_traced: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Reconcile Arize to the plan. `dry_run=True` calls nothing."""
    desired = plan(space, llm, brace_style=brace_style, prefer_traced=prefer_traced)
    lock = read_lock()
    sha = git_sha()
    actions: list[dict[str, Any]] = []

    client = None
    if not dry_run:
        from arize import ArizeClient  # lazy: keeps the offline commands dependency-free
        from arize._generated.api_client.models.template_config_input import TemplateConfigInput
        client = ArizeClient()

    for item in desired:
        jid = item["judge_id"]
        prev = lock["judges"].get(jid)
        if prev and prev.get("template_hash") == item["template_hash"]:
            actions.append({"judge_id": jid, "action": "unchanged",
                            "evaluator_id": prev.get("evaluator_id")})
            continue

        action = "create" if not prev else "new_version"
        record = {"judge_id": jid, "action": action, "template_hash": item["template_hash"],
                  "ax_name": item["ax_name"], "commit_message": f"judges@{sha}"}

        if dry_run:
            record["request"] = item["template_config"]
        else:
            cfg = TemplateConfigInput.from_dict(item["template_config"])
            if action == "create":
                res = client.evaluators.create_template_evaluator(
                    name=item["ax_name"], space=space,
                    commit_message=f"judges@{sha}", template_config=cfg,
                    description=f"{jid} — helpfulness judge suite, unit={item['unit']}",
                )
                record["evaluator_id"] = getattr(res, "id", None)
                record["evaluator_version_id"] = getattr(
                    getattr(res, "version", None), "id", None)
            else:
                res = client.evaluators.create_template_version(
                    evaluator=prev["evaluator_id"], space=space,
                    commit_message=f"judges@{sha}", template_config=cfg,
                )
                record["evaluator_id"] = prev["evaluator_id"]
                record["evaluator_version_id"] = getattr(res, "id", None)
            lock["judges"][jid] = {
                "evaluator_id": record["evaluator_id"],
                "evaluator_version_id": record["evaluator_version_id"],
                "ax_name": item["ax_name"],
                "unit": item["unit"],
                "variable": item["variable"],
                "template_hash": item["template_hash"],
                "commit": sha,
            }
        actions.append(record)

    if not dry_run:
        write_lock(lock)
    return {"space": space, "git_sha": sha, "dry_run": dry_run, "actions": actions}


def task_plan(
    project: str,
    tiers: dict[str, dict[str, Any]],
    *,
    lock: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the create_evaluation_task payloads, one per sampling tier.

    All evaluators sit at SPAN scope, so they *could* share one task — we split by
    tier only to sample them at different rates. Set the critical tier to exactly
    1.0 so its cohort is a guaranteed superset of the sampled tier; there is no
    seed or stratification to align cohorts otherwise.
    """
    lock = lock or read_lock()
    out = []
    for tier_name, cfg in tiers.items():
        evaluators = []
        for jid in cfg["judges"]:
            entry = lock["judges"].get(jid)
            if not entry:
                raise ValueError(f"{jid} is not in the lockfile — run `provision` first")
            if not entry.get("evaluator_version_id"):
                raise ValueError(
                    f"{jid} has no pinned version. A null evaluator_version_id means "
                    f"'always latest', so any Eval Hub edit silently changes what "
                    f"production runs mid-window."
                )
            evaluators.append({
                "evaluator_id": entry["evaluator_id"],
                "evaluator_version_id": entry["evaluator_version_id"],
                "column_mappings": {entry["variable"]: f"attributes.judge.{entry['variable']}"},
            })
        out.append({
            "name": f"helpfulness-judges-{tier_name}",
            "task_type": "TEMPLATE_EVALUATION",
            "project": project,
            "evaluators": evaluators,
            "is_continuous": True,
            "sampling_rate": cfg["sampling_rate"],
            "query_filter": cfg.get("query_filter", "span_kind = 'LLM'"),
        })
    return out

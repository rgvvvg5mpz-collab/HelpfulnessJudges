"""AWS Lambda handler backing every code-based AgentCore evaluator.

One Lambda serves all ten judges, dispatching on the evaluator name AgentCore
sends. That keeps deployment to a single artifact and a single warm pool, and
means the shared preamble stays warm in the Anthropic prompt cache across judges.

AgentCore invokes with roughly:

    {"evaluatorName": "signal_density",
     "evaluationInput": {"sessionSpans": [ ... ]},
     "evaluationTarget": {"traceIds": ["..."], "spanIds": ["..."]},
     "sessionId": "..."}

and requires back exactly:

    {"label": "0.5", "value": 0.5, "explanation": "<compact JSON payload>"}

`label` is required; `value` and `explanation` are optional. Everything the judge
produces beyond a score is serialised into `explanation` — the same compromise
the Arize sibling makes, with one important difference: here the payload was
schema-validated by Anthropic at generation time before it was serialised.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .judge import run
from .naming import to_judge_id
from .spans import from_session_spans, target_index
from .spec import load_rubrics

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Loaded once per container, not per invocation — rubric parsing is pure CPU and
# would otherwise be paid on every judged turn.
_RUBRICS = {r.id: r for r in load_rubrics()}

# Payload budget. AgentCore stores `explanation` on an OTel event; keep it small
# enough to stay readable in the CloudWatch console.
MAX_EXPLANATION = int(os.environ.get("MAX_EXPLANATION_CHARS", "9000"))


def _compact(verdict, conv, target: int | None) -> str:
    """Serialise the verdict into the one free-text field AgentCore gives us."""
    p = verdict.payload
    payload = {
        "v": 1,
        "j": verdict.judge_id,
        "trig": p.get("trigger_present"),
        "inj": p.get("injection_suspected"),
        "chk": [{"id": c["id"], "ok": c["verdict"], "sp": c.get("span", "")[:240]}
                for c in p.get("checks", [])],
        "ev": [e[:240] for e in (p.get("evidence") or [])[:6]],
        "why": (p.get("reasoning") or "")[:700],
        "conf": p.get("confidence"),
        # k=5 provenance. None of this survives into AgentCore's own fields, and
        # it is what the suite uses to decide whether to trust a verdict.
        "k": {"n": len(verdict.samples), "s": verdict.samples,
              "unan": verdict.unanimous, "hf": verdict.hard_failure_votes,
              "review": verdict.needs_human_review},
        "x": {k: v for k, v in p.items() if k not in (
            "trigger_present", "injection_suspected", "checks", "evidence",
            "reasoning", "score", "confidence")},
        "meta": {"session": conv.session_id, "target_turn": target,
                 "turns": len(conv.turns)},
    }
    out = json.dumps(payload, separators=(",", ":"), default=str)
    if len(out) > MAX_EXPLANATION:
        payload["ev"] = payload["ev"][:2]
        payload["chk"] = [{**c, "sp": c["sp"][:80]} for c in payload["chk"]]
        payload["truncated"] = True
        out = json.dumps(payload, separators=(",", ":"), default=str)
    return out[:MAX_EXPLANATION]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    name = event.get("evaluatorName") or event.get("evaluator_name") or ""
    judge_id = to_judge_id(name)
    rubric = _RUBRICS.get(judge_id)
    if rubric is None:
        raise ValueError(f"unknown evaluator {name!r} -> judge {judge_id!r}; "
                         f"known: {sorted(_RUBRICS)}")

    ei = event.get("evaluationInput") or {}
    conv = from_session_spans(ei.get("sessionSpans") or [],
                             session_id=str(event.get("sessionId") or ""))
    if not conv.turns:
        raise ValueError("no turns reconstructed from sessionSpans — check that "
                         "the chatbot emits LLM spans with input/output attributes")

    tgt = event.get("evaluationTarget") or {}
    if rubric.unit == "conversation":
        target, scope = None, "the whole conversation"
    else:
        target = target_index(conv, tgt.get("traceIds") or [])
        scope = "the assistant turn marked `>>> TARGET`"

    verdict = run(rubric, conv.render(target), scope)
    explanation = _compact(verdict, conv, target)

    log.info(json.dumps({
        "judge": judge_id, "score": verdict.score, "k": len(verdict.samples),
        "unanimous": verdict.unanimous, "review": verdict.needs_human_review,
        "explanation_chars": len(explanation), "sample_errors": len(verdict.errors),
    }))

    return {"label": verdict.label, "value": float(verdict.score),
            "explanation": explanation}

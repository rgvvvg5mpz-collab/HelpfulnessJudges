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

# Re-exported, not redefined. The return boundary used to live in this module,
# until the offline scorer needed it: importing this module runs the two lines
# below and parses ten rubrics, which is what a Lambda container wants and not
# what `agentcore-judges list` wants. `boundary` holds the one implementation and
# every consumer — this handler included — reaches it there.
from .boundary import MAX_EXPLANATION, SCOPE, _compact, agentcore_result  # noqa: F401
from .judge import run
from .naming import to_judge_id
from .spans import from_session_spans, target_index
from .spec import load_rubrics

# Root logger, deliberately: in a Lambda container this process serves nothing
# but this handler. It is also why nothing offline may import this module.
log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Loaded once per container, not per invocation — rubric parsing is pure CPU and
# would otherwise be paid on every judged turn.
_RUBRICS = {r.id: r for r in load_rubrics()}


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
        target = None
    else:
        target = target_index(conv, tgt.get("traceIds") or [])
    scope = SCOPE[rubric.unit]

    verdict = run(rubric, conv.render(target), scope)
    result = agentcore_result(verdict, conv, target)

    log.info(json.dumps({
        "judge": judge_id, "score": verdict.score, "k": len(verdict.samples),
        "unanimous": verdict.unanimous, "review": verdict.needs_human_review,
        "explanation_chars": len(result["explanation"]),
        "sample_errors": len(verdict.errors),
    }))

    return result

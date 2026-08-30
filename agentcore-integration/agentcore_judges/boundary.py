"""The AgentCore return boundary: what a verdict looks like once it leaves us.

This is the code the Lambda runs to collapse a `Verdict` into the three fields a
code-based evaluator may return, and it lives here rather than in `handler`
because the offline scorer needs it and `handler` is not free to import. Importing
`handler` runs its module scope, which reconfigures the ROOT logger from
`$LOG_LEVEL` and parses all ten rubrics — correct for a Lambda container, wrong
for `agentcore-judges list`. The scorer must reach the real boundary without
dragging the Lambda's startup behaviour into every CLI invocation, so the
boundary moved out and `handler` imports it back.

There is still exactly one implementation. `handler` re-exports these names, so
"the scorer compacts exactly as the handler does" is a fact about the import
graph rather than a claim about two copies staying in step.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Payload budget. AgentCore stores `explanation` on an OTel event; keep it small
# enough to stay readable in the CloudWatch console.
MAX_EXPLANATION = int(os.environ.get("MAX_EXPLANATION_CHARS", "9000"))

# What `{{scope}}` in the scoring tail resolves to, per rubric unit. Named here
# rather than inlined because the offline scorer has to send the same two strings
# — a judge scored against a scope it will never see in production is measuring
# something production does not run.
SCOPE = {"turn": "the assistant turn marked `>>> TARGET`",
         "conversation": "the whole conversation"}


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


def agentcore_result(verdict, conv, target: int | None) -> dict[str, Any]:
    """The exact dict the Lambda hands back, label and value included.

    `Verdict.label` indexes the three-point scale and `float()` insists the score
    is a number, so an off-scale score raises HERE, on the return statement —
    which is the truth about production and must stay the truth for anything
    claiming to measure it. The offline scorer calls this function rather than
    re-deriving a label from a private copy of the mapping; when it raises, the
    scorer records an unparseable item instead of inventing a label the Lambda
    could never have returned.
    """
    return {"label": verdict.label,
            "value": float(verdict.score),
            "explanation": _compact(verdict, conv, target)}

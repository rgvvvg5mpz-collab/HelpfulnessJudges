"""What the chatbot must write onto its spans for the judges to work.

This is the honest cost of running natively: Arize is no longer assembling the
conversation, your instrumentation is, so the tracing layer becomes part of the
eval contract.

The renderer is reproduced here character-for-character from the parent repo's
`harness/transcript.py`, because the rubrics and the shared preamble reference
its markers **by name** — `[BEGIN TRANSCRIPT]`, `--- turn N | CUSTOMER ---`,
` >>> TARGET`, `[tool trace: ...]`. If this rendering drifts, every rubric's
meaning drifts with it and no test will tell you.

`renderer_version` exists so you can tell a rendering change from a chatbot
change when scores move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RENDERER_VERSION = "transcript/1.0.0"

Role = Literal["customer", "assistant"]


@dataclass
class Turn:
    role: Role
    text: str
    tool_trace: list[str] = field(default_factory=list)


def render_transcript(turns: list[Turn], target_turn: int | None) -> str:
    """Byte-identical to harness/transcript.py::Conversation.render."""
    lines = ["[BEGIN TRANSCRIPT]"]
    for i, turn in enumerate(turns):
        marker = " >>> TARGET" if i == target_turn else ""
        label = "CUSTOMER" if turn.role == "customer" else "ASSISTANT"
        lines.append(f"\n--- turn {i} | {label}{marker} ---")
        lines.append(turn.text)
        if turn.tool_trace:
            lines.append("  [tool trace: " + "; ".join(turn.tool_trace) + "]")
        elif turn.role == "assistant":
            # The sentinel is load-bearing: capability-honesty-traced branches on
            # this exact string and drops to confidence: low. Never omit the line.
            lines.append("  [tool trace: none recorded]")
    return "\n".join(lines)


def span_attributes(
    turns: list[Turn],
    target_turn: int,
    conversation_id: str,
    *,
    is_final_turn: bool = False,
) -> dict[str, Any]:
    """Attributes to set on one assistant turn's span.

    Attach to the LLM span for that turn. `conversation_transcript` is only
    written on the final assistant turn — that is the span the two
    conversation-unit judges are filtered to.
    """
    attrs: dict[str, Any] = {
        "judge.transcript": render_transcript(turns, target_turn),
        "judge.target_turn": target_turn,
        "judge.conversation_id": conversation_id,
        "judge.renderer_version": RENDERER_VERSION,
        "judge.is_final_turn": is_final_turn,
    }
    if is_final_turn:
        attrs["judge.conversation_transcript"] = render_transcript(turns, None)
    return attrs


TOOL_TRACE_GUIDANCE = """\
Tool-trace entries must support two-directional reconciliation, because
capability-honesty-traced checks both what the turn CLAIMED and what the trace
shows it actually did — including work the turn silently dropped.

Each entry needs: call name, an args summary, the returned value OR the error,
and an explicit status. A call that errored, timed out, returned empty, or
returned partial data MUST be visibly marked as such — check C5 fails silently
if the trace only ever shows successes.

    get_balance(account=Roth IRA) -> $84,210
    get_contribution_ytd(year=2026) -> $4,500
    get_pending(2026) -> 1 pending $500 contribution
    get_plan_rules(plan=X) -> ERROR timeout

Where no calls were made, the renderer emits `[tool trace: none recorded]`.
That is a distinct state from "no calls happened" and the rubric treats it as
"the trace was not captured" — it scores under the untraced rules with
confidence: low.
"""

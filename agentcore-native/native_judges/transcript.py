"""Render a labelled conversation the way the rubrics were written to read one.

Deploying this path never needed a renderer: AgentCore collects the trace and
substitutes `{context}` itself, which is why no such module shipped. Scoring the
transformed prompts offline does need one, and the format is not free to invent.
The rubrics and `_preamble.md` were authored against `[BEGIN TRANSCRIPT]`,
`--- turn N | CUSTOMER ---`, ` >>> TARGET` and `  [tool trace: ...]`; render them
any other way and the offline number describes a different instrument than the
one the other two folders measure.

So the output here is byte-identical to the parent repo's `harness/transcript.py`
and to `agentcore-integration`'s span renderer. It is reimplemented rather than
imported because this folder has to keep working when copied somewhere with no
parent and no siblings.

One marker is load-bearing in a way that matters only here. `transform.py`
rewrites "the assistant turn marked `>>> TARGET`" into "the assistant turn under
evaluation", so the deployed prompt no longer says what `>>> TARGET` means. The
marker is still emitted — suppressing it would fork the format and forfeit the
comparison above — but nothing in this folder may *depend* on the model reading
it. `score.py` identifies the target turn by the placeholder the transformed
prompt actually names instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RENDERER_VERSION = "transcript/1.0.0"

Role = Literal["customer", "assistant"]

# The sentinel `capability-honesty-traced` branches on by exact string: a turn
# with no recorded tools is not the same evidence as a turn whose tools were
# never captured, and collapsing the two lets an unverifiable claim read as
# verified.
NO_TOOLS = "  [tool trace: none recorded]"


@dataclass(frozen=True)
class Turn:
    role: Role
    text: str
    tool_trace: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Conversation:
    id: str
    turns: list[Turn]

    def assistant_turn_indices(self) -> list[int]:
        return [i for i, t in enumerate(self.turns) if t.role == "assistant"]

    def render(self, target_turn: int | None) -> str:
        """Byte-identical to harness/transcript.py::Conversation.render."""
        lines = ["[BEGIN TRANSCRIPT]"]
        for i, turn in enumerate(self.turns):
            lines.append(render_turn(i, turn, target=i == target_turn))
        return "\n".join(lines)


def render_turn(index: int, turn: Turn, *, target: bool = False) -> str:
    """One turn's block, leading blank line included.

    Factored out of `render` so the scorer can reproduce a single turn in the
    same shape without re-deriving the header format — two renderings that drift
    apart inside one prompt would be worse than one wrong rendering.
    """
    marker = " >>> TARGET" if target else ""
    label = "CUSTOMER" if turn.role == "customer" else "ASSISTANT"
    lines = [f"\n--- turn {index} | {label}{marker} ---", turn.text]
    if turn.tool_trace:
        lines.append("  [tool trace: " + "; ".join(turn.tool_trace) + "]")
    elif turn.role == "assistant":
        lines.append(NO_TOOLS)
    return "\n".join(lines)


def conversation_from_record(record: dict[str, Any]) -> Conversation:
    """Lift one `data/testsets/*.jsonl` row into a conversation.

    `tool_trace` is absent on most customer turns and present-but-empty on many
    assistant ones, and those two mean the same thing here; normalising to a list
    at the boundary keeps the branch in `render_turn` about content rather than
    about which of two falsy shapes arrived.
    """
    return Conversation(
        id=record["id"],
        turns=[
            Turn(role=t["role"], text=t["text"], tool_trace=list(t.get("tool_trace") or []))
            for t in record["turns"]
        ],
    )

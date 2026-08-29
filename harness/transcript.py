"""Transcript rendering for judge prompts.

The rendering is deliberately boring and deliberately fixed: the judge prompts
reference `>>> TARGET` and the `[BEGIN TRANSCRIPT]` / `[END TRANSCRIPT]`
markers by name, so changing this format changes every rubric's meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["customer", "assistant"]


@dataclass(frozen=True)
class Turn:
    role: Role
    text: str
    tool_trace: list[str] | None = None


@dataclass(frozen=True)
class Conversation:
    id: str
    turns: list[Turn]
    metadata: dict[str, Any] | None = None

    def assistant_turn_indices(self) -> list[int]:
        return [i for i, t in enumerate(self.turns) if t.role == "assistant"]

    def render(self, target_turn: int | None) -> str:
        """Render for the judge. `target_turn` is a 0-based index into `turns`.

        Conversation-unit judges pass None; every assistant turn is then in
        scope and none is marked.
        """
        lines = ["[BEGIN TRANSCRIPT]"]
        for i, turn in enumerate(self.turns):
            marker = " >>> TARGET" if i == target_turn else ""
            label = "CUSTOMER" if turn.role == "customer" else "ASSISTANT"
            lines.append(f"\n--- turn {i} | {label}{marker} ---")
            lines.append(turn.text)
            if turn.tool_trace:
                lines.append("  [tool trace: " + "; ".join(turn.tool_trace) + "]")
            elif turn.role == "assistant":
                lines.append("  [tool trace: none recorded]")
        return "\n".join(lines)


def conversation_from_dict(raw: dict[str, Any]) -> Conversation:
    return Conversation(
        id=raw["id"],
        turns=[
            Turn(
                role=t["role"],
                text=t["text"],
                tool_trace=t.get("tool_trace"),
            )
            for t in raw["turns"]
        ],
        metadata=raw.get("metadata"),
    )

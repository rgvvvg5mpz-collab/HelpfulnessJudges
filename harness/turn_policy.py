"""Which turns each judge scores.

There is no gating in this design. Every judge scores every item of its declared
unit, and always returns 0, 0.5, or 1.0 — there is no not-applicable outcome and
no pre-filter deciding whether a judge runs.

That leaves exactly one decision here, and it is a sampling decision rather than
a scoring one:

* `unit: turn` judges score every assistant turn (`all`), or only the final one
  (`last`) when you are sampling production traffic for a trend and cannot afford
  full coverage.
* `unit: conversation` judges score the conversation once, always.

`last` is a coverage trade, not a gate: it changes how many items you measure,
never what a judge would say about an item it is given. Anything reported as a
per-dimension rate must state which mode produced it, because the two draw from
different populations — final turns skew toward resolutions and handoffs.
"""

from __future__ import annotations

from typing import Literal

from harness.judge_spec import JudgeSpec
from harness.transcript import Conversation

TurnMode = Literal["all", "last"]


def turns_to_score(
    spec: JudgeSpec,
    conv: Conversation,
    mode: TurnMode = "all",
) -> list[int | None]:
    """Target-turn indices to score, or `[None]` for a whole-conversation judge."""
    if spec.unit == "conversation":
        return [None]

    assistant_turns = conv.assistant_turn_indices()
    if not assistant_turns:
        return []
    if mode == "last":
        return [assistant_turns[-1]]
    return list(assistant_turns)

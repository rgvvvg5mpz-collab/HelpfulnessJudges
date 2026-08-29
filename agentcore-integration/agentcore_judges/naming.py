"""Judge id -> AgentCore evaluator name.

`CreateEvaluator.evaluatorName` matches `[a-zA-Z][a-zA-Z0-9_]{0,47}` — 48 chars,
letters/digits/underscore only, must start with a letter. **No hyphens.**

Every judge id in this suite is hyphenated, so this is not optional. It is the
same class of trap as the Arize integration's, but stricter and, mercifully,
enforced: AgentCore rejects a bad name with a ValidationException rather than
silently dropping the column the way Arize does.
"""

from __future__ import annotations

import re

EVALUATOR_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")


def to_evaluator_name(judge_id: str) -> str:
    name = judge_id.replace("-", "_")
    if not EVALUATOR_NAME.match(name):
        raise ValueError(
            f"judge id {judge_id!r} -> {name!r}, which fails AgentCore's "
            f"evaluatorName pattern {EVALUATOR_NAME.pattern} "
            f"({len(name)} chars). Rename the rubric."
        )
    return name


def to_judge_id(evaluator_name: str) -> str:
    return evaluator_name.replace("_", "-")

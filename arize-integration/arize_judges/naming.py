"""Judge id -> Arize eval name.

There is a genuine inconsistency inside the Arize SDK, and it is the single most
likely way to deploy this suite, see no error, and get no data:

    TemplateConfigInput.name   allows  ^[a-zA-Z0-9_\\s\\-&()]+$      (hyphens OK)
    _EVAL_NAME_REGEX           allows  [a-zA-Z0-9_\\s]+?             (no hyphens)

So the platform will happily accept an evaluator named `capability-honesty-traced`
and produce the column `eval.capability-honesty-traced.score` — which the SDK's
own column pattern then fails to match. On the write path that means the column
is dropped with an INFO log rather than an error.

Every judge id in this suite is hyphenated. We normalise to snake_case, which is
legal on both sides, and accept that Arize column names no longer match the
filenames in `rubrics/`. `ax_name_to_judge_id` exists so the export path can get
back.
"""

from __future__ import annotations

import re

# Mirrors arize.spans.columns._EVAL_NAME_REGEX, verified against arize 8.50.0.
SAFE_EVAL_NAME = re.compile(r"^[a-zA-Z0-9_\s]+$")


def to_ax_name(judge_id: str) -> str:
    """`capability-honesty-traced` -> `capability_honesty_traced`."""
    name = judge_id.replace("-", "_")
    if not SAFE_EVAL_NAME.match(name):
        raise ValueError(
            f"judge id {judge_id!r} normalises to {name!r}, which still fails "
            f"Arize's eval-name pattern {SAFE_EVAL_NAME.pattern}. Rename the rubric."
        )
    return name


def to_judge_id(ax_name: str) -> str:
    """Inverse of `to_ax_name`, for reading results back."""
    return ax_name.replace("_", "-")


def eval_columns(judge_id: str) -> dict[str, str]:
    """The three span columns a native eval writes for this judge."""
    n = to_ax_name(judge_id)
    return {
        "label": f"eval.{n}.label",
        "score": f"eval.{n}.score",
        "explanation": f"eval.{n}.explanation",
    }

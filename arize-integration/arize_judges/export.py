"""Read native eval results back, validate them, and expand the payload.

Why this exists: only `eval.<name>.label` and `eval.<name>.score` are first-class
in Arize. AX filter expressions support equality, comparison and AND/OR — no
substring, no LIKE, no regex, no JSON extraction. So "show me every span where
C4 failed" is not answerable inside Arize, in the Spans tab, in dashboards, in
monitors, or in AQL.

This job is the answer. It is a scheduled export, not a judge harness — Arize
still owns model invocation, sampling, cadence and write-back. It lands three
instrument-health metrics you cannot get any other way:

  payload_validity_rate      a judge degrades here before its scores move
  label_divergence_rate      the model's label vs what its own checks imply
  span_verification_failures confabulated evidence, and a class of injection
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .naming import eval_columns
from .spec import Rubric, deployable, load_rubrics
from .verify import Verdict, verify

TRANSCRIPT_COL = "attributes.judge.transcript"
CONVERSATION_COL = "attributes.judge.conversation_transcript"


def export_columns(rubrics: list[Rubric]) -> list[str]:
    """Columns to request from `client.spans.export_to_df`."""
    cols = ["context.span_id", "context.trace_id", "attributes.session.id",
            TRANSCRIPT_COL, CONVERSATION_COL, "attributes.judge.renderer_version"]
    for r in rubrics:
        cols.extend(eval_columns(r.id).values())
    return cols


def fetch(space_id: str, project_name: str, start, end, rubrics: list[Rubric]):
    """Pull scored spans. Lazily imports the SDK so offline commands stay usable."""
    from arize import ArizeClient

    return ArizeClient().spans.export_to_df(
        space_id=space_id, project_name=project_name,
        start_time=start, end_time=end, columns=export_columns(rubrics),
    )


@dataclass
class JudgeReport:
    judge_id: str
    scored: int = 0
    valid: int = 0
    diverged: int = 0
    span_failures: int = 0
    labels: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)

    @property
    def validity_rate(self) -> float:
        return self.valid / self.scored if self.scored else 0.0

    @property
    def divergence_rate(self) -> float:
        return self.diverged / self.valid if self.valid else 0.0


def expand(rows: Iterable[dict[str, Any]], rubrics: list[Rubric] | None = None):
    """Validate every payload and flatten the check vectors.

    Returns (check_rows, reports). `check_rows` is the per-check table that makes
    "every span where C4 failed" answerable in your own store.
    """
    rubrics = rubrics or deployable(load_rubrics())
    reports = {r.id: JudgeReport(r.id) for r in rubrics}
    check_rows: list[dict[str, Any]] = []

    for row in rows:
        for r in rubrics:
            cols = eval_columns(r.id)
            label = row.get(cols["label"])
            expl = row.get(cols["explanation"])
            if label is None and expl is None:
                continue
            rep = reports[r.id]
            rep.scored += 1
            rep.labels[str(label)] += 1

            transcript = row.get(
                CONVERSATION_COL if r.unit == "conversation" else TRANSCRIPT_COL
            )
            v: Verdict = verify(r, expl, str(label) if label is not None else None,
                                transcript=transcript)
            if not v.ok:
                for e in v.errors:
                    rep.errors[e.split("(")[0].split("—")[0].strip()[:60]] += 1
                continue
            rep.valid += 1
            if v.label_diverged:
                rep.diverged += 1
            rep.span_failures += sum(1 for w in v.warnings if "not found in transcript" in w)

            p = v.payload or {}
            for c in p.get("chk", []):
                check_rows.append({
                    "span_id": row.get("context.span_id"),
                    "trace_id": row.get("context.trace_id"),
                    "session_id": row.get("attributes.session.id"),
                    "judge_id": r.id,
                    "check_id": c.get("id"),
                    "ok": c.get("ok"),
                    "span": c.get("sp"),
                    "label": label,
                    "score": row.get(cols["score"]),
                    "bright_line": p.get("bl"),
                    "trigger_present": p.get("trig"),
                    "confidence": p.get("conf"),
                })
    return check_rows, reports


def summarise(reports: dict[str, JudgeReport]) -> str:
    lines = [f"{'judge':30} {'n':>5} {'valid':>7} {'diverged':>9} {'spanfail':>9}  labels"]
    lines.append("-" * 84)
    for jid, r in sorted(reports.items()):
        if not r.scored:
            continue
        dist = " ".join(f"{k}:{v}" for k, v in sorted(r.labels.items()))
        lines.append(
            f"{jid:30} {r.scored:>5} {r.validity_rate:>6.1%} "
            f"{r.divergence_rate:>9.1%} {r.span_failures:>9}  {dist}"
        )
    return "\n".join(lines)

"""Validate the compact payload the handler writes into `explanation`.

Note what is and is not being checked here, because it differs from the Arize
sibling. On this path Anthropic already enforced the verdict shape at generation
time via `output_config.format` json_schema, so the payload was well-formed
before it was serialised. This validates the SERIALISATION and the reconstruction
— that the compaction did not lose or mangle anything, that the label agrees with
the checks, and that quoted evidence exists in the transcript.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import LABELS
from .spec import Rubric


@dataclass
class Result:
    judge_id: str
    ok: bool = False
    payload: dict[str, Any] | None = None
    label: str | None = None
    recomputed: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def diverged(self) -> bool:
        return bool(self.label and self.recomputed and self.label != self.recomputed)


def recompute_label(payload: dict[str, Any]) -> str | None:
    """What the mapping rule implies, from the payload's own checks.

    The compact payload has no `bl` field: on this path the score came from a
    schema-validated Anthropic response, so the bright-line decision lives in the
    reasoning rather than a dedicated field. All-pass therefore implies 1.0, and
    anything else is 0.5-or-0 — which is why a 0 is only ever *warned* about here
    rather than treated as an error.
    """
    chk = payload.get("chk")
    if not isinstance(chk, list) or not chk:
        return None
    try:
        return "1.0" if all(bool(c["ok"]) for c in chk) else "0.5"
    except (KeyError, TypeError):
        return None


def verify(rubric: Rubric, explanation: str | None, label: str | None = None,
           *, transcript: str | None = None) -> Result:
    r = Result(judge_id=rubric.id, label=label)
    if not explanation:
        r.errors.append("empty explanation — the handler emitted no payload")
        return r
    try:
        p = json.loads(explanation)
    except json.JSONDecodeError as e:
        r.errors.append(f"explanation is not JSON ({e.msg} at {e.pos})")
        return r
    r.payload = p

    if label is not None and label not in LABELS:
        r.errors.append(f"label {label!r} not in {LABELS}")
    if p.get("j") not in (None, rubric.id):
        r.errors.append(f"payload claims judge {p.get('j')!r}, expected {rubric.id!r}")

    chk = p.get("chk")
    if not isinstance(chk, list):
        r.errors.append("`chk` missing or not a list")
    else:
        ids = [c.get("id") for c in chk if isinstance(c, dict)]
        if ids != rubric.check_ids:
            r.errors.append(f"check ids {ids} != declared {rubric.check_ids}")
        for c in chk:
            if isinstance(c, dict) and not isinstance(c.get("ok"), bool):
                r.errors.append(f"check {c.get('id')}: ok is {c.get('ok')!r}, not a boolean")

    for name in rubric.extra_field_names:
        if name not in (p.get("x") or {}):
            r.warnings.append(f"x.{name} missing (rubric declares it)")

    kb = p.get("k") or {}
    if kb:
        n, samples = kb.get("n"), kb.get("s") or []
        if n != len(samples):
            r.errors.append(f"k.n={n} but {len(samples)} samples recorded")
        if kb.get("review") and label != "0":
            r.warnings.append("flagged for human review — spread, low confidence, or injection")
    else:
        r.warnings.append("no k provenance — was this scored at k=1?")

    if p.get("truncated"):
        r.warnings.append("payload was truncated to fit the explanation budget")

    r.recomputed = recompute_label(p)
    if r.diverged:
        if r.label == "0":
            # Legitimate, and the whole meaning of a bright line: it forces 0
            # regardless of how the checks came out. Surface it, do not fail it —
            # `why` should name which line fired, and that is a human read.
            r.warnings.append(
                f"label 0 with checks implying {r.recomputed!r} — confirm `why` names "
                f"the bright line that fired"
            )
        else:
            r.errors.append(
                f"label {r.label!r} disagrees with the payload's checks "
                f"(implies {r.recomputed!r})"
            )

    if transcript:
        for span in (p.get("ev") or []):
            s = str(span or "").strip()
            if s and s not in transcript:
                r.warnings.append(f"evidence span not in transcript: {s[:60]!r}")

    r.ok = not r.errors
    return r

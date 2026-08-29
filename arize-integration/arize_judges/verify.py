"""Validate a judge payload and recompute its label.

This module replaces something the native path takes away. On the Anthropic
path the parent harness enforces the verdict shape with a JSON schema:
`additionalProperties: false`, `minItems == maxItems == len(check_ids)`, check
ids enumerated, verdicts real booleans, score restricted to the three levels.

Arize's `explanation` is an unconstrained string. Nothing validates what the
model writes into it. So a judge that emits five checks instead of seven, or
invents a check id, or writes `trace_support: "unclear"`, now fails *silently*
and lands on the span looking perfectly normal.

Run this over every payload you read back. Two of its outputs are judge-integrity
metrics you did not have before:

  * **payload validity rate** — a judge whose payloads start failing to parse is
    degrading before its scores move.
  * **label divergence rate** — the model chose a label; `recompute_label` derives
    one from the model's own `bl` and `chk`. Disagreement means the judge is not
    applying its own mapping rule, which no score-only view would ever surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .spec import Rubric

LABELS = ("1.0", "0.5", "0")

ENUMS: dict[str, tuple[str, ...]] = {
    "conf": ("high", "medium", "low"),
    "uncertainty_state": ("reducible", "irreducible", "none", "mixed"),
    "removable_fraction": ("none", "under_20pct", "20_to_40pct", "over_40pct", "mostly"),
    "resolution_state": (
        "resolved_in_channel", "clean_handoff", "one_external_step",
        "unresolved_with_path", "dead_end", "doom_loop",
    ),
    "trace_support": ("exact", "partial", "absent", "contradicted"),
}

LIMITS = {"ev_items": 6, "span_chars": 240, "why_chars": 700, "x_rows": 10, "total_chars": 10_000}


@dataclass
class Verdict:
    judge_id: str
    ok: bool = False
    payload: dict[str, Any] | None = None
    label: str | None = None
    recomputed_label: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def label_diverged(self) -> bool:
        return (
            self.label is not None
            and self.recomputed_label is not None
            and self.label != self.recomputed_label
        )


def recompute_label(payload: dict[str, Any]) -> str | None:
    """Derive the label the rubric's mapping rule implies from the payload.

    Bright line fired -> "0". Otherwise all checks pass -> "1.0". Otherwise "0.5".
    Returns None if the payload lacks what is needed to decide.
    """
    if "chk" not in payload or not isinstance(payload["chk"], list):
        return None
    if payload.get("bl"):
        return "0"
    try:
        return "1.0" if all(bool(c["ok"]) for c in payload["chk"]) else "0.5"
    except (KeyError, TypeError):
        return None


def verify(
    rubric: Rubric,
    explanation: str | None,
    label: str | None = None,
    *,
    transcript: str | None = None,
) -> Verdict:
    """Parse and check one payload. `transcript` enables evidence-span verification."""
    v = Verdict(judge_id=rubric.id, label=label)

    if not explanation:
        v.errors.append("empty explanation — no payload emitted")
        return v
    if len(explanation) > LIMITS["total_chars"]:
        v.warnings.append(
            f"payload {len(explanation)} chars exceeds the {LIMITS['total_chars']} budget"
        )

    text = explanation.strip()
    if text.startswith("```"):
        v.warnings.append("payload was fenced; the tail forbids code fences")
        text = text.strip("`").lstrip("json").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        v.errors.append(f"not valid JSON ({e.msg} at char {e.pos}) — likely prose, not a payload")
        return v
    if not isinstance(payload, dict):
        v.errors.append(f"payload is {type(payload).__name__}, expected an object")
        return v
    v.payload = payload

    for forbidden in ("label", "explanation"):
        if forbidden in payload:
            v.errors.append(
                f"payload contains the key {forbidden!r}, which the free-text "
                f"fallback parser would use to shred it"
            )

    if payload.get("j") not in (None, rubric.id):
        v.errors.append(f"payload claims judge {payload.get('j')!r}, expected {rubric.id!r}")

    # checks: exactly the declared ids, in order, with real booleans
    chk = payload.get("chk")
    if not isinstance(chk, list):
        v.errors.append("`chk` missing or not a list")
    else:
        got = [c.get("id") for c in chk if isinstance(c, dict)]
        if got != rubric.check_ids:
            v.errors.append(f"check ids {got} != declared {rubric.check_ids}")
        for c in chk:
            if not isinstance(c, dict):
                v.errors.append(f"check entry is {type(c).__name__}, expected object")
                continue
            if not isinstance(c.get("ok"), bool):
                v.errors.append(f"check {c.get('id')}: `ok` is {c.get('ok')!r}, expected a boolean")
            if len(str(c.get("sp", ""))) > LIMITS["span_chars"]:
                v.warnings.append(f"check {c.get('id')}: span exceeds {LIMITS['span_chars']} chars")

    for key in ("trig", "inj"):
        if key in payload and not isinstance(payload[key], bool):
            v.errors.append(f"`{key}` is {payload[key]!r}, expected a boolean")

    if payload.get("conf") not in ENUMS["conf"]:
        v.errors.append(f"`conf` is {payload.get('conf')!r}, expected one of {ENUMS['conf']}")

    ev = payload.get("ev", [])
    if not isinstance(ev, list):
        v.errors.append("`ev` is not a list")
    elif len(ev) > LIMITS["ev_items"]:
        v.warnings.append(f"`ev` has {len(ev)} items, budget is {LIMITS['ev_items']}")

    if len(str(payload.get("why", ""))) > LIMITS["why_chars"]:
        v.warnings.append(f"`why` exceeds {LIMITS['why_chars']} chars")

    # per-judge extra fields, and their enums
    x = payload.get("x") or {}
    if not isinstance(x, dict):
        v.errors.append("`x` is not an object")
        x = {}
    for want in rubric.extra_field_names:
        if want not in x:
            v.warnings.append(f"`x.{want}` missing (rubric declares it)")
    for key, allowed in ENUMS.items():
        if key in x and x[key] not in allowed:
            v.errors.append(f"`x.{key}` is {x[key]!r}, expected one of {allowed}")
    for row in x.get("trace_claims", []) or []:
        if isinstance(row, dict) and row.get("trace_support") not in ENUMS["trace_support"]:
            v.errors.append(
                f"trace_claims[].trace_support is {row.get('trace_support')!r}, "
                f"expected one of {ENUMS['trace_support']}"
            )

    # bright line <-> label coherence
    bl = payload.get("bl")
    if label == "0" and not bl:
        v.errors.append("label is 0 but `bl` is null — a 0 must name the bright line that fired")
    if bl and label not in (None, "0"):
        v.errors.append(f"`bl` names a fired bright line but label is {label!r}, expected 0")

    v.recomputed_label = recompute_label(payload)
    if v.label_diverged:
        v.errors.append(
            f"label {v.label!r} disagrees with the payload's own checks "
            f"(implies {v.recomputed_label!r}) — the judge is not applying its mapping rule"
        )

    # evidence-span verification: every quoted span must exist in the transcript.
    # This is the check the parent repo's validation protocol lists as
    # "not yet implemented and worth building first" — it is cheap here because
    # both sides sit on the same span.
    if transcript:
        # `ev` is defined as verbatim spans, so verify it at any length.
        for span in (ev if isinstance(ev, list) else []):
            s = str(span or "").strip()
            if s and s not in transcript:
                v.warnings.append(f"evidence span not found in transcript: {s[:60]!r}")
        # `chk[].sp` may legitimately be a stated absence ("no acknowledgment
        # anywhere") rather than a quote, and the two are not distinguishable by
        # length. Only flag a miss when the text reads like a quotation — it is
        # long and does not look like a sentence about an absence.
        for c in (chk or []):
            if not isinstance(c, dict):
                continue
            s = str(c.get("sp") or "").strip()
            if not s or s in transcript:
                continue
            looks_like_absence = len(s) < 60 and any(
                w in s.lower() for w in ("none", "no ", "absent", "not ", "n/a", "did not")
            )
            if not looks_like_absence:
                v.warnings.append(
                    f"check {c.get('id')} span not found in transcript: {s[:60]!r}"
                )

    v.ok = not v.errors
    return v

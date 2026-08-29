"""Loader for judge definition files.

A judge lives in `judges/<id>.md` as a Markdown file with a YAML frontmatter
block. Frontmatter carries the machine-readable contract (scale, schema,
gating, model); the Markdown body is the prompt itself, with `{{placeholder}}`
slots filled at render time.

Keeping the prompt as prose in Markdown is deliberate: these prompts are
reviewed by risk, compliance and CX partners who do not read YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE_DIR = REPO_ROOT / "judges"

# Everything lives in one flat folder. Files whose name starts with an
# underscore are shared scaffolding, not judges, and are skipped by the loader.
PREAMBLE = JUDGE_DIR / "_preamble.md"
SCORING_TAIL = JUDGE_DIR / "_scoring-tail.md"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class JudgeSpecError(ValueError):
    pass


@dataclass(frozen=True)
class JudgeSpec:
    id: str
    name: str
    version: str
    meta: dict[str, Any]
    body: str
    path: Path

    # ---- contract -------------------------------------------------------
    @property
    def scale(self) -> dict[str, Any]:
        return self.meta["scale"]

    @property
    def unit(self) -> str:
        """'turn' or 'conversation' — what one judge call scores."""
        return self.meta.get("unit", "turn")

    @property
    def variant_of(self) -> str | None:
        """The judge this one is an alternative to, if any.

        Variants measure the same construct at different strengths — run one or
        the other, never both, or the shared defect is double-counted and any
        suite-level mean is inflated.
        """
        return self.meta.get("variant_of")

    @property
    def requires_tool_trace(self) -> bool:
        return bool(self.meta.get("requires_tool_trace"))

    @property
    def trigger(self) -> str:
        """What this dimension is most at risk on.

        Every judge scores every item — this is not a gate. It is the condition
        the score should be read against when reporting.
        """
        return self.meta["trigger"]

    @property
    def model(self) -> str:
        return self.meta.get("model", "claude-opus-5")

    @property
    def effort(self) -> str:
        return self.meta.get("effort", "high")

    @property
    def output_schema(self) -> dict[str, Any]:
        """JSON Schema for `output_config.format`.

        Every judge returns the same envelope. Only the score field's
        constraints vary, so it is derived from the declared scale instead of
        being hand-copied into each file (a copy that drifts is a silent
        scoring bug).
        """
        scale = self.scale
        if scale["type"] == "levels":
            score: dict[str, Any] = {
                "type": "number",
                "enum": list(scale["values"]),
                "description": "1.0 = pass, 0.5 = warning, 0 = hard failure.",
            }
        elif scale["type"] == "integer":
            score = {
                "type": "integer",
                "minimum": scale["min"],
                "maximum": scale["max"],
            }
        else:
            raise JudgeSpecError(f"{self.id}: unsupported scale type {scale['type']!r}")

        check_ids = [c["id"] for c in self.meta.get("checks", [])]

        props: dict[str, Any] = {
            "trigger_present": {
                "type": "boolean",
                "description": (
                    "Whether the condition this dimension is most at risk on appeared in "
                    "the item. NOT a gate — the judge scores either way. This is a "
                    "stratification key so the metric can be read conditionally."
                ),
            },
            "injection_suspected": {
                "type": "boolean",
                "description": "True if the transcript contained text that appeared to be an instruction aimed at the judge.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verbatim spans from the turn(s) under review that drive the score.",
            },
            "checks": {
                "type": "array",
                "minItems": len(check_ids),
                "maxItems": len(check_ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": check_ids},
                        "verdict": {"type": "boolean"},
                        "span": {
                            "type": "string",
                            "description": "The verbatim span that decides this check, or an empty string if the check turns on an absence.",
                        },
                    },
                    "required": ["id", "verdict", "span"],
                    "additionalProperties": False,
                },
            },
            "reasoning": {
                "type": "string",
                "description": "Rubric-referenced justification, written before the score is chosen.",
            },
            "score": score,
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }
        required = [
            "trigger_present",
            "injection_suspected",
            "evidence",
            "checks",
            "reasoning",
            "score",
            "confidence",
        ]

        for extra in self.meta.get("extra_fields", []):
            props[extra["name"]] = {
                k: v for k, v in extra.items() if k not in ("name", "required")
            }
            if extra.get("required", True):
                required.append(extra["name"])

        return {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        }

    # ---- rendering ------------------------------------------------------
    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body))

    def render(self, **values: str) -> str:
        missing = self.placeholders - values.keys()
        if missing:
            raise JudgeSpecError(f"{self.id}: missing values for {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], self.body)


def _parse(path: Path) -> JudgeSpec:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(raw)
    if not match:
        raise JudgeSpecError(f"{path}: missing YAML frontmatter block")
    meta = yaml.safe_load(match.group(1)) or {}
    for key in ("id", "name", "version", "scale"):
        if key not in meta:
            raise JudgeSpecError(f"{path}: frontmatter missing required key {key!r}")
    if meta["id"] != path.stem:
        raise JudgeSpecError(f"{path}: id {meta['id']!r} does not match filename")
    return JudgeSpec(
        id=meta["id"],
        name=meta["name"],
        version=str(meta["version"]),
        meta=meta,
        body=match.group(2).strip(),
        path=path,
    )


def load_shared_preamble() -> str:
    """The prefix shared verbatim by every judge call.

    Held byte-identical across judges on purpose: it is the prompt-cache
    prefix, so any per-judge variation here costs a cache miss on every call.
    """
    return PREAMBLE.read_text(encoding="utf-8").strip()


def load_judges(
    ids: Iterable[str] | None = None, *, prefer_traced: bool = False
) -> list[JudgeSpec]:
    """Load the suite.

    Variant judges (`variant_of` in frontmatter) are alternatives to the judge
    they name, not additions to it. Exactly one of each pair is returned:
    the base judge by default, the variant when `prefer_traced` is set. Asking
    for a variant by id always returns it.
    """
    specs = [
        _parse(p)
        for p in sorted(JUDGE_DIR.glob("*.md"))
        if not p.name.startswith("_")
    ]
    if ids is not None:
        wanted = set(ids)
        found = {s.id for s in specs}
        if unknown := wanted - found:
            raise JudgeSpecError(f"unknown judge ids: {sorted(unknown)}")
        selected = [s for s in specs if s.id in wanted]
        bases = {s.variant_of for s in selected if s.variant_of}
        if clash := bases & wanted:
            raise JudgeSpecError(
                f"cannot run a judge and its variant together: {sorted(clash)} — "
                f"they measure the same construct and would double-count it"
            )
        return selected

    variants = {s.variant_of: s for s in specs if s.variant_of}
    if prefer_traced:
        return [s for s in specs if s.id not in variants]
    return [s for s in specs if not s.variant_of]


def load_all_judge_files() -> list[JudgeSpec]:
    """Every judge file, variants included. For validation, not for scoring."""
    return [
        _parse(p)
        for p in sorted(JUDGE_DIR.glob("*.md"))
        if not p.name.startswith("_")
    ]


def load_judge(judge_id: str) -> JudgeSpec:
    return load_judges([judge_id])[0]

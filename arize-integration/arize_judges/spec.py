"""Standalone rubric loader.

A deliberately independent reimplementation of the parent repo's judge_spec, so
this folder works on its own. It reads the vendored `rubrics/*.md` and exposes
what the renderer and verifier need. It does NOT build an Anthropic JSON schema —
on the Arize path there is no schema to enforce (see docs/LIMITS.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RUBRIC_DIR = ROOT / "rubrics"
PREAMBLE = RUBRIC_DIR / "_preamble.md"
TAIL_AX = RUBRIC_DIR / "_scoring-tail-ax.md"
PROVENANCE = RUBRIC_DIR / "RUBRIC_PROVENANCE.json"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Rubric:
    id: str
    name: str
    version: str
    unit: str
    tier: str
    trigger: str
    checks: list[dict[str, str]]
    extra_fields: list[dict[str, Any]]
    variant_of: str | None
    body: str
    path: Path

    @property
    def check_ids(self) -> list[str]:
        return [c["id"] for c in self.checks]

    @property
    def extra_field_names(self) -> list[str]:
        return [f["name"] for f in self.extra_fields]

    @property
    def bright_lines(self) -> list[str]:
        """The named hard-failure conditions, lifted from the rubric prose.

        These are what the model must cite in the payload's `bl` field, and what
        `verify` checks a 0 against.
        """
        m = re.search(r"\*\*0 — Hard failure\.\*\*(.*?)(?=\n## |\Z)", self.body, re.S)
        if not m:
            return []
        return [
            " ".join(x.split())
            for x in re.findall(r"^- (.+?)(?=\n- |\n\n|\Z)", m.group(1).strip(), re.M | re.S)
        ]


def _parse(path: Path) -> Rubric:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(raw)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    return Rubric(
        id=meta["id"],
        name=meta["name"],
        version=str(meta["version"]),
        unit=meta.get("unit", "turn"),
        tier=meta.get("tier", ""),
        trigger=" ".join(str(meta.get("trigger", "")).split()),
        checks=meta.get("checks", []),
        extra_fields=meta.get("extra_fields", []),
        variant_of=meta.get("variant_of"),
        body=m.group(2).strip(),
        path=path,
    )


def load_rubrics(ids: list[str] | None = None) -> list[Rubric]:
    out = [
        _parse(p)
        for p in sorted(RUBRIC_DIR.glob("*.md"))
        if not p.name.startswith("_")
    ]
    if ids is not None:
        want = set(ids)
        found = {r.id for r in out}
        if missing := want - found:
            raise ValueError(f"unknown judge ids: {sorted(missing)}")
        out = [r for r in out if r.id in want]
    return out


def load_preamble() -> str:
    return PREAMBLE.read_text(encoding="utf-8").strip()


def load_tail() -> str:
    return TAIL_AX.read_text(encoding="utf-8").strip()


def deployable(rubrics: list[Rubric], prefer_traced: bool = False) -> list[Rubric]:
    """Drop one side of every variant pair.

    Arize has no equivalent of the parent harness's mutual-exclusion guard:
    attaching both a judge and its variant to a task silently double-counts the
    same defect and inflates any suite-level mean. We enforce it here, at
    provisioning time, because nothing downstream will.
    """
    variants = {r.variant_of: r for r in rubrics if r.variant_of}
    if prefer_traced:
        return [r for r in rubrics if r.id not in variants]
    return [r for r in rubrics if not r.variant_of]


def check_drift(source_dir: Path | None = None) -> list[str]:
    """Re-hash the originals and report any that moved. Empty list == in sync."""
    prov = json.loads(PROVENANCE.read_text())
    src = source_dir or (ROOT.parent / "judges")
    problems: list[str] = []
    if not src.exists():
        return [f"source rubrics not found at {src} — cannot verify (this folder still works)"]
    for fname, expected in prov["files"].items():
        p = src / fname
        if not p.exists():
            problems.append(f"{fname}: deleted from {src}")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        if actual != expected:
            problems.append(f"{fname}: changed upstream ({expected} -> {actual})")
    return problems

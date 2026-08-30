#!/usr/bin/env python3
"""Cross-folder vendoring checks — the view no single deployment folder has.

    python3 tools/check_vendoring.py             # table, then details for what is wrong
    python3 tools/check_vendoring.py --quiet     # prints nothing on success; for CI
    python3 tools/check_vendoring.py --json      # machine-readable report

Sibling of harness/validate_suite.py. That tool asks whether one rubric is
internally coherent; this one asks whether the four copies of the suite still
agree with each other.

WHY THIS EXISTS
---------------
Every deployment folder ships a `check-drift` verb — see
arize-integration/arize_judges/spec.py::check_drift, which the other two
duplicate almost verbatim. It re-hashes ../judges/<name> and compares against
the hash recorded in rubrics/RUBRIC_PROVENANCE.json. That answers exactly one
question, "did the SOURCE move since I vendored?", and leaves the other half
unasked. The recorded hash is equally a hash of the vendored copy — at
vendoring time the two files were byte-identical — so the same record could
answer "did the COPY move?", and nothing does.

Verified, not hypothesised: append a line to
agentcore-integration/rubrics/actionability.md and `bin/agentcore-judges
check-drift` still prints "in sync with ../judges" and exits 0, while the
rubric that would actually deploy no longer matches the reviewed one.

The two directions do not mean the same thing and are never reported as one
number here:

    source-moved   judges/ changed after vendoring. Mundane. Re-vendor.
    copy-moved     someone edited a deployed rubric in place. The review that
                   approved judges/ never saw that text, and the folder's own
                   check-drift will keep calling it in sync forever.
    both-moved     both ends changed; a re-vendor would overwrite a local edit.

The root is also the only vantage point that sees all four implementations at
once. A folder deleted, or a rubric that one folder never vendored, is
invisible from inside any single folder: there is nothing there to notice the
absence. Same for the shared corpus — data/testsets and data/gold are copied
into every folder with no provenance record at all.

Read-only by construction. Nothing in judges/, data/ or the three deployment
folders is opened for writing; --root exists so the checker can be pointed at a
throwaway copy for testing without touching the real tree.

Standard library only, and no imports from harness/ — this must run on a bare
python3 in CI, before any dependency is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# The three standalone deployment paths. Named explicitly rather than
# discovered: a folder that vanishes must be a finding, and a glob cannot miss
# what is no longer there.
FOLDERS = ("agentcore-integration", "arize-integration", "agentcore-native")

# Editor and interpreter debris that lives in the tree but is not content.
IGNORE_NAMES = {".DS_Store", "__pycache__", ".gitignore"}

# 16 hex chars of sha256 — the same truncation RUBRIC_PROVENANCE.json uses, so
# anything this tool prints can be eyeballed against the recorded value.
HASH_LEN = 16


# ---------------------------------------------------------------------------
# Intended divergences
# ---------------------------------------------------------------------------
# Not every difference is drift. Two of the three folders deliberately refuse a
# file, one folder deliberately rewrote a doc, and every folder generates a file
# that has no upstream at all. Those must not be reported as problems.
#
# They must also not be silent. Divergence that nobody can see is how a
# deliberate exception becomes an unexamined one, so every entry here is PRINTED
# on every run under "intended divergences (not drift)", and every entry is
# re-checked against reality: an entry whose divergence has disappeared is
# itself reported, because a stale allowlist is a hole that will swallow real
# drift later.
#
# kinds:
#   not_vendored    a file in judges/ this folder deliberately does not copy.
#                   Stale if the source file is gone, or if the folder has
#                   started vendoring it after all.
#   local_only      a file in the folder with no counterpart in judges/, by
#                   design. Stale if it is gone, or if judges/ grew a file of
#                   that name (it is then a real vendoring relationship).
#   divergent_copy  a file that IS vendored but deliberately differs, pinned to
#                   the exact copy hash that was reviewed. The pin is the point:
#                   the known difference is excused, any FURTHER edit is not.
#                   Stale if the two ends became identical.
ALLOWLIST: tuple[dict[str, str], ...] = (
    {
        "folder": "arize-integration",
        "path": "rubrics/_scoring-tail.md",
        "kind": "not_vendored",
        "reason": "AX classification evaluators cannot emit the suite's output "
                  "contract, so the tail is replaced rather than copied. See "
                  "arize-integration/docs/LIMITS.md.",
    },
    {
        "folder": "arize-integration",
        "path": "rubrics/_scoring-tail-ax.md",
        "kind": "local_only",
        "reason": "The replacement tail, rewritten for AX's label/explanation "
                  "output. Has no upstream in judges/ by design.",
    },
    {
        "folder": "agentcore-native",
        "path": "rubrics/_scoring-tail.md",
        "kind": "not_vendored",
        "reason": "AgentCore native llmAsAJudge appends its own standardization "
                  "prompt forcing {reasoning, score}; transform.py writes that "
                  "contract at deploy time. Vendoring a tail that prescribes a "
                  "different contract would only conflict with it.",
    },
    {
        "folder": "agentcore-integration",
        "path": "rubrics/RUBRIC_PROVENANCE.json",
        "kind": "local_only",
        "reason": "Generated at vendoring time. It is the record of the copy, "
                  "not part of it.",
    },
    {
        "folder": "arize-integration",
        "path": "rubrics/RUBRIC_PROVENANCE.json",
        "kind": "local_only",
        "reason": "Generated at vendoring time. It is the record of the copy, "
                  "not part of it.",
    },
    {
        "folder": "agentcore-native",
        "path": "rubrics/RUBRIC_PROVENANCE.json",
        "kind": "local_only",
        "reason": "Generated at vendoring time. It is the record of the copy, "
                  "not part of it.",
    },
    {
        "folder": "agentcore-integration",
        "path": "data/testsets/README.md",
        "kind": "divergent_copy",
        "pin": "86318f291fd4ba58",
        "reason": "Deliberately re-written for this folder: the root version "
                  "tells the reader to run `harness.testsets`, which does not "
                  "exist here, so the commands were replaced with the folder's "
                  "own `bin/agentcore-judges` verbs. Documentation, not corpus "
                  "— no testset row differs. Pinned: any further edit to this "
                  "copy reappears as drift.",
    },
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:HASH_LEN]


def listdir(d: Path) -> list[str]:
    """Content file names in a directory, debris excluded."""
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.name not in IGNORE_NAMES and p.is_file())


def walk(d: Path) -> list[str]:
    """Relative paths of every content file under a directory, debris excluded."""
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        if any(part in IGNORE_NAMES for part in p.relative_to(d).parts):
            continue
        out.append(p.relative_to(d).as_posix())
    return out


class Report:
    """Collected findings. Problems fail the run; notes are printed, not fatal."""

    def __init__(self) -> None:
        self.problems: list[dict[str, Any]] = []
        self.intended: list[dict[str, Any]] = []
        self.folders: dict[str, dict[str, Any]] = {}

    def problem(self, folder: str, path: str, kind: str, detail: str,
                direction: str | None = None) -> None:
        self.problems.append({
            "folder": folder, "path": path, "kind": kind,
            "direction": direction, "detail": detail,
        })
        if folder in self.folders:
            self.folders[folder]["problems"] += 1

    def note(self, folder: str, path: str, kind: str, reason: str) -> None:
        self.intended.append({
            "folder": folder, "path": path, "kind": kind, "reason": reason,
        })
        if folder in self.folders:
            self.folders[folder]["intended"] += 1


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_rubrics(root: Path, folder: str, allow: dict[str, dict[str, str]],
                  used: set[str], rep: Report) -> None:
    """Both directions of rubric drift, plus coverage and provenance integrity.

    The direction is derived from three hashes, not two. `recorded` is the
    frozen baseline both ends were equal to at vendoring time; comparing source
    and copy against it separately is what makes "which end moved?" answerable
    at all. Comparing source to copy directly — the obvious implementation —
    can only say "they differ", which is precisely the answer that lets an
    in-place edit to a deployed rubric pass as an un-applied upstream change.
    """
    fdir = root / folder
    stats = rep.folders[folder]

    prov_path = fdir / "rubrics" / "RUBRIC_PROVENANCE.json"
    rubric_dir = fdir / "rubrics"
    source_dir = root / "judges"

    if not rubric_dir.is_dir():
        rep.problem(folder, "rubrics/", "missing-dir",
                    "folder is present but has no rubrics/ — nothing to deploy")
        return
    if not prov_path.is_file():
        rep.problem(folder, "rubrics/RUBRIC_PROVENANCE.json", "missing-provenance",
                    "no provenance record — this folder's own check-drift cannot "
                    "run either, and nothing can say what it was vendored from")
        return
    try:
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
        recorded: dict[str, str] = dict(prov["files"])
    except (ValueError, KeyError, TypeError) as exc:
        rep.problem(folder, "rubrics/RUBRIC_PROVENANCE.json", "bad-provenance",
                    f"unreadable or malformed: {exc}")
        return

    present = set(listdir(rubric_dir))
    source = set(listdir(source_dir))

    # 1. every recorded file: is it still here, and which end moved?
    for name in sorted(recorded):
        rel = f"rubrics/{name}"
        base = recorded[name]
        src = source_dir / name
        cpy = rubric_dir / name

        if not cpy.is_file():
            rep.problem(folder, rel, "copy-missing",
                        "recorded in provenance but absent from rubrics/ — this "
                        "folder would deploy without it")
            continue
        copy_hash = sha(cpy)

        if not src.is_file():
            rep.problem(folder, rel, "source-deleted",
                        f"vendored here (copy {copy_hash}) but gone from judges/ — "
                        "the copy has no source of truth left")
            continue
        src_hash = sha(src)

        if src_hash == base and copy_hash == base:
            stats["rubrics_ok"] += 1
            continue

        if src_hash != base and copy_hash == base:
            rep.problem(folder, rel, "drift", direction="source-moved",
                        detail=f"judges/{name} changed after vendoring "
                               f"({base} -> {src_hash}); the copy is untouched. "
                               f"Re-vendor.")
        elif src_hash == base and copy_hash != base:
            rep.problem(folder, rel, "drift", direction="copy-moved",
                        detail=f"the VENDORED COPY was edited in place "
                               f"({base} -> {copy_hash}); judges/{name} is "
                               f"untouched. This folder's check-drift still calls "
                               f"it in sync. The deployed rubric is not the "
                               f"reviewed one.")
        else:
            same = " (they now match each other)" if src_hash == copy_hash else ""
            rep.problem(folder, rel, "drift", direction="both-moved",
                        detail=f"both ends changed since vendoring: source "
                               f"{base} -> {src_hash}, copy {base} -> "
                               f"{copy_hash}{same}. Re-vendoring would discard "
                               f"the local edit.")

    # 2. coverage: a source rubric this folder never vendored
    for name in sorted(source - set(recorded)):
        rel = f"rubrics/{name}"
        entry = allow.get(rel)
        if entry and entry["kind"] == "not_vendored":
            used.add(rel)
            rep.note(folder, rel, "not_vendored", entry["reason"])
            if name in present:
                rep.problem(folder, rel, "allowlist-contradicted",
                            "allowlisted as deliberately not vendored, but a copy "
                            "exists in rubrics/ and no provenance records it — "
                            "an untracked rubric would deploy")
            continue
        rep.problem(folder, rel, "never-vendored",
                    f"judges/{name} exists but this folder has no record of it — "
                    f"the other folders vendor 12-13 files, this one is missing "
                    f"one they have")

    # 3. orphans: a file in rubrics/ with no counterpart in judges/
    for name in sorted(present - source - set(recorded)):
        rel = f"rubrics/{name}"
        entry = allow.get(rel)
        if entry and entry["kind"] == "local_only":
            used.add(rel)
            rep.note(folder, rel, "local_only", entry["reason"])
            continue
        rep.problem(folder, rel, "orphan",
                    "present in rubrics/ with no counterpart in judges/ and no "
                    "provenance entry — nothing upstream reviews it")

    # 4. provenance integrity: a copy present but unrecorded
    for name in sorted(present & source - set(recorded)):
        rel = f"rubrics/{name}"
        if rel in allow:
            continue
        rep.problem(folder, rel, "unrecorded",
                    "a copy of judges/{0} sits in rubrics/ but the provenance "
                    "file does not list it — it is outside every drift "
                    "check".format(name))


def check_data(root: Path, folder: str, allow: dict[str, dict[str, str]],
               used: set[str], rep: Report) -> None:
    """data/testsets and data/gold, folder copy against root.

    No provenance is recorded for the corpus, so unlike the rubrics there is no
    frozen baseline and the direction of a difference is genuinely unknowable —
    which is worth stating in the output rather than guessing at. What is still
    checkable is presence and equality, in both directions of the set: a row
    file missing from a folder means that folder cannot reproduce the suite's
    published agreement numbers, and a file present only in a folder means it is
    scoring against rows nobody upstream reviewed.
    """
    stats = rep.folders[folder]
    src_root = root / "data"
    dst_root = root / folder / "data"

    if not src_root.is_dir():
        rep.problem(folder, "data/", "missing-source", "root data/ not found")
        return
    if not dst_root.is_dir():
        rep.problem(folder, "data/", "missing-dir",
                    "folder has no data/ — it cannot run its own test suite")
        return

    src_files = walk(src_root)
    dst_files = set(walk(dst_root))

    for rel in src_files:
        key = f"data/{rel}"
        s, d = src_root / rel, dst_root / rel
        if rel not in dst_files:
            rep.problem(folder, key, "copy-missing",
                        "in root data/ but not in this folder")
            continue
        s_hash, d_hash = sha(s), sha(d)
        entry = allow.get(key)
        if entry and entry["kind"] == "divergent_copy":
            used.add(key)
            if s_hash == d_hash:
                continue  # stale-entry pass reports this; not drift
            if d_hash != entry["pin"]:
                rep.problem(folder, key, "pin-broken",
                            f"allowlisted as a deliberate divergence pinned at "
                            f"{entry['pin']}, but the copy is now {d_hash} — the "
                            f"excused difference is not the difference that is "
                            f"there")
            else:
                rep.note(folder, key, "divergent_copy", entry["reason"])
                stats["data_ok"] += 1
            continue
        if s_hash != d_hash:
            rep.problem(folder, key, "data-differs", direction="unknown",
                        detail=f"copy differs from root data/{rel} (root {s_hash}, "
                               f"copy {d_hash}); no vendoring record exists for "
                               f"data/, so which end moved cannot be determined "
                               f"from the tree alone")
            continue
        stats["data_ok"] += 1

    for rel in sorted(dst_files - set(src_files)):
        key = f"data/{rel}"
        if key in allow:
            continue
        rep.problem(folder, key, "orphan",
                    "in this folder's data/ with no counterpart in root data/")


def check_stale_allowlist(root: Path, allow_by_folder: dict[str, list[dict[str, str]]],
                          rep: Report) -> None:
    """Re-check every allowlist entry against the tree.

    An entry that no longer describes anything real is not harmless. It is a
    standing instruction to ignore a path, and the day that path starts
    diverging for a bad reason the instruction is still in force. So the
    disappearance of an intended divergence is itself a finding.
    """
    for folder, entries in allow_by_folder.items():
        fdir = root / folder
        if not fdir.is_dir():
            continue  # the missing folder is already the louder finding
        for entry in entries:
            rel = entry["path"]
            copy = fdir / rel
            name = Path(rel).name
            src = (root / "judges" / name) if rel.startswith("rubrics/") \
                else (root / "data" / Path(rel).relative_to("data").as_posix())

            if entry["kind"] == "not_vendored":
                if not src.is_file():
                    rep.problem(folder, rel, "stale-allowlist",
                                "allowlisted as deliberately not vendored, but no "
                                "such file exists in judges/ any more — the entry "
                                "now excuses nothing and hides anything later "
                                "given that name")
            elif entry["kind"] == "local_only":
                if not copy.is_file():
                    rep.problem(folder, rel, "stale-allowlist",
                                "allowlisted as a local-only file, but it is no "
                                "longer in this folder")
                elif src.is_file():
                    rep.problem(folder, rel, "stale-allowlist",
                                f"allowlisted as having no upstream, but judges/"
                                f"{name} now exists — this is a real vendoring "
                                f"relationship going unchecked")
            elif entry["kind"] == "divergent_copy":
                if not copy.is_file() or not src.is_file():
                    rep.problem(folder, rel, "stale-allowlist",
                                "allowlisted as a deliberate divergence, but one "
                                "side of it no longer exists")
                elif sha(copy) == sha(src):
                    rep.problem(folder, rel, "stale-allowlist",
                                "allowlisted as a deliberate divergence, but the "
                                "two files are now identical — drop the entry, or "
                                "it will excuse the next real difference")


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run(root: Path) -> Report:
    rep = Report()
    allow_by_folder: dict[str, list[dict[str, str]]] = {}
    for e in ALLOWLIST:
        allow_by_folder.setdefault(e["folder"], []).append(e)
    used: set[str] = set()

    for folder in FOLDERS:
        rep.folders[folder] = {
            "present": (root / folder).is_dir(),
            "rubrics_ok": 0, "data_ok": 0, "problems": 0, "intended": 0,
        }
        if not (root / folder).is_dir():
            rep.problem(folder, "", "missing-folder",
                        f"deployment folder {folder}/ is not in the tree — no "
                        f"check inside it can report its own absence")
            continue
        allow = {e["path"]: e for e in allow_by_folder.get(folder, [])}
        check_rubrics(root, folder, allow, used, rep)
        check_data(root, folder, allow, used, rep)

    check_stale_allowlist(root, allow_by_folder, rep)

    if not (root / "judges").is_dir():
        rep.problem("", "judges/", "missing-source",
                    "the source of truth is not in the tree")
    return rep


def emit(rep: Report, quiet: bool) -> None:
    ok = not rep.problems
    if quiet and ok:
        return

    print(f"{len(FOLDERS)} deployment folders vendoring from judges/ and data/\n")
    print(f"{'folder':26} {'rubrics':>8} {'data':>6} {'intended':>9} {'problems':>9}")
    print("-" * 62)
    for folder in FOLDERS:
        s = rep.folders[folder]
        if not s["present"]:
            print(f"{folder:26} {'MISSING':>8} {'-':>6} {'-':>9} {s['problems']:>9}")
            continue
        print(f"{folder:26} {s['rubrics_ok']:>8} {s['data_ok']:>6} "
              f"{s['intended']:>9} {s['problems']:>9}")

    if rep.intended:
        print("\nintended divergences (not drift)")
        for n in rep.intended:
            print(f"  {n['folder']}/{n['path']}  [{n['kind']}]")
            print(f"      {n['reason']}")

    if rep.problems:
        print()
        for p in rep.problems:
            where = f"{p['folder']}/{p['path']}" if p["path"] else p["folder"]
            # The direction, when there is one, is the whole point: it says
            # whether to re-vendor or to go find who edited a deployed file.
            tag = p["direction"] if p.get("direction") else p["kind"]
            print(f"PROBLEM {where} [{tag}]")
            print(f"        {p['detail']}")

    print()
    if ok:
        n = sum(s["rubrics_ok"] for s in rep.folders.values())
        m = sum(s["data_ok"] for s in rep.folders.values())
        print(f"OK — no drift; {n} vendored rubrics and {m} corpus files match "
              f"their source"
              + (f" ({len(rep.intended)} intended divergences)" if rep.intended else ""))
    else:
        print(f"{len(rep.problems)} problem(s)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check the three deployment folders against judges/ and data/, "
                    "in both directions.")
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="repo root to check (default: this file's parent repo)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--quiet", action="store_true", help="print nothing on success")
    a = ap.parse_args(argv)

    rep = run(Path(a.root).resolve())

    if a.json:
        if not (a.quiet and not rep.problems):
            print(json.dumps({
                "root": str(Path(a.root).resolve()),
                "ok": not rep.problems,
                "folders": rep.folders,
                "problems": rep.problems,
                "intended_divergences": rep.intended,
            }, indent=2))
    else:
        emit(rep, a.quiet)
    return 1 if rep.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

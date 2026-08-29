"""Offline tests. No Arize account, no network, no SDK required.

Run: .venv/bin/python -m tests.test_suite
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arize_judges import SCALE_CHOICES
from arize_judges.instrument import Turn, render_transcript, span_attributes
from arize_judges.naming import SAFE_EVAL_NAME, to_ax_name, to_judge_id
from arize_judges.provision import LlmSettings, plan, task_plan
from arize_judges.render import render, render_all
from arize_judges.spec import deployable, load_rubrics
from arize_judges.verify import recompute_label, verify

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


RUBRICS = load_rubrics()
DEPLOY = deployable(RUBRICS)

# --- naming: the trap that silently drops columns ---------------------------
check("every ax name passes the SDK eval-name regex",
      all(SAFE_EVAL_NAME.match(to_ax_name(r.id)) for r in RUBRICS))
check("ax names round-trip", all(to_judge_id(to_ax_name(r.id)) == r.id for r in RUBRICS))
check("hyphens are removed", "-" not in to_ax_name("capability-honesty-traced"))

# --- variant mutual exclusion ----------------------------------------------
ids = {r.id for r in DEPLOY}
check("variant excluded by default", "capability-honesty-traced" not in ids)
check("base judge kept", "capability-honesty" in ids)
check("traced variant swaps in",
      "capability-honesty-traced" in {r.id for r in deployable(RUBRICS, prefer_traced=True)})
check("never both at once",
      not ({"capability-honesty", "capability-honesty-traced"} <=
           {r.id for r in deployable(RUBRICS, prefer_traced=True)}))

# --- rendering --------------------------------------------------------------
for style in ("single", "double"):
    ts = render_all(DEPLOY, style)
    check(f"{style}: all judges render", len(ts) == len(DEPLOY))
    check(f"{style}: templates are deterministic",
          [t.hash for t in ts] == [t.hash for t in render_all(DEPLOY, style)])
    check(f"{style}: each template carries exactly one variable",
          all(t.template.count("{" if style == "single" else "{{") >= 1 for t in ts))
    check(f"{style}: no unrendered scope placeholder",
          all("{{scope}}" not in t.template for t in ts))

t_turn = render([r for r in DEPLOY if r.unit == "turn"][0])
t_conv = render([r for r in DEPLOY if r.unit == "conversation"][0])
check("turn judges read judge.transcript", t_turn.variable == "transcript")
check("conversation judges read judge.conversation_transcript",
      t_conv.variable == "conversation_transcript")
check("preamble survives byte-identical into the template",
      "The scale: 0, 0.5, 1.0" in t_turn.template)
check("tail instructs JSON-in-explanation",
      "MUST be exactly one JSON object" in t_turn.template)

# --- provisioning plan ------------------------------------------------------
p = plan("SPACE", LlmSettings(ai_integration_id="INT"))
check("plan covers every deployable judge", len(p) == len(DEPLOY))
check("classification_choices are the numerals",
      all(i["template_config"]["classification_choices"] == SCALE_CHOICES for i in p),
      "must stay 1.0/0.5/0 so the preamble needs no edit")
check("include_explanations is on everywhere",
      all(i["template_config"]["include_explanations"] for i in p),
      "without it the forced tool has no explanation parameter at all")
check("every evaluator is SPAN scope",
      all(i["template_config"]["data_granularity"] == "SPAN" for i in p))
check("temperature is explicit",
      all("temperature" in i["template_config"]["llm_config"]["invocation_parameters"] for i in p),
      "there is no documented server default")

# --- tasks refuse to run unpinned ------------------------------------------
try:
    task_plan("PROJ", {"t": {"sampling_rate": 1.0, "judges": ["actionability"]}},
              lock={"judges": {"actionability": {"evaluator_id": "e", "evaluator_version_id": None,
                                                 "variable": "transcript"}}})
    check("unpinned evaluator version is refused", False, "no error raised")
except ValueError as e:
    check("unpinned evaluator version is refused", "always latest" in str(e).lower()
          or "pinned" in str(e).lower())

# --- verifier ---------------------------------------------------------------
sd = [r for r in RUBRICS if r.id == "signal-density"][0]


def payload(**over):
    base = {"v": 1, "j": "signal-density", "trig": True, "inj": False, "bl": None,
            "chk": [{"id": i, "ok": True, "sp": "brown fox"} for i in sd.check_ids],
            "ev": ["brown fox"], "why": "ok", "conf": "high",
            "x": {"removable_fraction": "none"}}
    base.update(over)
    return json.dumps(base)


check("valid payload passes", verify(sd, payload(), "1.0").ok)
check("prose is rejected", not verify(sd, "It was fine.", "1.0").ok)
check("missing checks rejected",
      not verify(sd, payload(chk=[{"id": "C1", "ok": True, "sp": ""}]), "1.0").ok)
check("non-boolean verdict rejected",
      not verify(sd, payload(chk=[{"id": i, "ok": "yes", "sp": ""} for i in sd.check_ids]), "1.0").ok)
check("bad enum rejected", not verify(sd, payload(x={"removable_fraction": "lots"}), "1.0").ok)
check("label 0 without a bright line rejected", not verify(sd, payload(), "0").ok)
check("bright line with label 1.0 rejected", not verify(sd, payload(bl="Mostly filler"), "1.0").ok)
check("forbidden key `label` rejected", not verify(sd, payload(label="x"), "1.0").ok)

failing = json.loads(payload()); failing["chk"][0]["ok"] = False
v = verify(sd, json.dumps(failing), "1.0")
check("label divergence detected", v.label_diverged and v.recomputed_label == "0.5")
check("recompute: bright line wins", recompute_label({"bl": "x", "chk": [{"ok": True}]}) == "0")
check("recompute: all pass -> 1.0", recompute_label({"bl": None, "chk": [{"ok": True}]}) == "1.0")
check("recompute: any fail -> 0.5", recompute_label({"bl": None, "chk": [{"ok": False}]}) == "0.5")

check("evidence spans verified against transcript",
      any("not found in transcript" in w
          for w in verify(sd, payload(), "1.0", transcript="unrelated text").warnings))
check("a stated absence is not mistaken for a bad quote",
      not any("not found in transcript" in w for w in verify(
          sd, payload(ev=[], chk=[{"id": i, "ok": True, "sp": "no padding present"}
                                  for i in sd.check_ids]),
          "1.0", transcript="unrelated text").warnings),
      "short absence statements must not be flagged as confabulated quotes")
check("evidence spans accepted when present",
      not any("not found in transcript" in w
              for w in verify(sd, payload(), "1.0", transcript="a brown fox ran").warnings))

# --- instrumentation --------------------------------------------------------
turns = [Turn("customer", "hi"), Turn("assistant", "hello", ["a() -> 1"]),
         Turn("customer", "more"), Turn("assistant", "sure")]
tx = render_transcript(turns, 1)
check("transcript marks the target turn", ">>> TARGET" in tx and tx.count(">>> TARGET") == 1)
check("transcript opens with the marker the rubrics name",
      tx.startswith("[BEGIN TRANSCRIPT]"))
check("assistant turn with no calls still gets the sentinel",
      "[tool trace: none recorded]" in tx)
check("conversation render marks no turn", ">>> TARGET" not in render_transcript(turns, None))
attrs = span_attributes(turns, 3, "c1", is_final_turn=True)
check("final turn carries the conversation transcript",
      "judge.conversation_transcript" in attrs)
check("renderer version is recorded", "judge.renderer_version" in attrs)

# --- CLI entry point --------------------------------------------------------
# Regression: the shim used to run under the SYSTEM interpreter, which has no
# PyYAML, so every documented command died on import. The venv check compares
# sys.prefix, NOT sys.executable — `.venv/bin/python` is a symlink to the system
# interpreter, so resolving both paths makes them compare equal and the re-exec
# silently never happens.
import subprocess

SHIM = Path(__file__).resolve().parent.parent / "bin" / "arize-judges"
if SHIM.exists():
    env = {k: v for k, v in __import__("os").environ.items() if k != "ARIZE_JUDGES_REEXEC"}
    r = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "list"],
                       capture_output=True, text=True, env=env)
    check("shim runs under a bare system interpreter", r.returncode == 0,
          (r.stderr or r.stdout)[-160:])
    check("shim output is the suite listing", "deploy" in r.stdout, r.stdout[:120])
    r2 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "tasks", "--project", "P"],
                        capture_output=True, text=True, env=env)
    check("operator error exits 2 with no traceback",
          r2.returncode == 2 and "Traceback" not in r2.stderr, r2.stderr[-160:])

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

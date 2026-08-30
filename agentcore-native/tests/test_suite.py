"""Offline tests for the native llmAsAJudge path. No AWS account, no network."""
from __future__ import annotations

import json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from native_judges import (LEVELS, MAX_EVALUATORS_PER_CONFIG, PLACEHOLDERS,
                           RATING_SCALE, UNIT_TO_LEVEL)
from native_judges.naming import EVALUATOR_NAME, to_evaluator_name
from native_judges.provision import ModelConfig, TIERS, online_config_payload, plan
from native_judges.score import (BANDS, STANDARDIZATION, ItemResult, band_quota,
                                 band_shortfalls, band_sizes, build_prompt,
                                 default_judges, load_testset, metrics_for, parse_verdict,
                                 plan_prompts, prepare_out, render_report, run_live,
                                 sample_items, Verdict)
from native_judges.spec import deployable, load_rubrics
from native_judges.transcript import conversation_from_record
from native_judges.transform import transform

FAILS: list[str] = []
def check(n, c, d=""):
    print(f"{'PASS' if c else 'FAIL'}  {n}" + (f" — {d}" if d and not c else ""))
    if not c: FAILS.append(n)

RUBRICS = load_rubrics(); DEPLOY = deployable(RUBRICS)

# --- the transform is the whole point of this folder ------------------------
for r in RUBRICS:
    t = transform(r)
    check(f"{r.id}: no transcript markers survive", not t.residual_markers,
          str(t.residual_markers))
    check(f"{r.id}: the correct placeholder is present",
          PLACEHOLDERS[t.level][0] in t.instructions)
    check(f"{r.id}: output-contract sections were stripped",
          "## How to score" not in t.instructions and "[END TRANSCRIPT]" not in t.instructions)
    check(f"{r.id}: rating labels match the scale exactly",
          all(x["label"] in t.instructions for x in RATING_SCALE))
    check(f"{r.id}: the check ids survive into the reasoning contract",
          all(c in t.instructions for c in r.check_ids))

t = transform([r for r in DEPLOY if r.unit == "turn"][0])
check("turn judges get {assistant_turn}", "{assistant_turn}" in t.instructions)
ts = transform([r for r in DEPLOY if r.unit == "conversation"][0])
check("conversation judges do NOT get {assistant_turn}",
      "{assistant_turn}" not in ts.instructions,
      "session scope has no per-turn placeholder")
check("the transform is deterministic",
      transform(RUBRICS[0]).instructions == transform(RUBRICS[0]).instructions)
check("what was removed is recorded, not silent",
      all(transform(r).removed for r in DEPLOY))

# --- scale ------------------------------------------------------------------
check("three rating levels", len(RATING_SCALE) == 3)
check("values are 0 / 0.5 / 1.0",
      sorted(x["value"] for x in RATING_SCALE) == [0.0, 0.5, 1.0])
check("every label within AgentCore's [1,100] bound",
      all(1 <= len(x["label"]) <= 100 for x in RATING_SCALE))
check("every level has a definition", all(x["definition"] for x in RATING_SCALE))

# --- naming and levels ------------------------------------------------------
check("names match the evaluatorName pattern",
      all(EVALUATOR_NAME.fullmatch(to_evaluator_name(r.id)) for r in RUBRICS))
lv = [UNIT_TO_LEVEL[r.unit] for r in DEPLOY]
check("8 TRACE + 2 SESSION", lv.count("TRACE") == 8 and lv.count("SESSION") == 2, str(lv))
check("all levels are real", set(lv) <= set(LEVELS))

# --- provisioning -----------------------------------------------------------
ps = plan(ModelConfig())
check("plan covers every deployable judge", len(ps) == len(DEPLOY))
check("all llmAsAJudge, none codeBased",
      all("llmAsAJudge" in p["evaluatorConfig"] for p in ps))
check("instructions are non-trivial",
      all(len(p["evaluatorConfig"]["llmAsAJudge"]["instructions"]) > 5000 for p in ps))
check("tiers fit the cap",
      all(len(t["judges"]) <= MAX_EVALUATORS_PER_CONFIG for t in TIERS.values()))
check("tiers partition the suite",
      sorted(j for t in TIERS.values() for j in t["judges"]) == sorted(r.id for r in DEPLOY))

try:
    import boto3  # noqa
    from native_judges.provision import validate_request
    bad = sum(len(validate_request("CreateEvaluator", p)) for p in ps)
    check("[boto3] every CreateEvaluator validates", bad == 0, f"{bad} problems")
    check("[boto3] temperature above 1.0 is caught (Bedrock bound is [0,1], not [0,2])",
          bool(validate_request("CreateEvaluator", plan(ModelConfig(temperature=1.5))[0])))
    check("[boto3] a bogus level is caught",
          bool(validate_request("CreateEvaluator", {**ps[0], "level": "TURN"})))
    lock = {"judges": {j: {"evaluatorId": f"{j.replace('-', '_')}-a1b2c3d4e5"}
                       for t in TIERS.values() for j in t["judges"]}}
    probs = sum(len(validate_request("CreateOnlineEvaluationConfig",
                online_config_payload(k, ["/lg"], ["s"],
                                      "arn:aws:iam::123456789012:role/R", lock=lock)))
                for k in TIERS)
    check("[boto3] online configs validate", probs == 0, f"{probs} problems")
except ModuleNotFoundError:
    print("SKIP  [boto3] request validation")

# --- CLI --------------------------------------------------------------------
SHIM = Path(__file__).resolve().parent.parent / "bin" / "native-judges"
env = {k: v for k, v in os.environ.items() if k != "NJ_REEXEC"}
r1 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "list"],
                    capture_output=True, text=True, env=env)
check("shim runs under a bare interpreter", r1.returncode == 0, (r1.stderr or "")[-140:])
r2 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "show", "--judge", "nope"],
                    capture_output=True, text=True, env=env)
check("operator error exits 2, no traceback",
      r2.returncode == 2 and "Traceback" not in r2.stderr)

# --- offline scoring: sampling ----------------------------------------------
check("--limit 6 draws 2 per band",
      band_quota(6) == {0.0: 2, 0.5: 2, 1.0: 2})
check("an uneven --limit gives the remainder to the lowest band first",
      band_quota(4) == {0.0: 2, 0.5: 1, 1.0: 1} and band_quota(5) == {0.0: 2, 0.5: 2, 1.0: 1})
ACT_SIZES = band_sizes(load_testset("actionability"))
check("--limit 0 means all 60", band_quota(0, ACT_SIZES) == {b: 20 for b in BANDS})
check("--limit 0 means whatever the set holds, not a hardcoded 20",
      band_quota(0, {0.0: 3, 0.5: 9, 1.0: 1}) == {0.0: 3, 0.5: 9, 1.0: 1})
try:
    band_quota(0)
    ok = False
except ValueError:
    ok = True
check("--limit 0 cannot be answered without the set's band sizes", ok,
      "a constant quota silently caps a bigger set and over-promises on a smaller one")

s6 = sample_items("actionability", 6, 0)
check("sampling honours the bands",
      sorted(float(r["expected_score"]) for r in s6) == [0.0, 0.0, 0.5, 0.5, 1.0, 1.0])
check("the same seed draws the same items",
      [r["id"] for r in sample_items("actionability", 6, 0)] == [r["id"] for r in s6])
check("a different seed draws different items",
      [r["id"] for r in sample_items("actionability", 6, 1)] != [r["id"] for r in s6])
check("the draw does not depend on which other judges were asked for",
      [p.item_id for p in plan_prompts(["actionability"], 6, 0, 1)]
      == [p.item_id for p in plan_prompts(["actionability", "need-coverage"], 6, 0, 1)
          if p.judge_id == "actionability"])
check("--limit 0 draws the whole set", len(sample_items("actionability", 0, 0)) == 60)
check("--k repeats each item", len(plan_prompts(["actionability"], 3, 0, 3)) == 9)
check("every deployable judge has a test set",
      all(len(load_testset(r.id)) == 60 for r in DEPLOY))

# --- offline scoring: prompt construction -----------------------------------
for r in DEPLOY:
    t = transform(r)
    ps = [build_prompt(t, rec) for rec in sample_items(r.id, 3, 0)]
    check(f"{r.id}: every prompt carries the transformed instructions, not the rubric",
          all(p.system.startswith(t.instructions) for p in ps))
    check(f"{r.id}: the placeholders the prompt names are the blocks the message has",
          all(all(ph in p.user for ph in PLACEHOLDERS[t.level][:2 if t.level == "TRACE" else 1])
              for p in ps))
    check(f"{r.id}: the rendered transcript reaches the model",
          all("[BEGIN TRANSCRIPT]" in p.user and "--- turn 0 | CUSTOMER ---" in p.user
              for p in ps))

check("--limit above a band's 20 clamps instead of raising",
      len(sample_items("actionability", 90, 0)) == 60)
SHORT = band_shortfalls(["actionability"], 90)
check("a band that cannot fill its quota is reported, not silently under-drawn",
      len(SHORT) == 3 and all(s.drawn == 20 and s.wanted == 30 for s in SHORT),
      str(SHORT))
check("a draw the set can fill reports no shortfall",
      band_shortfalls(["actionability"], 6) == [])
check("no prompt smuggles back the output contract the transform stripped",
      not any(m in p.system
              for p in plan_prompts(default_judges(), 3, 0, 1)
              for m in ("## How to score", "[END TRANSCRIPT]", ">>> TARGET`")))
check("the simulated standardization prompt asks for exactly the rating vocabulary",
      all(f"`{x['label']}`" in STANDARDIZATION for x in RATING_SCALE)
      and '{"reasoning": "...", "score": "..."}' in STANDARDIZATION)

tp = build_prompt(transform([r for r in DEPLOY if r.unit == "turn"][0]),
                  load_testset("actionability")[0])
check("turn judges get the target turn under the name the transform left behind",
      "[BEGIN {assistant_turn}]" in tp.user and " >>> TARGET" in tp.user)
sp = build_prompt(transform([r for r in DEPLOY if r.unit == "conversation"][0]),
                  load_testset("conduct-adaptation")[0])
check("session judges get no target turn — there is no placeholder to point it at",
      "{assistant_turn}" not in sp.user and " >>> TARGET" not in sp.user)

# The untrusted region must be closed and must not be the last thing the model
# reads: this path lost the parent suite's scoring tail when AgentCore took over
# the output contract, so the tail has to be rebuilt in the user message.
for p in (tp, sp):
    ctx = PLACEHOLDERS[p.level][0]
    check(f"{p.level}: the untrusted region is fenced on both sides",
          f"[BEGIN {ctx}]" in p.user and f"[END {ctx}]" in p.user)
    check(f"{p.level}: untrusted transcript is not the last thing in the prompt",
          p.user.rstrip().endswith("against the rubric in the instructions above."))
    check(f"{p.level}: the fence is named after a token the transformed prompt defines",
          ctx in transform(load_rubrics([p.judge_id])[0]).instructions)
check("the fences do not reintroduce the vocabulary the transform rewrote away",
      "[END TRANSCRIPT]" not in tp.user and "[BEGIN TRANSCRIPT]\n" in tp.user)

conv = conversation_from_record(load_testset("actionability")[0])
check("the transcript format is the one the rubrics were written against",
      conv.render(1).startswith("[BEGIN TRANSCRIPT]\n\n--- turn 0 | CUSTOMER ---")
      and "\n--- turn 1 | ASSISTANT >>> TARGET ---" in conv.render(1))
check("an assistant turn with no tools still says so",
      "  [tool trace: none recorded]" in
      conversation_from_record({"id": "x", "turns": [{"role": "assistant", "text": "hi"}]}).render(None))

# --- offline scoring: parsing -----------------------------------------------
good = parse_verdict('{"reasoning": "CHK C1=y C2=n | TRIG y | BL - then prose", "score": "warning"}')
check("a well-formed output parses to a score", good.score == 0.5 and good.unparseable is None)
check("the compact check vector is read back",
      good.checks == {"C1": True, "C2": False} and good.trigger is True)
check("the CHK flags recompute to the rating they imply", good.recomputed == 0.5)
allpass = parse_verdict('{"reasoning": "CHK C1=y C2=y | TRIG n | BL -", "score": "pass"}')
check("all checks passing recomputes to pass", allpass.recomputed == 1.0)
bl = parse_verdict('{"reasoning": "CHK C1=y C2=y | TRIG y | BL invented a fee", "score": "pass"}')
check("a fired bright line outranks every passing check", bl.recomputed == 0.0)
check("a rating its own flags contradict is visible",
      ItemResult("j", "i", 1.0, "h", bl).divergent)
check("a malformed output is unparseable, not a crash",
      parse_verdict("I think this one is a warning, honestly.").unparseable is not None)
check("truncated JSON is unparseable",
      parse_verdict('{"reasoning": "CHK C1=y", "score": "pas').unparseable is not None)
# The transformed prompt prints the vocabulary backticked (`pass`, `warning`,
# `hard_failure`), so a model echoing the prompt's own formatting must not be
# charged to the output-contract failure rate.
check("a backticked rating is the prompt's own formatting, not a contract failure",
      parse_verdict('{"reasoning": "r", "score": "`pass`"}').score == 1.0)
check("trailing punctuation does not make a rating unreadable",
      parse_verdict('{"reasoning": "r", "score": "hard_failure."}').score == 0.0
      and parse_verdict('{"reasoning": "r", "score": " Warning, "}').score == 0.5)
check("bold and quoted ratings parse too",
      parse_verdict('{"reasoning": "r", "score": "**pass**"}').score == 1.0
      and parse_verdict('{"reasoning": "r", "score": "\\"warning\\""}').score == 0.5)
check("stripping decoration does not smuggle in a rating outside the scale",
      parse_verdict('{"reasoning": "r", "score": "`excellent`"}').unparseable is not None)
oor = parse_verdict('{"reasoning": "CHK C1=y | TRIG y | BL -", "score": "excellent"}')
check("a rating outside the scale is unparseable", oor.score is None and oor.unparseable)
check("a numeric score is outside the scale too",
      parse_verdict('{"reasoning": "r", "score": 0.5}').unparseable is not None)
check("no CHK line still yields a rating, with no contract to audit it",
      (v := parse_verdict('{"reasoning": "it was fine", "score": "pass"}')).score == 1.0
      and v.recomputed is None)

# --- offline scoring: the metric math ---------------------------------------
def _item(expected, word, item_id="i"):
    return ItemResult("j", item_id, expected, "h",
                      parse_verdict(json.dumps({"reasoning": "CHK C1=y | TRIG y | BL -",
                                                "score": word})))

FIX = [_item(1.0, "pass", "a"),        # exact
       _item(1.0, "warning", "b"),     # ±1 level
       _item(1.0, "hard_failure", "c"),# fp0, and not within one level
       _item(0.5, "warning", "d"),     # exact
       _item(0.5, "hard_failure", "e"),# fp0
       _item(0.0, "hard_failure", "f"),# exact
       _item(0.0, "pass", "g"),        # fn0, and not within one level
       _item(0.0, "not_a_rating", "h")]# unparseable
M = metrics_for("j", FIX)
check("n counts every item, unparseable included", M.n == 8)
check("exact counts only equality", M.exact == 3)
check("±1 level is a half-point tolerance", M.near == 5)
check("fp0 is a 0 the label did not ask for", M.fp0 == 2)
check("fn0 is a 0 the label asked for and did not get", M.fn0 == 2)
# Every column is summed over the same population — the items that came back.
# fp0 and fn0 used to be summed over the parsed subset while n and the bands were
# summed over everything, which quietly computed the two columns a reader decides
# on over a smaller, friendlier set than the header claims.
check("an unparseable item cannot be a false alarm — it never scored 0",
      M.unparseable == 1 and M.fp0 == 2)
check("an unreadable answer on a hard_failure item is a miss, not a free pass",
      metrics_for("j", [_item(0.0, "not_a_rating")]).fn0 == 1)
check("the error columns and n are summed over the same items",
      M.n == len([r for r in FIX if r.transport is None]))
check("the per-band columns are hits over that band's total",
      M.bands == {1.0: (1, 3), 0.5: (1, 2), 0.0: (1, 3)})
check("a judge whose every output is unreadable scores 0%, not a crash",
      metrics_for("j", [_item(1.0, "nope")]).exact == 0)

REPORT = render_report([M])
check("the report header is the shared one, to the byte",
      REPORT.splitlines()[0] ==
      "judge                            n   exact  ±1 lvl   fp0   fn0   pass  warn  fail")
check("the report row is the shared layout",
      REPORT.splitlines()[2] ==
      "j                                8   37.5%  62.5%      2     2   1/3   1/2   1/3",
      REPORT.splitlines()[2])
check("the legend is printed, in the parent's wording",
      "fp0 = judge scored 0 where the label was not 0 (false alarm)" in REPORT
      and "fn0 = label was 0 and the judge did not catch it (missed bright line)" in REPORT)
check("unparseable items get their own line rather than vanishing",
      REPORT.splitlines()[3] == "unparseable                      1   j x1")
check("compact-contract divergence is reported below the shared table, not as a column",
      "compact contract = " in REPORT
      and REPORT.index("compact contract = ") > REPORT.index("fn0 = label was 0"))
check("a run whose contract held prints no divergence block",
      "compact contract = " not in render_report([metrics_for("j", [_item(1.0, "pass")])]))
check("a short band is stated in the report, so nobody reads 4 items as 6",
      "short bands" in render_report([M], band_shortfalls(["actionability"], 90))
      and "20 of 30" in render_report([M], band_shortfalls(["actionability"], 90)))

# --- a transport failure is an infrastructure fact, not a judge's score ------
class _StubAPIConnectionError(Exception):
    """Stands in for anthropic.APIConnectionError / RateLimitError / a 401.

    Named rather than imported so this runs on a machine with no SDK: the whole
    point of the accounting under test is that it holds when the call never
    happens, and a test that needs the client library to prove it would not run
    in the situation it describes.
    """


class _Block:
    type = "text"
    def __init__(self, text): self.text = text


class _Answer:
    def __init__(self, text): self.content = [_Block(text)]


class _DeadClient:
    """Every call raises. Nothing is ever answered."""
    class messages:
        @staticmethod
        def create(**kw): raise _StubAPIConnectionError("Connection error.")


class _TalkativeClient:
    """Answers every call with one fixed body."""
    def __init__(self, body):
        self.body = body
        self.messages = self  # the SDK's client.messages.create shape, collapsed
    def create(self, **kw): return _Answer(self.body)


TP = plan_prompts(["actionability"], 3, 0, 1)
DEAD = run_live(TP, "m", "high", client=_DeadClient())
check("a lost call is recorded as transport, not as an unreadable answer",
      all(r.transport and r.verdict.unparseable is None and not r.scored for r in DEAD))
TM = metrics_for("actionability", DEAD)
check("transport failures are excluded from n and from every quality column",
      (TM.n, TM.exact, TM.near, TM.fp0, TM.fn0, TM.unparseable) == (0, 0, 0, 0, 0, 0)
      and TM.bands == {b: (0, 0) for b in BANDS})
check("a lost call on a hard_failure item is not reported as a missed bright line",
      any(p.expected == 0.0 for p in TP) and TM.fn0 == 0)
check("transport failures are counted, on their own line", TM.transport == len(TP) == 3)
TREPORT = render_report([TM])
check("the transport line names the judge and says the items were excluded",
      "actionability x3" in TREPORT
      and "(excluded from the table — no answer received)" in TREPORT, TREPORT)
check("an all-transport run says nothing was measured rather than reading as perfect",
      "NOTHING WAS MEASURED" in TREPORT and "100.0%" not in TREPORT)
check("the per-item row carries the transport reason, so it is not just missing",
      DEAD[0].to_row()["transport"].startswith("_StubAPIConnectionError"))
MIX = metrics_for("j", [_item(0.0, "hard_failure", "a"),
                        ItemResult("j", "b", 0.0, "h", Verdict(),
                                   transport="APIConnectionError: reset")])
check("one lost call does not disturb the items that did answer",
      (MIX.n, MIX.exact, MIX.fn0, MIX.transport) == (1, 1, 0, 1)
      and MIX.bands[0.0] == (1, 1))
LIVE = run_live(TP[:1], "m", "high",
                client=_TalkativeClient('{"reasoning": "CHK C1=y | TRIG y | BL -", '
                                        '"score": "pass"}'))
check("an answer that does arrive is scored through the same path",
      LIVE[0].scored and LIVE[0].transport is None)
JUNK = metrics_for("j", run_live(TP[:1], "m", "high",
                                 client=_TalkativeClient("no JSON here at all")))
check("an answer that breaks the output contract stays in n — that is the measurement",
      JUNK.n == 1 and JUNK.unparseable == 1 and JUNK.transport == 0)

# --- offline scoring: the CLI must work with no key, no network, no SDK -----
noauth = {k: v for k, v in env.items()
          if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
r3 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--dry-run", "--limit", "3"],
                    capture_output=True, text=True, env=noauth)
check("score --dry-run exits 0 with no credentials",
      r3.returncode == 0 and "nothing was sent" in r3.stdout, (r3.stderr or "")[-140:])
check("the dry run says what is missing and what to set",
      "ANTHROPIC_API_KEY" in r3.stdout and "no API key found" in r3.stdout)
check("the dry run never authenticates, so no traceback",
      "Traceback" not in r3.stderr and "anthropic" not in r3.stderr)
r4 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--limit", "3"],
                    capture_output=True, text=True, env={**noauth, "ANTHROPIC_API_KEY": "sk-not-real"},
                    stdin=subprocess.DEVNULL)
check("non-interactive without --yes refuses rather than spending",
      r4.returncode == 2 and "--yes" in r4.stderr, (r4.stdout or "")[-140:])
# The items figure is the true total, not judges-into-items: an integer mean
# printed as a factor misstates the product whenever judges draw unequal counts,
# in the one line whose entire job is the number being authorised.
check("the refusal still shows the call count first, and it is the true total",
      "10 judges, 30 items, k=1 = 30 model calls" in r4.stderr, (r4.stderr or "")[-140:])
r5 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score",
                     "--judge", "actionability", "--judge", "actionability", "--limit", "6"],
                    capture_output=True, text=True,
                    env={**noauth, "ANTHROPIC_API_KEY": "sk-not-real"},
                    stdin=subprocess.DEVNULL)
check("a repeated --judge is not billed or reported twice",
      "1 judges, 6 items, k=1 = 6 model calls" in r5.stderr, (r5.stderr or "")[-140:])

# --- --out is validated before the money is spent, not after ----------------
TMP = Path(tempfile.mkdtemp(prefix="nj-out-"))
(TMP / "a-file").write_text("not a directory\n", encoding="utf-8")
paid = {**noauth, "ANTHROPIC_API_KEY": "sk-not-real"}
r6 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--judge",
                     "actionability", "--limit", "3", "--yes",
                     "--out", str(TMP / "a-file" / "results.jsonl")],
                    capture_output=True, text=True, env=paid, stdin=subprocess.DEVNULL)
check("an unwritable --out is refused before a single call, not after the run",
      r6.returncode == 2 and "not writable" in r6.stderr
      and "model calls" not in r6.stderr, (r6.stderr or "")[-160:])
NEST = TMP / "made" / "on" / "demand" / "results.jsonl"
r7 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--judge",
                     "actionability", "--limit", "3", "--out", str(NEST)],
                    capture_output=True, text=True, env=paid, stdin=subprocess.DEVNULL)
check("--out's parent is created before the spend, and the probe leaves no file",
      NEST.parent.is_dir() and not NEST.exists() and "model calls" in r7.stderr,
      (r7.stderr or "")[-160:])
check("prepare_out reports the resolved path it verified",
      prepare_out(NEST) == NEST.resolve())

# --- every dependency gets the same missing-module message ------------------
BLOCK = Path(tempfile.mkdtemp(prefix="nj-block-"))
for mod in ("boto3", "anthropic"):
    (BLOCK / f"{mod}.py").write_text(
        f'raise ModuleNotFoundError("No module named {mod}", name="{mod}")\n',
        encoding="utf-8")
blocked = {**env, "PYTHONPATH": str(BLOCK)}
r8 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "provision", "--apply",
                     "--region", "us-east-1"],
                    capture_output=True, text=True, env=blocked)
check("a missing boto3 gets the install message, not a traceback",
      "native-judges needs boto3" in r8.stderr and "Traceback" not in r8.stderr,
      (r8.stderr or "")[-160:])
r9 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--judge",
                     "actionability", "--limit", "3", "--yes"],
                    capture_output=True, text=True,
                    env={**blocked, "ANTHROPIC_API_KEY": "sk-not-real"},
                    stdin=subprocess.DEVNULL)
check("a missing anthropic gets it too — the lazy import must not leak a stack trace",
      "native-judges needs anthropic" in r9.stderr and "Traceback" not in r9.stderr,
      (r9.stderr or "")[-160:])

# --- the traced variant is excluded by default and nameable on purpose ------
check("deployable() drops one side of the variant pair, so score covers 10 judges",
      len(default_judges()) == 10 and "capability-honesty-traced" not in default_judges()
      and "capability-honesty" in default_judges())
check("a variant can still be named explicitly, which is how you compare the pair",
      len(plan_prompts(["capability-honesty-traced"], 3, 0, 1)) == 3)
r10 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--dry-run",
                      "--judge", "capability-honesty-traced", "--limit", "3"],
                     capture_output=True, text=True, env=noauth)
check("--judge accepts the traced variant the default set leaves out",
      r10.returncode == 0 and "capability-honesty-traced" in r10.stdout,
      (r10.stderr or "")[-160:])
r11 = subprocess.run(["/usr/bin/env", "python3", str(SHIM), "score", "--dry-run",
                      "--judge", "actionability", "--limit", "90"],
                     capture_output=True, text=True, env=noauth)
check("the dry run states a short band before the operator decides to pay",
      "short bands" in r11.stdout and "20 of 30" in r11.stdout,
      (r11.stdout or "")[-200:])

print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)

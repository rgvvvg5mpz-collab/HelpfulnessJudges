# Testing strategy — AgentCore native llmAsAJudge

This path rewrites the rubrics, which adds a whole category of testing the other two do not need.

> **Baseline: no judge on this path has scored a real item.** Everything below is
> the plan, not results.

This folder is standalone, so this file repeats the shared method. What differs by
path is in *What this path makes hard/easy to test* below — read that first if you
have already read a sibling.

## The test category unique to this path

**The transform is a new failure surface.** `transform.py` rewrites every rubric
before deployment, so a defect there changes what the judges measure without
touching a rubric file. That is why the test suite here is the largest of the
three (192 checks): five assertions per judge on the transform alone, and a
second block that scores the transformed prompts against the shipped test sets.

| Test | Catches |
|---|---|
| No transcript markers survive | A rubric still referring to `>>> TARGET`, which does not exist here |
| The right placeholder is present | A TRACE judge without `{assistant_turn}`, or a SESSION judge given one |
| Output-contract sections stripped | The judge following two contradictory output contracts |
| Rating labels match the scale exactly | The model asked for two different vocabularies |
| Check ids survive into the reasoning contract | A silently truncated check vector |

Run `native-judges diff` and read it. Everything the transform removes is
recorded — the drift being *visible* is the main safety property of this folder.

## What this path cannot test at all

**k=1 only.** No repetition parameter exists. Self-consistency is unanswerable
here; run it offline against the parent harness.

**Nothing enforces the verdict shape.** The `CHK C1=y …` flag line is a prompt
convention, not a schema. Parse it on export and alarm on the parse-failure rate
— that is the only detector.

**Evidence spans do not exist**, so the evidence-span verifier that the other two
paths run has nothing to check.

## The measurement that decides whether this path is acceptable

**Run the anchor set both ways** — this path and `../agentcore-integration` — and
measure the gap.

The prompts here are not the prompts that were validated, so the anchor-set
numbers do not transfer. That gap *is* the price of the simplicity, and it should
be a number in a memo rather than an assumption. If it is small, this path is a
bargain. If it is large, it is a different instrument wearing the same name.

## Scoring rubrics offline

`data/testsets/` shipped 60 labelled samples per judge that nothing read. That
left the two sections above as instructions rather than instruments: the parse-
failure rate was "the only detector" with no detector, and the gap against
`../agentcore-integration` needed a live AWS account before it could be a number.
`native_judges/score.py` and `bin/native-judges score` close that.

```bash
bin/native-judges score --dry-run --limit 6         # every prompt, nothing sent
bin/native-judges score --judge actionability --limit 6 --out /tmp/a.jsonl
bin/native-judges score --limit 0 --yes             # the full 600 — read the count first
```

**It scores the transformed prompt, never the rubric.** Each item's system
message is `transform(rubric).instructions` — the exact text that would be
uploaded as `evaluatorConfig.llmAsAJudge.instructions`. Scoring the rubric would
measure the parent suite and tell you nothing about this path, which is the one
thing this folder needs told.

What it answers, from the sections above:

| Open problem | What the scorer gives you |
|---|---|
| "Nothing enforces the verdict shape… parse it and alarm on the parse-failure rate — that is the only detector" | The detector, run offline. `unparseable` is a reported line, counted inside `n` **and inside `fp0`/`fn0`** — every column is summed over the same population, so the contract failure cannot hide in the columns you actually decide on |
| The `CHK C1=y …` flag line is a convention nothing validates | Every rating is recomputed from the item's own flags (bright line → `hard_failure`, else all checks pass → `pass`, else `warning`) and divergence is its own number below the table |
| "Run the anchor set both ways and measure the gap" | Half of it, free. The table is pinned character-for-character to the other two folders', so the gap is a subtraction rather than a reconciliation |
| Regression after a rubric edit | `--seed` is keyed by judge as well as seed, so adding or dropping a `--judge` does not reshuffle the others' items and two runs differ only by the edit |

Sampling is balanced across the three `expected_score` bands by construction —
`--limit 6` is 2/2/2, `--limit 0` is every item the set holds (60 as shipped, not
a hardcoded 20 per band), and an uneven limit gives the remainder to
`hard_failure` first. An unbalanced draw makes `fp0` and `fn0` uninterpretable:
six items that happen to be five passes estimate the false-alarm rate from one
item. A band with fewer items than the quota is still under-drawn rather than
raising — a short set should stay scoreable — but the shortfall is printed in the
dry run *and* under the report, because a `1/4` column read as a `1/6` overstates
how much evidence sits behind it.

**Three outcomes, not two.** An item either scored, broke the output contract, or
never got an answer:

| Outcome | Where it lands | Why |
|---|---|---|
| scored | every column | — |
| `unparseable` — an answer arrived and broke the contract (no JSON, no `score`, a word outside the scale) | its own line, **and** `n`, `fp0`/`fn0` and the band columns | This is the measurement. AgentCore owns the output contract here, and its failure rate is the number no other path can observe |
| `transport` — no answer arrived (`APIConnectionError`, a 429, a 401, a timeout) | its own line only, excluded from every column | A fact about the network or the account. Folded into `fn0` it would report an expired key as "this judge missed a hard failure", which is the most misleading thing this tool could say |

If a whole run is transport failures the table is empty, and the report says
`NOTHING WAS MEASURED` in words — an empty table is too easy to read as a clean
one.

A rating is matched after stripping the decoration the prompt itself uses. The
transformed instructions print the vocabulary backticked (`` `pass` ``,
`` `warning` ``, `` `hard_failure` ``), so a model echoing that formatting is
giving a correct answer in the prompt's own punctuation; charging it to the
contract-failure rate would inflate the one number this scorer exists to report
honestly.

**Cost.** A full sweep is 10 judges x 60 samples x k Opus calls at high effort.
`--dry-run` is the default whenever no API key is present and exits 0 without
importing the SDK; a live run prints the call count first — the true total, not a
per-judge mean — and refuses non-interactively unless `--yes` is passed. `--out`
is resolved, created and proved writable *before* the confirmation, so a typo'd
path costs nothing instead of costing the whole sweep and discarding its results.

**`capability-honesty-traced` is not in the default set, on purpose.** `deployable()`
keeps one side of every variant pair — a judge and its variant attached to the
same task double-count one defect — so the default draw is the 10 base judges.
The traced variant ships a full 60-row test set and `--judge
capability-honesty-traced` scores it explicitly; that is how you compare the pair.

### The two things it does not do

**It simulates AgentCore, it does not reproduce it.** On the real path AgentCore
builds the model call — it substitutes its own `{context}` / `{assistant_turn}`
from CloudWatch spans, appends its own standardization prompt, and calls Bedrock.
Here the transformed instructions become a system prompt, the rendered transcript
becomes the user message, and the standardization prompt is reconstructed from
what AgentCore documents rather than from anything observed. The perturbation
table below still stands unchanged: the shape of `{context}` remains undocumented
and inspecting it in Test Evaluator is still the first thing to do with an
account. Treat the numbers as this folder's regression baseline, not as a
prediction of production.

**It cannot tell you whether the judges are right.** `data/testsets/` is
model-written and model-labelled; see *What the shipped test data can and cannot
do*. A rising `exact` column after a rubric edit is evidence the edit did what
you meant, not evidence the rubric is valid.

### The `>>> TARGET` decision the transform forces

`MARKER_REWRITES` turns "the assistant turn marked `>>> TARGET`" into "the
assistant turn under evaluation", so the deployed prompt no longer says what
`>>> TARGET` means — while the transcript the rubrics were authored against still
emits it. Three options; the folder takes the third.

1. Drop the marker. Forks the transcript format and forfeits the cross-path
   comparison that is the point of running this at all.
2. Keep it and hope the model infers it. Makes the target-turn identification an
   untested inference in the one place a turn-level judge cannot afford one.
3. Keep the marker for format parity **and** repeat the target turn verbatim
   under `{assistant_turn}` — the channel the transformed prompt still names.

`build_prompt` does (3), which is why the user message names its blocks with the
placeholder tokens themselves rather than being a bare transcript: the transform
leaves `{context}` and `{assistant_turn}` unsubstituted and points at them by
name, so a bare transcript would leave every such reference dangling. SESSION
judges get `{context}` alone and their `target_turn` is dropped, because the
session prompt has no per-turn placeholder to point one at.

Each block is **fenced on both sides** — `[BEGIN {context}]` … `[END {context}]` —
and a one-line scoring tail follows the last one, so untrusted transcript text is
never the final thing the model reads. That is the job the parent suite's
`_scoring-tail.md` does, and this path lost the tail when AgentCore took over the
output contract, so it has to be rebuilt in the user message. The fences are named
after the placeholders deliberately: `transform.py` rewrites `[BEGIN TRANSCRIPT]`
/ `[END TRANSCRIPT]` *out* of the instructions, so that vocabulary no longer means
anything to the deployed prompt, while the placeholders are region names it still
defines. The transcript keeps emitting its own `[BEGIN TRANSCRIPT]` line inside
the fence — that is the format the rubrics were authored against, and it stays
byte-identical to the other two paths.

## Path-specific perturbations

| Perturbation | Requirement |
|---|---|
| Inspect one rendered prompt in Test Evaluator | `{context}` must actually contain the conversation — its shape is undocumented |
| Same, for a judge needing tool calls | If `{context}` omits tool calls, the honesty judges lose their subject matter entirely |
| Re-run an identical item | Expect variance; there is no k to smooth it |


## The five questions

They get conflated. They need different instruments, and only one tests *validity*.

| # | Question | Instrument | Needs people? |
|---|---|---|---|
| 1 | Does it agree with people? | Ordinal-weighted Krippendorff's α; the alt-test | **Yes** |
| 2 | Is it self-consistent? | Repeat the same input | No |
| 3 | Is it measuring what it claims? | Factor analysis over check vectors | No |
| 4 | Is it gameable? | Perturbation gates | No |
| 5 | Does it predict anything real? | Correlation with repeat-contact, escalation, CSAT | Production data |

Only #1 tests validity. A judge can be perfectly self-consistent, perfectly
distinct, perfectly robust — and perfectly wrong.

## Phase 0 — free, and it comes first

```bash
.venv/bin/python -m tests.test_suite     # 192 offline checks, no credentials
bin/native-judges diff        # exactly what the transform removes, per judge
bin/native-judges validate
bin/native-judges score --dry-run --limit 6   # every prompt built, nothing sent
```

Then the two things that cost nothing and are upstream of every measurement:

**Fix the trigger clauses.** When the 660 labelled samples in `data/testsets/`
were generated, an independent verifier read the same rubric and objected where
it disagreed. **22 of 68 objections were about `trigger_present`**, not about
scores — concentrated in `calibrated-hedging` (7), `capability-honesty-traced`
(4) and `signal-density` (4). Two capable models read those disjunctive trigger
clauses differently, so the clauses are the defect.

**Read the 10 genuine score disagreements.** Distinct from the above: 10 items
where the two models gave *different* scores. Each marks a band boundary that is
not sharp enough.

> The `contested` field in `data/testsets/` is misnamed — 68 rows carry it but
> only 10 are score disagreements. `verdict_counts.disagreed` is unreliable and
> should be ignored. Both are documented in `data/testsets/README.md`.

## Phase 3 — needs people, cannot be shortcut

**Measure SME-vs-SME agreement before measuring any judge.** Human experts reach
weighted κ ≈ 0.29 on the closest published empathy analogue. If two of your SMEs
agree at 0.62 on attunement, no judge can be held to 0.80 — and a validation memo
that omits the ceiling claims more than it measured.

Use **expert** labels, not crowd labels: agreement runs consistently higher
against non-experts, so validating against contractors and deploying against a
standard set by licensed representatives overstates what you measured.

## Phase 4 — the test that actually matters

Do the scores predict repeat-contact rate, escalation, CSAT, task completion?
Entirely separate from agreement, and the only evidence that acting on these
numbers improves anything. Needs production traffic, so it cannot be first — but
it should be scheduled, not left implicit.

## What the shipped test data can and cannot do

`data/testsets/` is **model-written and model-labelled**. Measuring agreement
against it is circular. It is good for **regression testing** after a rubric
edit, **self-consistency**, and **cross-judge isolation** — running every judge
over one judge's set, where only the owning judge should move. It is not a gold
set and must never be reported as one. The 20/20/20 split is enforced by
construction, so no rate computed on it estimates production.

## Recommended order

1. **Phase 0** — free. Offline suite, the transform/validate commands above, the
   22 trigger clauses, the 10 score splits.
2. **Cheap first run** — the 68 flagged items. Confirms the clause fixes and gives
   the first real numbers.
3. **Regression baseline** — the full 660, frozen, so every later change diffs
   against it.
4. **Cross-path agreement** — the same items through the other two folders. All
   three run rubrics derived from one source, so disagreement isolates the
   platform from the judge.
5. **Phase 3** — the SME ceiling, then the gold set. Everything above is
   reliability; this is the first validity evidence.
6. **Phase 4** — outcome correlation.

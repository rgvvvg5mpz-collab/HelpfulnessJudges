# Proposed testing

How to find out whether these judges are any good, in the order the tests should
be run, with what each one costs.

This is the **plan**. [validation-protocol.md](validation-protocol.md) is the
**standard** — the gold-set design, the agreement statistics, the drift
architecture, the ship gates. This file does not restate them; it says what to do
first, what it costs, and what we already know without spending anything.

> **Baseline: no judge in this suite has ever scored a real item.** Prompt
> assembly, schema derivation, aggregation, span reconstruction and request
> validation are tested offline. The rubrics themselves are unrun, on all three
> execution paths.

---

## The five questions

They get conflated. They need different instruments, and only one of them is a
test of *validity*.

| # | Question | Instrument | Needs humans? |
|---|---|---|---|
| 1 | Does it agree with people? | Ordinal-weighted Krippendorff's α; the alt-test | **Yes** — unavoidable |
| 2 | Is it self-consistent? | k=10 on identical input | No |
| 3 | Is it measuring what it claims? | Factor analysis over check vectors | No |
| 4 | Is it gameable? | The perturbation gates | No |
| 5 | Does it predict anything real? | Correlation with repeat-contact, escalation, CSAT | No, but needs production data |

**Only #1 tests validity.** The rest test reliability, distinctness, robustness
and usefulness — all necessary, none sufficient. A judge can be perfectly
self-consistent, perfectly distinct, perfectly robust, and perfectly wrong.

---

## Phase 0 — free, and it comes first

No API key. No AWS account. Do this before spending anything.

### 0.1 Fix the trigger clauses the generation run already exposed

When the 660 test samples were built, each judge had an independent verifier read
the same rubric and object where it disagreed. **22 of 68 objections were about
`trigger_present`** — not about scores:

| Judge | `wrong_trigger` objections |
|---|---|
| `calibrated-hedging` | 7 |
| `capability-honesty-traced` | 4 |
| `signal-density` | 4 |
| `effort-and-resolution-path` | 3 |
| `need-coverage` | 2 |
| `capability-honesty`, `emotional-accuracy` | 1 each |

That is not scattered noise. It is a concentrated signal that the ungated
design's trigger definitions are ambiguous — the one field added so a dimension's
rate could be read conditionally.

The worked example: `calibrated-hedging`'s trigger is disjunctive — *"the turn
hedges, defers, or refers the customer elsewhere, **OR** the question carried
genuine uncertainty."* The generator derived `trigger_present` from
`uncertainty_state` instead, which is the wrong key. Two capable models read that
sentence differently, so the sentence is the defect.

**Reading those 22 notes and sharpening the clauses costs nothing and is upstream
of every other test.** There is no point measuring agreement against rubrics two
models already read differently.

### 0.2 Read the 10 genuine score disagreements

Distinct from the above: 10 items where the two models gave *different scores*.

`actionability` ×2, `signal-density` ×2, and one each in `calibrated-hedging`,
`capability-honesty`, `conduct-adaptation`, `effort-and-resolution-path`,
`emotional-accuracy`, `plain-language-clarity`.

Each is a place a rubric's band boundary is unclear. `actionability-warn-09` is
the pattern: is naming a setting plus its exact navigation path a "specific
action" or a "category"? The rubric's C1 examples do not settle it.

### 0.3 Run the perturbation suite as generation, not scoring

The [ship gates](validation-protocol.md) need perturbed *inputs*. Generating them
— padded, reformatted, affect-stripped, truncated, fabricated-process variants of
existing samples — is deterministic string work. Build the corpus now; score it
when a key exists.

---

## Phase 1 — cheap, needs an Anthropic API key

Costs assume Opus 5 at `effort: high`, prompt caching across the k repeats, and
~2.5k output tokens per call including thinking.

| Experiment | Calls | Cost | What it buys |
|---|---|---|---|
| 68 flagged items, k=5 | 340 | **~$22** | Self-consistency on the hardest items; whether the judge resolves each contested case |
| One judge's 60 samples, k=3 | 180 | **~$12** | End-to-end proof, one judge, one number |
| 8 hand-written fixtures × 11 judges, k=5 | 440 | **~$28** | Cross-judge isolation — only the owning judge should move |
| Full 660 samples, k=5 | 3,300 | **~$194** | The regression baseline everything later diffs against |
| Perturbation suite (~200 pairs), k=3 | 1,200 | **~$70** | The ship gates |

**Start with the 68 at $22.** Highest information per dollar in the repo, and it
directly tests whether the Phase 0 clause fixes worked.

### What the 660 samples can and cannot tell you

They are **model-written and model-labelled**. Measuring agreement against them
is circular — a judge agreeing with a model that read the same rubric proves
nothing about validity.

They are genuinely good for: **regression testing** after a rubric edit,
**self-consistency** at k=10, and **cross-judge isolation**. They are not a gold
set and must never be reported as one.

---

## Phase 2 — the free instrument nobody had a week ago

Three execution paths now run **byte-identical rubrics**: the Anthropic harness,
`arize-integration/`, and `agentcore-integration/`. Scoring the same items three
ways isolates platform effects from judge effects.

| Result | Reading |
|---|---|
| All three agree | The rubric is doing the work; the platform is not distorting it |
| AgentCore ≈ harness, Arize diverges | The missing `effort: high` on Arize — a known, documented gap |
| AgentCore diverges from harness | Span reconstruction or the k-inside-Lambda budget — this integration's problem |
| All three disagree | The rubric is unstable; go back to Phase 0 |

This costs only the extra scoring runs and it is the only way to attribute a
score movement to the platform rather than the judge.

---

## Phase 3 — needs people, and cannot be shortcut

**Measure SME-vs-SME agreement before measuring any judge.** Human experts reach
weighted κ ≈ 0.29 on the closest published empathy analogue. If two of your SMEs
agree at 0.62 on attunement, **no judge can be held to 0.80**, and a validation
memo that omits the ceiling is claiming more than it measured.

Use **expert** labels, not crowd labels — agreement is consistently higher
against non-experts, so validating against contractors and deploying against a
standard set by licensed representatives overstates what you measured.

Then the gold set: 300–400 conversations, three disjoint splits, per-dimension
agreement with confidence intervals. [validation-protocol.md](validation-protocol.md)
§2–§4 has the design.

---

## Phase 4 — the test that actually matters

**Do the scores predict anything?** Correlate them against repeat-contact rate,
escalation-to-human rate, CSAT, and task completion.

This is entirely separate from SME agreement, it is the only evidence that acting
on these numbers improves anything, and almost nobody does it. It needs
production traffic and a few months, so it cannot be first — but it should be
scheduled, not left implicit.

---

## Known defects in the existing test data

Recorded so nobody trusts these numbers further than they go.

**The `contested` field is misnamed.** 68 rows carry it, but only **10** are
score disagreements. The other 58 are same-score objections — `wrong_trigger`
(22), `not_diagnostic` (15), `duplicate` (14), `strawman` (5). `flagged` would
have been the honest name.

**`verdict_counts.disagreed` is unreliable — ignore it.** The verifier agents were
asked for it as a free integer rather than having it derived from `flagged`, so
nothing forced consistency. It totals 39, matching neither 68 nor 10, and
mismatches per-judge in 6 of 11 cases. The `flagged` array is the trustworthy
artifact because every entry carries its evidence.

**Two rows are internally incoherent**: labelled `wrong_label` while recording the
same score for both models. Adjudicate them by hand or drop them.

**The label distribution is enforced, not observed.** Every set is exactly
20/20/20 by construction. Real traffic will not be, so any rate computed on these
samples is meaningless as a production estimate.

---

## Recommended order

1. **Phase 0** — free. Fix the 22 trigger clauses, read the 10 score splits,
   generate the perturbation corpus.
2. **$22** — the 68 flagged items at k=5. Confirms the clause fixes and gives
   the first real self-consistency numbers.
3. **$194** — the full 660 at k=5, frozen as the regression baseline.
4. **Phase 2** — cross-platform agreement, once two paths can run.
5. **Phase 3** — the SME ceiling, then the gold set. Everything above is
   reliability; this is the first validity evidence.
6. **Phase 4** — outcome correlation, scheduled from the start even though it
   reports last.

Steps 1–3 cost about $220 and a few days. Step 5 is the expensive one, and no
amount of steps 1–4 substitutes for it.

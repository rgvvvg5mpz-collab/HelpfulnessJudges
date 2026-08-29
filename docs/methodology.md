# Methodology: how these prompts are built, and why

> **v2.** The suite is ten independent, ungated judges on a three-level scale.
> The compliance-grounded guardrail tier was removed at request; two of its four
> judges returned re-grounded in CX and honesty research (§12). Sections 3, 4 and
> 6 changed substantially; the rest is unchanged.

Every structural decision below traces to a specific finding in
[literature-review.md](literature-review.md). Where the literature is split, the
choice made here is stated as a choice, with the reasoning.

---

## 1. One judge per construct

Ten prompts, each scoring exactly one dimension. Not one "helpfulness" judge
with twelve sub-criteria.

FLASK measured the gain directly: skill-specific evaluation raised human–model
Spearman from 0.641 to 0.680 on the same annotations. Beyond the accuracy gain,
separate judges give you three things an omnibus score cannot: a per-dimension
agreement figure (so you know which judges you can trust), a per-dimension drift
dashboard, and the ability to retire or replace one rubric without invalidating
the rest.

The cost is real — ten calls where one would do — and §7 is about managing it.

Judges are also **mutually independent**: no judge reads another's output, and no
judge's scope depends on another's verdict. `test_turn_policy.py` asserts that
every turn-unit judge scores an identical turn set, which is the property that
would silently break if a gate crept back in.

## 2. Binary checks first, band second

Each rubric decomposes into 5–7 yes/no questions about observable text features,
then maps the count to a 1–5 band. CheckEval reports **+0.45 average
cross-evaluator agreement** from exactly this move, and names "subjective
criteria + Likert scale" as the root cause of judge inconsistency. HealthBench
runs 48,562 physician criteria the same way.

Three rules the rubrics follow:

- **A check must be answerable from a span.** *"Does the acknowledgment name a
  specific emotion the customer raised?"* is a check. *"Is the response
  empathetic?"* is not.
- **Checks are answered independently**, each with the span that decides it. The
  output schema enforces one `{id, verdict, span}` object per declared check —
  the model cannot skip one.
- **The written level description governs, not the count.** The count is the
  starting point; the band whose prose description matches what was observed
  wins. This is Prometheus's contract, which reached Pearson 0.897 with humans
  across 45 custom rubrics.

## 3. Three levels: 0, 0.5, 1.0

Hard failure, warning, pass. Not 1–5, not 1–10.

The reliability case is strong. CheckEval's +0.45 cross-evaluator agreement gain
came precisely from collapsing subjective Likert judgment into binary decisions,
and HealthBench grades 48,562 physician criteria as binary met/not-met. A
three-level scale has almost no room for the centre-collapse Stureborg et al.
document, where an unanchored 1–5 piles everything on 4 and detects nothing.

The honest counter-evidence: Li et al. compared 0–5, 0–10, 0–100 and binary and
found **0–5 gave the highest ICC alignment with humans** — so this scale is not
the ICC-optimal choice, and that trade is deliberate. What buys it back is that
per-item resolution moves from the score to the check vector, which is where the
rubric literature says fine-grained signal belongs anyway. Six or seven binary
checks carry far more information than a single 1–5 integer, and they are
auditable one at a time.

**The uniform mapping rule**, stated identically in every rubric:

- Any bright line named in the rubric fires → **0**
- Otherwise all checks pass → **1.0**
- Otherwise → **0.5**

Two properties follow, and both are design goals. First, **0 is never a
judgment call** — it requires a named bright line, enumerated per dimension, so
the most consequential verdict is the least discretionary. Second, **0.5 is the
ordinary outcome for imperfect work**, which keeps the judge from being forced to
decide whether something is a 3 or a 4 — the decision that produces most of the
noise in Likert judging.

Where resolution is genuinely needed — A/B testing a prompt change — it comes
from the population mean. A mean of 0/0.5/1.0 over 200 conversations is
continuous and as sensitive as anything a 1–5 scale would give you.

## 4. No gates, and how the metric stays sensitive anyway

Every judge scores every item of its declared unit. There is no not-applicable
outcome, no pre-filter deciding whether a judge runs, and no judge that depends
on another judge's verdict.

This removes a real source of complexity — gated judges made the suite's
production rates hard to read, since a moving mean could be a behavior change or
a change in how often the gate fired. It creates one problem, which has to be
solved rather than accepted.

**The problem.** If "nothing to evaluate" maps to a pass, `empathic-attunement`
over mostly-transactional traffic sits near 1.0 regardless of how the assistant
treats distressed customers. The metric goes insensitive exactly where it matters.

**Solution one: every rubric is two-sided.** A dimension can be failed by
*manufacturing* it where it did not belong, not only by omitting it. This is not
a workaround — it is a better rubric, and it is only available once gating is
gone:

| Judge | Failure by omission | Failure by imposition |
|---|---|---|
| `empathic-attunement` | Cold on real distress | Emotional preamble on a one-line factual question (C7) |
| `emotional-accuracy` | Misreads the feeling | Invents a feeling the customer never expressed (C1) |
| `actionability` | No executable step | Manufactured next step where none was called for (C6) |
| `plain-language-clarity` | Unglossed jargon | Explains basics to someone who showed expertise (C2) |
| `calibrated-hedging` | Cop-out referral | Disclaimer on a settled published fact (C4) |
| `need-coverage` | Multi-part ask half-answered | Answers a question the customer did not ask (C1) |
| `conduct-adaptation` | Standard flow over a clear struggle signal | Slowing down at a customer who was managing fine (C7) |
| `capability-honesty` | Overstates what it did or can see | Volunteers self-description the answer never needed (C7) |

`signal-density` is the deliberate exception: it is one-sided, because a terse
turn is charged by `need-coverage` and charging it twice would corrupt the
opposed pair. Its rubric says so explicitly so the judge does not invent a
terseness penalty.

**Solution two: `trigger_present` as a reporting key.** Every verdict records
whether the condition the dimension is most at risk on appeared. It never changes
the score — it is not a gate — but it lets a reader ask "what is attunement on
the turns where someone was distressed?" without dilution. The aggregator emits
`trigger_rate` per item.

## 5. Evidence before verdict, verdict before score

Fixed output order: `evidence` → `checks` → `reasoning` → `score` →
`confidence`.

Rationale-before-score is the defensible choice in a regulated deployment: the
score is generated in the context of the reasoning rather than the reasoning
being confabulated to fit a number already emitted. The literature is genuinely
split — some essay-scoring work reports score-first is more stable — so this is
a choice made on auditability, not on measured accuracy.

The reasoning is **constrained**, not free-form: checklist verdicts with spans,
then a few sentences referencing check ids. Free-form CoT drifts; anchoring to
cited spans is what the rubric survey identifies as improving trustworthiness.
It also makes a judge/human disagreement diagnosable — you can see *which check*
they split on.

## 6. What the judge sees, what it scores, and what you pay for

Three different questions that get conflated.

### What it sees: always the whole conversation

Zheng et al. found that splitting a multi-turn exchange across separate judge
calls makes the judge mis-locate the assistant's prior turns and produce faulty
references; presenting the full conversation in one prompt with an instruction to
focus on a target turn "significantly alleviated" it.

So every call renders the entire transcript between `[BEGIN TRANSCRIPT]` and
`[END TRANSCRIPT]`, with the scored turn marked `>>> TARGET`. This is fixed in
`harness/transcript.py` and is not configurable. A turn like *"Yes, that's right,
and it'll settle Tuesday"* is unscoreable without its history.

### What it scores: turn or conversation, fixed per construct

| Unit | Judges | Why |
|---|---|---|
| **turn** (8) | `empathic-attunement`, `emotional-accuracy`, `need-coverage`, `signal-density`, `actionability`, `plain-language-clarity`, `calibrated-hedging`, `capability-honesty` | The defect lives in a sentence and can be pointed at. |
| **conversation** (2) | `effort-and-resolution-path`, `conduct-adaptation` | The defect only exists across turns. |

Neither conversation judge is turn judges averaged. A doom loop requires the
customer to ask twice and the assistant not to change strategy — invisible at
every individual turn. And `conduct-adaptation` asks whether a signal disclosed
in turn 2 changed conduct in turns 3 through 6, which is a comparison between
turns rather than a property of any one of them: its central test is *would this
conversation have gone the same way without the signal?*, and that question has
no single-turn form.

The fixture `data/gold/examples.jsonl` contains `conv-doom-loop-003` for exactly
this reason: every individual turn in it is polite, accurate and on-topic, and
the turn judges score it acceptably. Only the conversation judge sees that turns
2 and 4 are the same answer and turn 6 ignored an explicit request for a human.

### What you pay for: two modes, neither of them a gate

`harness/turn_policy.py` offers `all` (every assistant turn — the default) and
`last` (final assistant turn only, for production trend sampling).

This is a **coverage trade, not a gate**: it changes how many items you measure,
never what a judge would say about an item it is given. But the two modes draw
from different populations — final turns skew toward resolutions and handoffs —
so any reported rate must state which mode produced it.

Cost is real and worth planning for. Ten judges × every assistant turn × k=5
over a six-turn conversation is 100+ calls. The levers, in order: drop k to 1 for
online trend monitoring where aggregate means average out; use `--turns last`;
use the Batches API at 50%; and reserve `--turns all` with k=5 for the frozen
offline regression suite where set size bounds the cost.

## 7. Bias controls, in order of expected damage

The four MT-Bench debiasing sentences are in the shared preamble, and they are
**necessary but nowhere near sufficient** — the 2026 mitigation study found
style-blind instructions alone have minimal effect. The layered controls:

**Style and formatting (the largest bias).** Four of five judges showed
near-unanimous markdown preference on content-equivalent pairs where humans
preferred it only 0.57 of the time. Mitigations applied: every rubric is written
as a **comprehension or execution outcome**, never as a formatting feature
(*"could a first-time 401(k) participant execute the next step without
re-reading?"*, not *"is it well-organized?"*). `signal-density` caps at 3 any
turn whose formatting scaffolding exceeds the content it organizes.
`plain-language-clarity` states the trap explicitly in the prompt.

**Verbosity.** `need-coverage` is written as coverage of an *enumerated
requirement set* rather than as depth or thoroughness — element coverage is
length-independent in a way that "detailed" is not. `signal-density` is its
designed opposite. The two are never traded off inside one judge. At the
aggregate level, log response token count with every score and regress it out
(Dubois et al.'s length-controlled approach).

**Position.** Sidestepped rather than mitigated: the suite is **pointwise
absolute scoring**, not pairwise. Tripathi et al. found pairwise preferences flip
~35% under distractor manipulation versus ~9% for absolute scores, and position
bias is *worst* when candidates are close — the normal case in prompt A/B
testing. If you do run pairwise for a large-gap comparison, run both orders and
call disagreement a tie.

**Self-preference.** Panickssery et al. show a linear relationship between
self-recognition and self-preference; Zheng et al. measured 10–25% inflation.
**The judge model must be from a different family than the chatbot.** If both are
the same vendor's models, the judge is grading its own homework. See §9.

**Fluency-as-empathy.** HEART found LLM judges over-reward generic reassurance
and polished prose. `empathic-attunement` therefore hard-caps portable warmth at
3 no matter how well written, and instructs the judge that fluency is not
attunement.

## 8. Injection hardening

Customer transcripts are attacker-controlled text. JudgeDeceiver showed
optimized injections defeating perplexity-based and known-answer detection;
a companion study measured >30% attack success on unhardened judges and
identified a separate **justification-manipulation attack** that poisons the
rationale while leaving the score plausible — which matters if rationales reach
compliance reports.

Five structural defenses, all applied:

1. **Delimiters.** Content is confined to `[BEGIN TRANSCRIPT]` /
   `[END TRANSCRIPT]`.
2. **Data-not-instructions framing** in the shared preamble, naming the specific
   attacks: fake system messages, fake rubrics, fake closing delimiters.
3. **The scoring tail comes after the transcript.** The instructions the model
   acts on last are not followed by attacker-controlled text. This is why the
   rubric is restated in the user message tail even though it is already in the
   cached system block — see §9 for the caching trade this makes.
4. **Schema-constrained output.** `output_config.format` with a strict JSON
   schema; anything that does not parse is rejected rather than salvaged.
5. **Evidence-span verification.** Every claim must quote a verbatim span. A
   post-hoc check that cited spans are actual substrings of the transcript is a
   cheap, powerful detector — it is on the roadmap in
   [validation-protocol.md](validation-protocol.md) and is not yet implemented.

Plus an explicit `injection_suspected` boolean in every verdict, which routes the
item to human review.

`data/gold/examples.jsonl` includes `conv-injection-006` as a standing control.

## 9. Model, sampling, and caching

**Model.** Pinned to an exact id, never a floating alias. A silent vendor model
change is indistinguishable from a chatbot regression on the dashboard. The
default is `claude-opus-5`; **change it if the production chatbot is also a
Claude model** — self-preference is measured at 10–25% and cross-family judging
is the mitigation. This is a live decision for Vanguard, not a default to accept.

**k and aggregation.** Default k=5, median-aggregated. *The Coin Flip Judge*
measures a 13.6% average verdict flip rate on identical repeated input, with 28%
of items above 20%. Temperature 0 is not reproducibility. The **spread travels
with the score**: an item scored 2/4/5 is an item for human review, not an item
with a score of 4. `aggregate()` emits `spread`, `stable`, and
`needs_human_review` on every row.

Use k=1 only for high-volume online trend monitoring where aggregate means
average out — never for an item reported to a stakeholder or gating a release.

**Caching.** The system block holds the shared preamble followed by the judge
rubric, both byte-stable across every conversation, with the cache breakpoint at
the end. The shared preamble is identical across all twelve judges and comes
first, so it stays cached across judges as well as across conversations. Only the
transcript varies, and it sits in the user message after the breakpoint.

The trade: injection hardening wants the operative instructions after the
untrusted content, and caching wants stable content first. Resolution — the
rubric lives in the cached system block *and* the scoring tail restates the
authority ordering and output contract after the transcript. Both properties,
one duplicated paragraph.

Verify with `usage.cache_read_input_tokens`. If it is zero across repeated runs,
something is invalidating the prefix.

**Batch.** The offline regression suite and gold-set scoring go through the
Batches API at 50% cost. Results come back in arbitrary order — the runner keys
by `custom_id`, never by position.

## 10. Reproducibility

Every score is written with `judge_version`, `judge_prompt_hash`, `model`,
`effort`, `k`, and `sample`. A score you cannot re-derive is not evidence.

Rubrics are versioned artifacts under change control. *The Coin Flip Judge* found
semantically equivalent prompt templates flip majority outcomes in **25%** of
cases — a comma is a code change. Every rubric edit requires a re-run against the
frozen anchor set and a documented before/after agreement delta. **Never change a
rubric and a judge model in the same release**; you will not be able to attribute
the movement.

## 11. What is deliberately NOT an LLM judge

Measure these deterministically. Sending them to a judge costs money and adds
noise to a number you could have computed exactly:

- Response latency, containment rate, escalation rate, session length
- Link and URL validity; whether a cited form number exists
- Presence of a required disclosure string
- PII appearing in the transcript (regex/NER, not a judge)
- Stated length or format constraints from IFEval-style verifiable instructions
- Turn counts, repeat-contact rate within N days
- **Factual accuracy.** Not in this suite at all. Correctness against Vanguard's
  own plan data, fee schedules, and published limits is a retrieval-grounded
  check against systems of record, not a helpfulness judgment. `need-coverage`
  scores coverage of a wrong answer as coverage.
- **Compliance and regulatory boundaries.** `reassurance-integrity` and
  `advice-boundary-discipline` were removed at request. Nothing in the suite now
  catches a promissory claim about market recovery or an implicit securities
  recommendation — see §12. Assume another suite owns them.


## 12. What removing the guardrail tier gave up, and what came back

Recorded here so the decision stays visible rather than becoming an unexamined
default.

The four removed judges were not compliance checks bolted onto a helpfulness
suite. They were the specific failure modes of optimizing the eight that remain,
and each lives inside a response the remaining judges score well:

| Optimizing this | Produces this | Formerly caught by |
|---|---|---|
| `empathic-attunement` | *"Markets always recover — you'll be fine."* Warm, specific, attuned, and a performance projection. | `reassurance-integrity` |
| `actionability` | Specificity that resolves into naming a fund as right for this customer. | `advice-boundary-discipline` |
| `need-coverage` + warmth | Narrated diligence — *"I've reviewed your complete financial picture"* — that never happened. | `capability-honesty` |
| `empathic-attunement` on distress | Kind words wrapped around unchanged conduct on a bereavement or a red flag. | `vulnerability-responsiveness` |

### What came back

Two of the four returned, re-grounded away from regulation entirely. The
constructs survive the reframing because they were never really regulatory — the
rules were describing a service failure that exists on its own terms.

**`vulnerability-responsiveness` → `conduct-adaptation`.** The supervisory
framing (specified adults, exploitation red flags, trusted contacts) is gone. It
is now built on effort research, SERVQUAL responsiveness, chatbot-repair work,
and the emotional-support strategy literature, and it asks one question: *would
this conversation have gone the same way if the customer had not said they were
struggling?* The reframing also made it **two-sided**, which the regulatory
version could not be — over-accommodation is now a scored failure, because
treating a competent adult as fragile is a real service defect that no
supervisory expectation describes.

**`capability-honesty`** kept its name and lost its citations. Disclosure law is
out; the labor-illusion finding is in. It is in a *helpfulness* suite because
narrating work raises perceived value, which means an assistant can inflate every
other judge here by claiming work it never did. That is a measurement-integrity
argument, not a compliance one, and it holds regardless of what any regulator
requires. It also gained the same two-sidedness: volunteering self-description
the answer did not need is now a scored failure alongside overstating.

### What did not come back

`reassurance-integrity` and `advice-boundary-discipline` have no non-regulatory
grounding worth having. What makes *"markets always recover"* a defect is a rule
about projections, and what makes naming a fund a defect is a rule about
recommendations. Strip the rule and there is no construct underneath — a customer
would call both responses helpful. That is exactly why they need a suite that
owns them, and why this one cannot substitute.

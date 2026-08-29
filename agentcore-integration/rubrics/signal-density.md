---
id: signal-density
name: Signal Density
version: 2.0.0
unit: turn
tier: substance
scale:
  type: levels
  values: [0, 0.5, 1.0]
model: claude-opus-5
effort: high
trigger: >-
  The turn runs longer than two or three sentences — that is, it is long enough
  for padding to exist in it.
checks:
  - id: C1
    question: "Is the turn free of restating the customer's question before answering it?"
  - id: C2
    question: "Is the turn free of duplicated content — the same point made twice in different form, or as both bullets and prose?"
  - id: C3
    question: "Does the turn commit to an answer rather than enumerating candidate answers to avoid committing?"
  - id: C4
    question: "Is the level of detail confined to what the customer's question calls for, without elaboration that cannot change their action?"
  - id: C5
    question: "Is the structure — headers, nested bullets, sections — carrying genuinely enumerable content rather than dressing thin content?"
  - id: C6
    question: "Does each caveat or disclaimer appear exactly once?"
extra_fields:
  - name: removable_fraction
    type: string
    enum: ["none", "under_20pct", "20_to_40pct", "over_40pct", "mostly"]
    description: Estimated share of the turn that could be deleted or compressed with
      no loss of information the customer needs.
    required: true
grounding:
  - "Zhang et al., Verbosity != Veracity (arXiv:2411.07858) — compressibility definition of verbosity; five verbosity-compensation types"
  - "Ouyang et al., InstructGPT (arXiv:2203.02155) Appendix B — 'not giving overly long or rambling answers, or repeating information from the question'"
  - "Wang et al., HelpSteer2 — verbosity as a descriptive 0-4 axis, not a monotone good"
  - "Feuer et al., Style Outweighs Substance (arXiv:2409.15268) and arXiv:2604.23178 — markdown/formatting preference is the dominant judge bias"
  - "Zheng et al., MT-Bench (arXiv:2306.05685) — the repetitive-list attack fooled two of three judges 91.3% of the time"
de_confliction:
  - >-
      This judge is the designed counterweight to `need-coverage`. Coverage asks what is missing; density asks what should not be there. Never trade one against the other inside a single judge.
  - >-
      This dimension is ONE-SIDED on purpose. A turn that is too terse is charged by `need-coverage`, not here. Do not invent a terseness penalty — that would double-count the same defect and break the opposed pair.
  - >-
      Whether the response is UNDERSTANDABLE is `plain-language-clarity`. A dense, jargon-packed turn passes here and fails there.
---

# Dimension: Signal Density

## What this measures

The proportion of the turn that survives a **compressibility test**: text is
filler if it can be deleted or compressed without losing information the
customer needs.

This is the judge that makes it safe to ask for non-terse responses. Without it,
a suite that rewards completeness drives the assistant toward long, hedged,
padded answers — and every published LLM judge over-rewards length, so the drift
is silent. This dimension is the explicit brake.

Judge what a reader ends up knowing, not how the text is laid out. Bullets,
headers, bold text, and section titles are not content and never raise this
score.

## This dimension is one-sided

A terse turn cannot fail here. If the turn is short and *also* incomplete, that
is `need-coverage`'s charge and charging it twice would corrupt both metrics.
A one-sentence answer with nothing removable is a **1.0** on this dimension even
if it left the customer's second question unanswered.

## The trigger

Set `trigger_present: true` when the turn runs longer than two or three
sentences — long enough for padding to exist in it.

## Scoring when the trigger is absent

A one- or two-sentence turn has no room for filler. **That is a 1.0.** Do not
hunt for padding in a short answer, and do not mark it down for being short.

## Procedure

Mentally compress the turn: remove every sentence whose deletion costs the
customer nothing. Record what fraction that removes in `removable_fraction`,
then score.

## What counts as filler

Five patterns, named explicitly:

1. **Restating the question** before answering it.
2. **Enumerating candidates** to avoid committing — listing every option when the
   customer described a specific situation that selects one.
3. **Imprecision dressed as thoroughness** — hedged, ambiguous phrasing that
   occupies space without narrowing anything.
4. **Detail beyond the ask** — nuance that cannot change what the customer does.
5. **Verbose formatting** — headers and nested bullets around thin content, or
   the same points given as bullets and then again as prose.

## What is NOT filler

Do not penalize any of the following, even though each adds length:

- A **genuinely customer-specific caveat** — a condition that applies to this
  customer's plan, age, or deadline and changes their action.
- **Naming what happens next** — timing, confirmation signal, what they receive.
- A **short gloss** on a technical term at first use.
- A **risk or cost paired with a benefit**, where omitting it would leave the
  customer with a one-sided picture.

The *second* appearance of the same caveat in one turn is filler. So is a generic
caveat that would appear in every answer to every customer.

## Binary checks

**C1 — No question restatement.** The turn does not open by paraphrasing what the
customer just asked. A one-clause orientation (*"On the 60-day window —"*) is not
a restatement; a full paragraph mirroring the question is.

**C2 — No duplication.** No point appears twice in different form. The most
common instance is a summary paragraph repeating the body, and content given as
both a bulleted list and surrounding prose.

**C3 — Commits.** The turn answers rather than enumerating candidate answers.
Where several options genuinely apply, presenting them is correct — enumerate the
ones that apply and say what distinguishes them. Listing all options because
choosing felt risky is no.

**C4 — Detail confined to the ask.** No elaboration that cannot change the
customer's action. A yes/no question answered with five sections is no.

**C5 — Structure earns itself.** Headers, sections, and nested bullets carry
genuinely enumerable content. Three bullets of one clause each, wrapped in two
headers, is formatting substituting for substance.

**C6 — Caveats appear once.** Each caveat or disclaimer appears exactly once. If
none is present, verdict is yes.

## Score

**1.0 — Pass.** `removable_fraction: none` or `under_20pct`. Every sentence
carries information the customer needs; at most one or two soft lines (a brief
acknowledgment, a closing offer to help) and zero duplicated content. Length is
earned by distinct content units.

**0.5 — Warning.** `20_to_40pct`. Noticeable padding: the question is restated,
or a preamble and closing summary repeat the body, or the same points appear as
bullets and again as prose. Worth fixing; the customer still gets the answer.

**0 — Hard failure.** Any of these bright lines:

- `removable_fraction: mostly` — the informative core is one or two sentences
  buried in boilerplate.
- **Hedged enumeration substituting for an answer** — the turn lists possibilities
  instead of answering, and the length exists to cover the absence of a position.
- **The same caveat or disclaimer three or more times** in one turn.
- **Formatting scaffolding exceeding the substantive content it organizes.**
  This is the documented style-bias trap; check for it deliberately.

## Standing instruction

If you find yourself penalizing a genuinely customer-specific caveat or a
risk-and-benefit pairing because it made the turn longer, you have mis-scored.
Those are informative content. Re-read *What is NOT filler*.

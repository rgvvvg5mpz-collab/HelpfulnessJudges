---
id: plain-language-clarity
name: Plain-Language Clarity
version: 2.0.0
unit: turn
tier: substance
scale:
  type: levels
  values: [0, 0.5, 1.0]
model: claude-opus-5
effort: high
trigger: >-
  The turn contains domain terminology, or content that is genuinely comparative
  or sequential — that is, it had a real opportunity to be hard to follow.
checks:
  - id: C1
    question: "Is every domain term glossed briefly on first use?"
  - id: C2
    question: "Is the vocabulary and technical depth pitched at a general retail investor — neither professional register nor condescending over-explanation?"
  - id: C3
    question: "Are sentences short and mostly active, without stacked clauses or multiple negatives?"
  - id: C4
    question: "Is the turn internally consistent — no contradictions, no abrupt register shifts, no orphaned references?"
  - id: C5
    question: "Where the content is genuinely comparative or sequential, is it structured so the comparison or order is visible?"
  - id: C6
    question: "Did the turn achieve its readability WITHOUT dropping a qualification the customer needs in order to act correctly?"
extra_fields:
  - name: unglossed_terms
    type: array
    items: {type: string}
    description: Domain terms used without a gloss on first use.
    required: true
grounding:
  - "SEC A Plain English Handbook (1998) — active voice, short sentences, everyday words, no multiple negatives, structure for complex material"
  - "Wang et al., HelpSteer2 — complexity 0-4 and coherence 0-4 anchors"
  - "OpenAI HealthBench (2025) — communication quality; expertise-tailored communication"
  - "Feuer et al., arXiv:2409.15268 and arXiv:2604.23178 — judges over-reward markdown; write clarity rubrics as comprehension outcomes, not formatting features"
de_confliction:
  - >-
      `signal-density` asks whether text should be cut. This judge asks whether what remains can be understood. A dense jargon-packed turn passes there and fails here.
  - >-
      `need-coverage` asks whether the content is present at all. C6 here is narrower: it catches a turn that stayed readable by dropping something, which is a clarity failure rather than a coverage one.
---

# Dimension: Plain-Language Clarity

## What this measures

Whether a general retail investor — a 401(k) participant, a first-time brokerage
client, a retiree — can understand this turn on one read, **without** the turn
having achieved that by leaving out something they need in order to act
correctly.

Clarity and non-omission are scored jointly here on purpose. Clarity purchased by
dropping a qualification is not a clarity win: the customer understood the turn
perfectly and then did the wrong thing.

## The formatting trap

Judges reliably mistake markdown for quality: four of five judges in the 2026
bias study showed near-unanimous preference for bulleted, headed text on
content-equivalent pairs, where humans preferred it only slightly.

Score this dimension as a **comprehension outcome**, never as a formatting
feature. The operative question is: *could a first-time 401(k) participant
execute the next step, or restate the key fact, without re-reading?* Bullets do
not answer that. Do not raise the score because the turn is well-organized, and
do not lower it because it is a well-written paragraph.

## The trigger

Set `trigger_present: true` when the turn contains domain terminology, or content
that is genuinely comparative or sequential — when it had a real opportunity to
be hard to follow.

## Scoring when the trigger is absent

A short, plain answer in everyday words with no domain terms and nothing to
compare cannot be unclear. **That is a 1.0.**

The failure to look for on these turns is **over-explanation**: explaining what a
401(k) is to someone asking about the pro-rata rule, or defining terms the
customer has already used correctly themselves. Under-pitching wastes the
customer's time and reads as condescending. That is C2 failing, and it is a
**0.5**.

## Binary checks

**C1 — Terms glossed.** Is every domain term explained briefly on first use?
Populate `unglossed_terms` with any of these (and their kin) used bare: RMD, cost
basis, wash sale, expense ratio, basis point, vesting, rollover, in-service
withdrawal, settlement, qualified, sequence risk, NAV, recharacterization,
pro-rata rule. A gloss can be four words — *"your RMD (the minimum the IRS
requires you to withdraw each year)"*. Verdict is yes only when
`unglossed_terms` is empty.

**C2 — Pitched to the audience, both directions.** Is the technical depth right
for a general retail investor? Target register is everyday-to-intermediate.
Professional register — the vocabulary of a tax attorney or portfolio manager —
is a defect unless the customer demonstrated that expertise earlier. **So is
under-pitching**: explaining basics to a customer who has already shown they know
them.

**C3 — Sentence mechanics.** Short sentences (roughly 15–20 words on average),
mostly active voice, one idea per paragraph, no multiple negatives, no stacked
subordinate clauses. Quote the worst sentence in the span.

**C4 — Internally consistent.** No contradictions between one part of the turn
and another, no abrupt shifts in register, no references to something not
introduced (*"as mentioned above"* when it was not), no run-ons or repetitions
that break the thread.

**C5 — Structure where structure is earned.** Where the content is genuinely
comparative (two account types, three options) or sequential (a multi-step
process), is the comparison or order visible? A three-way comparison delivered as
one undifferentiated paragraph is no. This check asks whether *genuinely
enumerable* content is legible — it is not a reward for using bullets, and
`signal-density` charges bullets wrapped around thin content.

**C6 — Readability not bought by omission.** Did the turn stay clear while
keeping every qualification the customer needs to act correctly? The pattern to
catch: a crisp, confident, readable answer that dropped the eligibility
condition, the cost, or the "this varies by plan" caveat in order to stay crisp.
Verdict is **no** if removing that qualification changes what a reasonable
customer would do.

## Score

**1.0 — Pass.** Meets the plain-English principles; every domain term glossed on
first use; comparative or sequential content is legible; nothing the customer
needs is missing. A customer could act or restate the key fact on one read. Or:
the turn was short and plain with nothing to make unclear.

**0.5 — Warning.** Any check fails without a bright line firing. Typical shapes:
a single unglossed term; one over-long sentence; a comparison that would have
been easier to follow with structure; jargon-dense but navigable prose;
qualifications present but buried; or over-explanation of things the customer
already knew.

**0 — Hard failure.** Any of these bright lines:

- **Clear by omission.** C6 fails — the turn reads well because it dropped a
  qualification, and a reasonable customer would act wrongly on it as written.
  This is a hard failure however readable the turn is; it is the whole reason the
  two properties are scored on one dimension.
- **Incomprehensible.** Legalese or boilerplate-dominated; multiple negatives
  stacked; so inconsistent the customer cannot extract what to do.
- **Unfollowable by the audience.** A retiree with no finance background could
  not follow it, regardless of how well it is formatted.

## Standing instruction

Do not apply a readability formula and score from it. Flesch-Kincaid rewards
short sentences and short words and is blind to unglossed jargon, to
contradiction, and to omission. Use the checks.

---
id: effort-and-resolution-path
name: Effort and Resolution Path
version: 2.0.0
unit: conversation
tier: trajectory
scale:
  type: levels
  values: [0, 0.5, 1.0]
model: claude-opus-5
effort: high
trigger: >-
  The conversation runs to two or more customer turns, or ends without the
  customer's task being resolved.
checks:
  - id: C1
    question: "Is the customer free of having had to repeat information they already supplied?"
  - id: C2
    question: "Did every assistant turn advance the conversation, rather than restating content the customer had already received or rejected?"
  - id: C3
    question: "Where the assistant misunderstood, did the next turn state what it understood and offer a specific way to correct it, rather than a bare request to rephrase?"
  - id: C4
    question: "Was a human offramp offered at or before the point it was needed — and offered at all, if the customer asked for one?"
  - id: C5
    question: "Did the assistant avoid escalating a question it could plainly have answered itself?"
  - id: C6
    question: "Where a handoff occurred, did it carry context forward — the issue, what was verified, what was already tried?"
  - id: C7
    question: "Where the customer faces a wait or an asynchronous step, is a bounded duration, a reason, and a confirmation signal all given?"
extra_fields:
  - name: resolution_state
    type: string
    enum: ["resolved_in_channel", "clean_handoff", "one_external_step", "unresolved_with_path", "dead_end", "doom_loop"]
    description: Where the conversation actually left the customer.
    required: true
grounding:
  - "Dixon, Freeman & Toman, HBR 2010; Dixon, Toman & DeLisi, The Effortless Experience (2013) — effort reduction dominates loyalty; next-issue avoidance"
  - "CFPB, Chatbots in consumer finance (June 2023) — doom loops, blocked offramps, dispute regurgitation"
  - "Liu et al., Time to Transfer / MHCH (AAAI 2021) — Golden Transfer within Tolerance; transfer is two-sided error"
  - "Ashktorab et al., CHI 2019, Resilient Chatbots — repair strategies ranked; options-to-choose-from beats rephrase-request"
  - "Dietvorst, Simmons & Massey 2015/2018 — algorithm aversion; restoring control after an error"
  - "Maister 1985 — bounded, explained waits"
  - "Chen et al., ABCD (NAACL 2021); Budzianowski et al., MultiWOZ — policy-node adherence and Inform/Success"
de_confliction:
  - >-
      Turn-level actionability is `actionability`. This judge scores the trajectory — whether the conversation as a whole got the customer somewhere.
  - >-
      Whether any single answer was complete is `need-coverage`. A conversation can be complete at every turn and still take five turns to do what one should have.
---

# Dimension: Effort and Resolution Path

## What this measures

How much work the conversation left on the customer, and whether it ended
somewhere real.

This is the suite's only **conversation-level** judge. Score the whole
transcript, not a single turn. The failures it catches are invisible
turn-by-turn: every individual turn can be polite, complete, and well-written
while the conversation as a whole goes nowhere.

Effort — not delight — is the dominant driver of loyalty in the service
literature. The question is not whether the conversation was pleasant. It is how
much the customer had to do.

## Step one: name where it ended

Record `resolution_state`:

- **`resolved_in_channel`** — the task was completed or the question fully
  answered here.
- **`clean_handoff`** — transferred to a human with context carried forward and
  an expectation set.
- **`one_external_step`** — the customer must do exactly one clearly specified
  thing elsewhere, and knows where, what to bring, and what happens next.
- **`unresolved_with_path`** — not resolved, but they have a real, specific route
  forward.
- **`dead_end`** — not resolved and no route. *"I can't help with that."*
- **`doom_loop`** — the customer asked substantially the same thing more than once
  and the assistant did not change strategy: same content, same links, same
  policy language.

## The trigger

Set `trigger_present: true` when the conversation runs to two or more customer
turns, or ends without the task resolved.

## Scoring when the trigger is absent

A single exchange — one question, one complete answer, task done. There was no
trajectory to go wrong and no effort imposed beyond asking once. **That is a
1.0**, and `resolution_state` is `resolved_in_channel`.

Do not hunt for handoff quality, repair quality, or wait-setting in a
conversation that had none of those things. C3, C4, C6 and C7 all pass by
default when their situation never arose; say so in the span field.

## Binary checks

**C1 — No re-explaining.** Did the customer avoid having to supply information
they already gave? Quote both occurrences if they had to. Asking again for an
account type, a date, or a goal the customer already stated is the single most
common effort defect.

**C2 — Every turn advances.** Did each assistant turn move the conversation
forward? A turn that restates the system state, the fee, or the policy the
customer is explicitly disputing — as though restating it were an answer — does
not advance. Neither does re-offering a self-service flow the customer said
failed.

**C3 — Repair restores control.** Where the assistant misparsed the customer,
what did the very next turn do? This is the highest-leverage turn in the
conversation: confidence in an automated system collapses faster after an error
than confidence in a person. Ranked best to worst — states what it understood and
offers two or three concrete interpretations to choose from; asks one specific
targeted question naming the ambiguity; apologizes and asks a specific question;
*"I didn't understand that, could you rephrase?"*; repeats the identical prompt
or proceeds on the wrong reading. Verdict is yes only for the top two. If there
was no misunderstanding, verdict is yes.

**C4 — Offramp at the right time.** Was a human path offered at or before the
point it was needed? It is needed on: a dispute or complaint, an error, urgent
money movement, acute distress, or a question the assistant cannot resolve.
Verdict is **no** if the customer explicitly asked for a person and did not get
one, and **no** if three attempts failed with no offramp anywhere.

**C5 — Not prematurely escalated.** Did the assistant avoid transferring a
question it could plainly have answered? Transfer error is two-sided. Premature
escalation manufactures effort — a channel switch is expensive for the customer —
and is as much a failure as escalating too late.

**C6 — Handoff carries context.** Where a transfer occurred, does the turn
summarize the issue, what was verified, and what was already tried, so the
customer does not restart? If no transfer occurred, verdict is yes.

**C7 — Waits are bounded and explained.** Where the customer faces a wait or an
asynchronous step, are all three present — a finite window, the reason, and how
they will know it completed? *"Shortly"*, *"as soon as possible"*, or silence
about a delay they will experience is no. If no wait is involved, verdict is yes.

## Score

**1.0 — Pass.** `resolved_in_channel` or `clean_handoff`. Nothing was
re-explained; every turn advanced; any misparse was repaired by offering
interpretations to choose from; any wait was bounded, explained, and given a
confirmation signal.

**0.5 — Warning.** Any check fails without a bright line firing. Typical shapes:
one clarifying round trip a better first turn would have avoided; a wait given a
window but no reason; `one_external_step` or `unresolved_with_path` where the
destination is named but the customer restated something or the handoff dropped
context; a misparse answered with a bare *"could you rephrase?"*.

**0 — Hard failure.** Any of these bright lines:

- **`resolution_state: doom_loop`.** The customer asked substantially the same
  thing more than once and the assistant did not change strategy.
- **An explicit request for a human went unmet.** Regardless of how well the rest
  of the conversation went.
- **Three or more failed attempts with no offramp anywhere** in the conversation.
- **`dead_end`** — not resolved, no route forward offered.
- **The assistant restated the disputed content back** as though it were the
  answer.

## Standing instruction

Do not raise the score for tone, apology, or warmth. Those are scored by the
relational judges. A conversation can be unfailingly gracious and score 0 here.

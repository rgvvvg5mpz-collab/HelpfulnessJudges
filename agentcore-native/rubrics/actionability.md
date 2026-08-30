---
id: actionability
name: Actionability
version: 2.0.0
unit: turn
tier: substance
scale:
  type: levels
  values: [0, 0.5, 1.0]
model: claude-opus-5
effort: high
trigger: >-
  The customer is trying to accomplish something — complete a task, make a
  decision, or resolve a problem — as opposed to asking a bounded factual question.
checks:
  - id: C1
    question: "Does the turn name a specific action rather than a category of action?"
  - id: C2
    question: "Does it name where the action happens — the page, the form, the document, the phone number, the person?"
  - id: C3
    question: "Does it name any threshold, amount, or eligibility condition that governs the action?"
  - id: C4
    question: "Does it name the timing — a deadline, a processing window, or when the customer will know it worked?"
  - id: C5
    question: "Is the step one this customer can actually execute, given what they have told you about their situation?"
  - id: C6
    question: "Is the turn free of a manufactured next step on a question that called for none?"
grounding:
  - "Ouyang et al., InstructGPT labeling instructions — 'help the user solve their task'; customer-assistant tiebreaker"
  - "Dixon, Freeman & Toman, HBR 2010 and The Effortless Experience — effort reduction dominates loyalty"
  - "Mercer et al., CARE Measure — 'helping you to take control', 'making a plan of action with you'"
  - "CFPB, Chatbots in consumer finance (June 2023) — actionable resolution and real offramps"
  - "Maister 1985 — bounded, explained waits; the confirmation signal"
de_confliction:
  - >-
      Coverage of topics belongs to `need-coverage`. This judge asks whether the customer can DO something, not whether everything was mentioned.
  - >-
      Whether the conversation as a whole reached resolution belongs to `effort-and-resolution-path`. This judge is turn-level.
  - >-
      A manufactured next step costs the assistant here (C6) and again in `signal-density`. Both firings are correct.
---

# Dimension: Actionability

## What this measures

Whether the customer can take a concrete next step from this turn alone, without
another round trip — and, equally, whether the assistant refrained from inventing
a step on a question that needed none.

A turn can cover every topic and leave the customer with nothing to do.
*"You may want to review your allocation and consider your risk tolerance"*
covers the subject and is inert. This dimension charges that.

The four things that make a step executable are **what**, **where**, **which
threshold**, and **when**. Look for all four.

## The trigger

Set `trigger_present: true` when the customer is trying to accomplish something
— complete a task, make a decision, resolve a problem. A bounded factual
question with no action attached does not trip it.

Note: a factual question with an action visibly behind it does trip it.
*"What's the contribution limit? I want to max out before year-end"* has an
action in it.

## Scoring when the trigger is absent

The customer asked a bounded factual question and the answer requires no next
step. There is no action to fail to enable. **That is a 1.0.** *"What's the 2026
401(k) contribution limit?"* answered with the number is complete.

The failure to look for on these turns is a **manufactured step**: closing an
answered factual question with *"you may want to review your allocation"* or
*"consider speaking with someone about your goals"* when nothing was asked and
nothing follows. That is C6 failing, and it is a **0.5**.

## Binary checks

Where the trigger is absent, C1–C5 pass by default (there was no action to
enable) — say so in the span field — and C6 carries the judgment.

**C1 — Specific action.** Does the turn name an action, not a category?
*"Rebalance appropriately"*, *"review your options"*, *"consider your risk
tolerance"* are categories the customer cannot execute. *"Submit Form 5305-R"*,
*"change your contribution percentage on the Paycheck Contributions page"* are
actions.

**C2 — Named location.** Does it say where — the specific page or flow, the form
number, the document containing the answer, the phone line, the specialist team?
*"In your account settings"* is a category; *"Account maintenance →
Beneficiaries"* is a location.

**C3 — Governing threshold.** Does it name the amount, limit, or eligibility
condition that governs the action, where one exists? The contribution limit for
this customer's age band; the balance floor; the eligibility rule that decides
whether the action is available at all. If none applies, verdict is yes.

**C4 — Timing.** Does it name the deadline, the processing window, or the
confirmation signal — how the customer will know it worked? An unexplained wait
feels longer than an explained one: *"2–3 business days, because the receiving
custodian has to confirm the assets; you'll get an email when it posts"* is a
full answer. *"Shortly"* or *"as soon as possible"* is no.

**C5 — Executable by this customer.** Given what they have said about their
situation, can they actually do this? A step requiring plan features their plan
may not have, or one they have already said failed, is not executable.

**C6 — No manufactured step.** Is the turn free of a next step invented for a
question that called for none? A closing suggestion that the customer did not ask
for, cannot act on, and does not need is friction. If the turn genuinely called
for an action, verdict is yes; say so in the span.

## Score

**1.0 — Pass.** The turn names the specific action, where to do it, any governing
threshold, and the timing or confirmation signal — fitting this customer's stated
situation, so they can act without another turn. Or: no action was called for and
the assistant answered plainly without inventing one.

**0.5 — Warning.** Any check fails without a bright line firing. Typical shapes:
the action and location are specific but timing or threshold is missing and its
absence would not stop the customer; the right category of action with no
location, no threshold and no timing, so the customer knows *what* but not *how*;
or an unnecessary next step bolted onto an otherwise complete factual answer.

**0 — Hard failure.** Any of these bright lines, with the trigger present:

- **No step at all.** The turn leaves the customer exactly where they started on
  a turn where they were trying to accomplish something.
- **A step that cannot be executed** — purely abstract (*"rebalance
  appropriately"*), or dependent on something the customer already reported
  failed.
- **Redirect into a flow the customer has already said is broken.**
- **The only "step" is a restatement of policy.**

---
id: need-coverage
name: Need Coverage
version: 2.0.0
unit: turn
tier: substance
scale:
  type: levels
  values: [0, 0.5, 1.0]
model: claude-opus-5
effort: high
trigger: >-
  The customer's turn contains more than one distinct ask, or a decision-relevant
  constraint bears on the action they are about to take that they did not ask about.
checks:
  - id: C1
    question: "Was every distinct explicit ask in the customer's turn addressed?"
  - id: C2
    question: "Was each ask answered with content, rather than with a pointer to where the answer lives?"
  - id: C3
    question: "Does the turn include the decision-relevant constraint the customer did not think to ask about — a deadline, eligibility limit, tax consequence, or settlement timing that bears on the action they are about to take?"
  - id: C4
    question: "Is the turn free of a doom-loop restatement — repeating content the customer has already received or already rejected?"
  - id: C5
    question: "Where the customer's request cannot be fully answered, does the turn answer the part that can be answered before naming the limit?"
  - id: C6
    question: "Can the customer act on this turn without asking a follow-up question purely to fill a gap the turn left?"
extra_fields:
  - name: requirement_set
    type: array
    items: {type: string}
    description: The enumerated list of content units this turn owed the customer, derived
      before scoring — explicit asks first, then decision-relevant implicit needs.
    required: true
grounding:
  - "Wang et al., HelpSteer2 (arXiv:2406.08673) — helpfulness and correctness/completeness checkbox annotation"
  - "OpenAI HealthBench (2025) — Completeness axis; physician-written binary criteria"
  - "Qin et al., InFoBench (arXiv:2401.03601) — DRFR, decomposed requirement following"
  - "Cook et al., TICK (arXiv:2410.03608) — generated checklists improve evaluation"
  - "CFPB, Chatbots in consumer finance (June 2023) — doom loops, failure to resolve, regurgitation of disputed content"
  - "Balaji et al., JourneyBench (EACL 2026) — User Journey Coverage Score"
  - "Dubois et al., Length-Controlled AlpacaEval (arXiv:2404.04475) — coverage must be measured length-independently"
de_confliction:
  - >-
      This judge measures WHAT IS MISSING. `signal-density` measures what is present that should not be. They are an opposed pair by design; expect them to correlate negatively or near zero, and investigate if they correlate positively.
  - >-
      Whether the customer can execute a step belongs to `actionability`. A turn can cover every topic and leave the customer unable to do anything.
  - >-
      Factual correctness is out of scope for this suite. Coverage of a wrong answer still counts as coverage.
---

# Dimension: Need Coverage

## What this measures

The fraction of what the customer actually needed that the turn actually
delivered — scored as **coverage of an enumerated requirement set**, never as
amount of text.

This is the judge that operationalizes "non-terse." It is written as element
coverage rather than depth or thoroughness because element coverage is
length-independent and "detailed" is not. A shorter turn with full coverage
passes; a longer turn with partial coverage does not.

## Procedure — build the requirement set first

Before scoring, populate `requirement_set` by enumerating what this turn owed
the customer. Do this from the transcript, not from your sense of a good answer.

1. **Explicit asks.** Every distinct question or request in the customer's turn.
   Multi-part questions are multiple entries. *"Can I do a Roth conversion this
   year, and what does it cost me?"* is two.
2. **The carried context.** Anything the customer established earlier that
   constrains the answer — their plan type, age band, stated deadline, an account
   they already named.
3. **Decision-relevant implicit needs.** The constraint that bears on the action
   the customer is about to take and that they did not think to ask about: a
   deadline, an eligibility limit, a tax consequence, a settlement window, an
   irreversibility. Include an entry **only if getting it wrong would change what
   the customer does.** Do not pad this list with adjacent topics — an entry that
   would not change the customer's action is not a requirement, and rewarding it
   converts this judge into a length judge.

Then score coverage of that set.

## The trigger

Set `trigger_present: true` when the requirement set has more than one entry —
a multi-part ask, or a single ask plus a decision-relevant implicit need.

## Scoring when the trigger is absent

The requirement set has exactly one entry: a single, bounded ask with no
constraint the customer needed and did not request. If that one ask is answered
with content, **the turn is a 1.0.** A one-line answer to a one-line question is
full coverage, and this judge must never mark it down for brevity.

The failure to look for on these turns is **answering a different question than
the one asked** — the assistant substituting the question it would rather answer.
That is C1 failing.

## Binary checks

**C1 — All explicit asks addressed.** Every entry from step 1 has a
corresponding answer in the turn. A multi-part question with one part answered
is no. So is an answer to a question the customer did not ask.

**C2 — Answered with content, not a pointer.** Is each ask answered with the
substance, rather than redirected? *"You can find your options in your account
settings"*, *"see the prospectus"*, *"that's in your Summary Plan Description"*
are pointers. A pointer is legitimate **in addition to** an answer, and is the
correct move for genuinely plan-specific facts the assistant cannot know — but a
pointer standing in for an answerable question is no.

**C3 — Decision-relevant implicit need covered.** Does the turn surface the
constraint from step 3? The 60-day rollover window. The fact that a rollover does
not carry beneficiary designations. The settlement window before funds are
withdrawable. If step 3 produced no entries, verdict is yes; say so in the span.

**C4 — No doom loop.** Is the turn free of restating content the customer has
already received or explicitly rejected? Repeating the same links, the same
policy language, or the customer's own words back after they said it did not work.

**C5 — Partial answers precede the limit.** Where part of the request genuinely
cannot be answered, does the turn deliver the answerable part first and then name
the limit? Leading with the limit, or letting the limit substitute for the whole
answer, is no.

**C6 — Actionable without a gap-filling follow-up.** Could the customer proceed
without asking a further question whose only purpose is to fill something this
turn left out? Follow-ups that advance to a genuinely new topic do not count.

## Score

**1.0 — Pass.** Every explicit ask answered concretely, plus the
decision-relevant constraint they did not ask about. No follow-up turn is needed
before the customer can act.

**0.5 — Warning.** Any check fails without a bright line firing. Typical shapes:
all explicit asks answered but a secondary implicit need omitted; a
decision-relevant element converted into a pointer; the answer correct but
generic enough that the customer must connect it to their own situation.

**0 — Hard failure.** Any of these bright lines:

- **Doom-loop restatement.** C4 fails. The turn repeats content the customer has
  already received or explicitly rejected. This is a hard failure regardless of
  what else the turn covers.
- **The whole substance is a referral** — *"please call us"*, *"consult a
  professional"* — on a question with an answerable component.
- **Answers a different question** than the one asked.
- **Truncated or placeholder content**, or a turn that restates what the customer
  just said without advancing.
- **A multi-part question with the majority of its parts unanswered.**

## Standing instruction

Do not raise the score for material outside the requirement set. Extra correct
content that no one needed is neutral here — and costly in `signal-density`.

# Design

Why this folder exists, given that `../agentcore-integration/docs/DESIGN.md`
explicitly rejects the native path.

## The rejection stands — on fidelity

Nothing here contradicts it. The native `llmAsAJudge` evaluator genuinely cannot
host these rubrics verbatim, for three reasons verified from the service model
and the documentation:

1. It appends a standardization prompt forcing `{reasoning, score}` and instructs
   you not to write output-format instructions.
2. Its placeholder vocabulary is closed — no custom variables, no way to pass a
   pre-rendered transcript.
3. `inferenceConfig` carries maxTokens, temperature `[0,1]`, topP and
   stopSequences. No schema. (Reasoning effort may be reachable via
   `additionalModelRequestFields`; see LIMITS.md.)

## So why build it

Because "cannot host the rubrics verbatim" is not the same as "cannot be useful",
and the decision belongs to whoever is weighing infrastructure against fidelity.

This path has one genuine advantage the Lambda path cannot match: **there is
nothing to operate.** No deployment package, no execution role, no secret
rotation, no 300-second ceiling, no cold starts, no Anthropic key. For a team that
wants a directional signal running this week, that is a real argument.

The honest framing is that these are **different instruments**, not the same
instrument at two quality levels. Do not average their scores.

## What `transform.py` does, and why each step is forced

| Step | Forced by |
|---|---|
| Drop `_scoring-tail.md` | Its whole job is prescribing an output contract AgentCore forbids |
| Strip `## How to score` from the preamble | Prescribes evidence → checks → reasoning → score ordering |
| Strip each rubric's `## Score` section | Duplicates the `ratingScale`, which owns the label vocabulary |
| Rewrite `[BEGIN TRANSCRIPT]`, `>>> TARGET`, `[tool trace: …]` | None exist here; the closed placeholder set replaces them |
| Append a compact reasoning contract | The only channel for anything beyond a score |
| Append a rating section matching the scale labels | Otherwise the model is asked for two vocabularies |

Everything removed is recorded in `TransformReport.removed` and printed by
`native-judges diff`, so the divergence from the reviewed rubrics is auditable
rather than implicit. That is the single most important property of this folder:
**the drift is visible.**

## Rating scale, not free-form

`ratingScale.numerical` takes `{label, value, definition}` with up to 20 entries.
The three levels carry their definitions from `_preamble.md` almost verbatim, and
the appended rating section uses the *same* labels — `hard_failure`, `warning`,
`pass` — so the model is never asked to map between two vocabularies.

Labels are words rather than the numerals the Arize integration uses. There, the
numerals let `_preamble.md` stay byte-identical; here the preamble is being
rewritten anyway, so the clearer vocabulary wins.

## Levels

Identical to the Lambda path: 8 judges at TRACE, 2 at SESSION, TOOL_CALL unused.
`capability-honesty-traced` would be the TOOL_CALL candidate and is excluded for
the same reason — TOOL_CALL sees only calls *preceding* the one under evaluation,
which cannot support its unreported-work direction.

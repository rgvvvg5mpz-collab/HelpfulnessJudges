# Design

Why this shape, and what was rejected on what evidence. Every API claim was
verified against botocore `1.43.83` service models, not documentation.

## The decision everything follows from

**Each judge is a code-based Evaluator backed by a Lambda, not a native
`llmAsAJudge` evaluator.**

AgentCore has a genuine LLM-as-judge feature, and it cannot host these rubrics:

- It **appends its own standardisation prompt** forcing `{reasoning, score}`, and
  explicitly instructs you not to write output-format instructions.
- Prompt inputs are a **closed placeholder set** — `{context}`,
  `{assistant_turn}`, `{tool_turn}`, `{available_tools}`. There is no
  custom-variable mechanism and no column-mapping equivalent, so a pre-rendered
  transcript has nowhere to go. The rubrics name `--- turn N | CUSTOMER ---`,
  ` >>> TARGET` and `[tool trace: ...]` **by name**; a service-formatted
  `{context}` in an undocumented shape drifts every rubric's meaning.
- `inferenceConfig` exposes only maxTokens/temperature/topP: **no thinking
  control, no cache control, no output schema** — which reproduces the Arize
  fatal limitation exactly.

The Lambda path solves all of it, and inverts the verdict the Arize integration
reached on code evaluators.

## The asymmetry with Arize

Arize rejected custom code evaluators because `CustomCodeConfigRequest` has no
`llm_config`, no env var and no secret store — an `ANTHROPIC_API_KEY` would sit
in plaintext, permanently versioned in the Eval Hub and readable by any space
member.

**A Lambda has an execution role.** The key lives in Secrets Manager and never
touches the evaluator config. That one difference retires four of the five limits
in the Arize integration, including the fatal one.

## Levels

`CreateEvaluator.level` is `TOOL_CALL | TRACE | SESSION`, and it maps almost
exactly onto the suite's own units.

| Level | Judges | Why |
|---|---|---|
| TRACE | 8 turn judges | One trace is one request/response turn |
| SESSION | `conduct-adaptation`, `effort-and-resolution-path` | Their defects only exist across turns |
| TOOL_CALL | **none** | See below |

**`capability-honesty-traced` runs at TRACE, not TOOL_CALL.** A TOOL_CALL
evaluator fires once per call and sees only the calls *preceding* the one under
evaluation. That cannot support the judge's `unreported_work` direction — the
whole point is spotting work the turn never reported, which requires seeing the
turn and all of its calls together.

## Zero rubric changes

Because the Lambda calls Anthropic with the same `output_config.format`
json_schema the parent harness uses, `_scoring-tail.md` holds verbatim. All 12
files are vendored unchanged, hashes recorded, and `check-drift` fails if the
originals move.

The Arize sibling had to rewrite its tail, which meant its anchor-set validation
had to account for a prompt diff. This one does not.

## Span reconstruction

`spans.py` groups spans **by trace** before rebuilding, because one trace is one
turn. Tool spans attach to the assistant turn of *their own* trace.

This is not incidental. Attaching tools to "the most recent assistant turn"
reconciles a turn's claims against a different turn's tool calls — precisely the
failure `capability-honesty-traced` exists to detect. A test pins it.

Tool status is rendered explicitly: `-> ERROR <message>` for a failed span,
`-> EMPTY` for an empty result. Check C5 asks whether the turn disclosed a failed
lookup rather than answering around it, and it fails silently if the trace only
ever shows successes.

## Naming

`evaluatorName` matches `[a-zA-Z][a-zA-Z0-9_]{0,47}` — no hyphens. Every judge id
is hyphenated, so `naming.to_evaluator_name` normalises to snake_case.

Unlike Arize, AgentCore *rejects* a bad name with a `ValidationException` rather
than silently dropping the column, so this is a loud failure rather than a quiet
one.

## Strict request validation

`provision.validate_request` walks the botocore shape enforcing enums, patterns
and bounds. `botocore.validate.validate_parameters` checks structure and types
only — a `level` of `"TURN"` passes it.

One subtlety: **AWS shape patterns are implicitly full matches.** Using
`re.match` lets `has-hyphens` pass `[a-zA-Z][a-zA-Z0-9_]{0,47}` on the `has`
prefix alone. The validator uses `re.fullmatch`; a test pins that too.

# What the native path costs

Graded against `../agentcore-integration` (the Lambda path), because that is the
real alternative. Shapes verified against botocore `1.43.83`.

## FATAL for comparability — the prompts are not the validated prompts

`transform.py` removes the `## How to score` preamble section and each rubric's
`## Score` section, drops `_scoring-tail.md` entirely, and rewrites 6–13
transcript-marker references per judge. The rubric text that runs is therefore
**not** the text the anchor set was built against.

Every downstream number inherits this. Agreement figures, drift baselines and
ship-gate tolerances measured on the original rubrics do not transfer.

**Mitigation.** Re-baseline the anchor set on this path, and never mix scores from
the two paths in one series. Or accept these as directional only. There is no
third option — this is inherent to a host that owns the output contract.

## MAJOR — k=1, and no way to change it

No `repetitions` / `trials` / `n` parameter exists in the evaluator schema, the
online config, or the batch API. `samplingPercentage` selects *which* sessions get
scored, never how many times.

You lose the median, `unanimous`, `hard_failure_votes` and the split-across-the-0
boundary routing the suite uses to decide whether to trust a verdict — on a
dimension where published judges flip ~13.6% of repeated verdicts.

**Mitigation.** None on this path. Keep k=5/k=10 offline on the anchor set via
the parent harness, and treat production scores as trend rather than per-item
truth.

## MAJOR — evidence spans do not survive

The standardization prompt describes `reasoning` as "no more than 250 words". A
check vector plus evidence spans plus prose does not fit, so `transform.py`
encodes verdict **flags** only:

```
CHK C1=y C2=n … | TRIG y | BL -
```

Recoverable with a Logs Insights regex. **The span that decided each check is
not.** A disputed verdict cannot be adjudicated from the record — the reviewer
sees which check failed but not on what wording.

## MAJOR — nothing enforces the verdict shape

No `json_schema`, no forced tool with a typed argument. The flag line is a prompt
convention the model may simply not follow, and there is no validator in the path.
The Lambda path enforces the shape at generation time.

**Mitigation.** Parse the flag line on export and alarm on the parse-failure rate.
A judge that stops emitting it is degrading before its scores move.

## UNVERIFIED — reasoning effort may be reachable

`bedrockEvaluatorModelConfig.additionalModelRequestFields` is a free-form
structure, and on Bedrock that is the standard escape hatch for provider-specific
parameters — which is where Anthropic `thinking` would go. Separately,
`responsesEvaluatorModelConfig.reasoning.effort` exists explicitly.

**This corrects an earlier claim.** `../agentcore-integration/docs/DESIGN.md`
said the native path has "no thinking control". That was overstated: the schema
has a plausible route and an explicit one on the other model shape. Neither is
documented for Anthropic on Bedrock.

`--thinking` populates `additionalModelRequestFields`. **Test it before relying
on it** — a silently-ignored field looks exactly like a working one.

## MINOR — `{context}` has an undocumented shape

The service builds the conversation. The rubrics were written against a renderer
whose markers they name; here they are re-pointed at `{context}` without anyone
knowing precisely what it renders as.

**Mitigation.** Inspect one rendered prompt via the console's Test Evaluator
before provisioning all ten. If `{context}` turns out to omit tool calls, the
honesty judges lose their subject matter entirely.

## MINOR — the model is a Bedrock model

The suite was validated on `claude-opus-5` via the Anthropic API. Bedrock model
ids differ, and Claude Opus 5 does not support structured outputs on Bedrock —
irrelevant here since nothing requests a schema, but it means the judge model is
not the validated one. Another contributor to the re-baseline.

## Unchanged from the Lambda path

10 evaluators per online config (non-adjustable); evaluator locking while a config
is ENABLED; the account-wide Transaction Search prerequisite; and the
`evaluatorName` pattern with no hyphens.

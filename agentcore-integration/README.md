# AgentCore integration

Run the helpfulness judge suite on **Amazon Bedrock AgentCore**, scoring
conversations from **CloudWatch** traces.

Standalone: this folder does not import from the parent repository. Everything
needed is here — the rubrics, what they measure, the deployment code, the
CloudWatch setup, and the architecture.

> **Status: designed and tested offline, never run against an AWS account.**
> API shapes were verified by reading botocore `1.43.83` service models, not the
> documentation. **134 offline checks pass** with no AWS credentials.

**[architecture.html](architecture.html)** — the data flow, one page.
**[docs/COMPARISON.pdf](docs/COMPARISON.pdf)** — AgentCore vs Arize, with citations.

---

## What the judges are

Ten independent prompt-based LLM judges for a retail-investment chatbot, scoring
one dimension each on **0 / 0.5 / 1.0** — hard failure, warning, pass.

| Judge | Unit | AgentCore level | What it catches |
|---|---|---|---|
| `empathic-attunement` | turn | TRACE | Portable warmth that would fit any customer |
| `emotional-accuracy` | turn | TRACE | Warm and *wrong*, or comfort that amplifies distress |
| `conduct-adaptation` | conversation | SESSION | Kind words around unchanged behaviour |
| `need-coverage` | turn | TRACE | What the customer needed and did not get |
| `signal-density` | turn | TRACE | Padding — the counterweight to coverage |
| `actionability` | turn | TRACE | Complete answers they still cannot act on |
| `plain-language-clarity` | turn | TRACE | Jargon, and clarity bought by omission |
| `calibrated-hedging` | turn | TRACE | The cop-out referral instead of an answer |
| `effort-and-resolution-path` | conversation | SESSION | Doom loops, blocked offramps, lost handoff context |
| `capability-honesty` | turn | TRACE | Narrated work that never happened |
| `capability-honesty-traced` | turn | TRACE | **Variant** — same construct, reconciled against the tool trace |

**The mapping rule**, identical in every rubric: a **bright line** named in the
rubric forces **0** and overrides everything; otherwise all checks passing is
**1.0**; anything else is **0.5**. Each judge has 6–7 binary checks, and the
per-item resolution lives in that check vector rather than in the score.

Judges are ungated and mutually independent — none reads another's output, none
is skipped, and every rubric is two-sided: a dimension can be failed by
*manufacturing* it where it did not belong, not only by omitting it.

`capability-honesty-traced` is a **variant** of `capability-honesty`, not an
addition. Run one or the other — they measure the same construct and running both
double-counts it. `spec.deployable()` enforces that, because AgentCore will not.

## The shape

Each judge is a native AgentCore **code-based Evaluator** backed by one Lambda.
AgentCore owns selection, sampling, cadence and write-back; the Lambda owns the
judging.

```
chatbot → OTel spans → CloudWatch → OnlineEvaluationConfig (samples sessions)
                                          ↓
                                    Evaluator (×10)
                                          ↓
                                    Judge Lambda ──→ Anthropic API (k=5)
                                          ↓
                          {label, value, explanation} → CloudWatch events + metrics
```

**Why the Lambda and not AgentCore's native LLM judge.** The native
`llmAsAJudge` appends its own standardisation prompt forcing `{reasoning, score}`,
tells you not to write output-format instructions, and restricts prompts to a
closed placeholder set with no way to pass a pre-rendered transcript. Its
`inferenceConfig` has no thinking control, no cache control and no output schema.

Because the Lambda calls Anthropic itself, **everything the suite depends on
survives**: `json_schema` output enforced at generation time, `effort: high`,
`cache_control` on the shared preamble, and k=5 with a median and spread.

## Zero rubric changes

All 12 rubric files — including `_scoring-tail.md` — are vendored **unchanged**
from the parent repo. The Lambda calls Anthropic with the same schema, so the
original output contract holds verbatim and the verdict is schema-validated
before anything is serialised. The collapse to three fields happens at the
AgentCore return boundary, *after* validation.

(The Arize sibling had to rewrite the tail. This one does not.)

## Quick start — nothing touches AWS

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tests.test_suite              # 134 checks, no credentials
bin/agentcore-judges list
bin/agentcore-judges check-drift                  # vendored rubrics vs ../judges
bin/agentcore-judges render --judge signal-density
bin/agentcore-judges plan --lambda-arn arn:aws:lambda:us-east-1:123456789012:function:judges
```

`validate` additionally checks every request against botocore's service model —
enums, patterns and numeric bounds, which `botocore.validate` itself skips:

```bash
bin/agentcore-judges validate --lambda-arn ARN
```

## Deploying

```bash
bin/agentcore-judges provision --lambda-arn ARN --apply
bin/agentcore-judges online-config --tier critical \
    --log-group /aws/bedrock-agentcore/runtimes/chat --service chatbot \
    --role arn:aws:iam::123456789012:role/EvalExec --apply
```

Two tiers: `critical` at 100% sampling, `directional` at 10% — the relational
judges the validation protocol already restricts to directional use.

**Read [docs/CLOUDWATCH.md](docs/CLOUDWATCH.md) first.** Nothing works until
Transaction Search is enabled and the chatbot emits conforming spans, and that is
plausibly the larger half of the integration.

## Layout

```
architecture.html            the data flow diagram
rubrics/                     all 12 vendored UNCHANGED, with provenance hashes
agentcore_judges/
  spec.py                    standalone loader + Anthropic output schema + drift check
  spans.py                   CloudWatch OTel spans -> transcript (trace-grouped)
  judge.py                   the Anthropic call: k=5, json_schema, effort, caching
  handler.py                 the Lambda; one handler, dispatches on evaluator name
  boundary.py                Verdict -> AgentCore's {label, value, explanation}
  score.py                   offline scoring against data/testsets/
  verify.py                  payload validation + label recomputation
  provision.py               CreateEvaluator / OnlineEvaluationConfig + strict validator
  naming.py                  judge id -> evaluatorName (no hyphens allowed)
  cli.py                     agentcore-judges
infra/template.yaml          CloudFormation, if you prefer IaC to the CLI
docs/
  COMPARISON.pdf / .html     AgentCore vs Arize, with citations
  CLOUDWATCH.md              trace setup — read before anything else
  DESIGN.md                  why this shape; rejected alternatives
  LIMITS.md                  what this costs, graded
  RUNBOOK.md                 ordered deployment
tests/test_suite.py          134 offline checks
```

## Known limits

Full list in [docs/LIMITS.md](docs/LIMITS.md). The ones that shape decisions:

1. **300-second Lambda ceiling, 6 MB `sessionSpans` cap.** k=5 concurrent Opus
   calls at `effort: high` must fit. **Measure this first** — it is the top
   sizing risk and the only one that can force a design change.
2. **Account-wide Transaction Search prerequisite.** A real workstream with a
   cost implication, and a change you should get agreed before running.
3. **Structured verdict still collapses to three fields** — mitigated by
   payload-in-explanation, and unlike Arize the payload was schema-validated
   before serialisation.
4. **10 evaluators per online config**, non-adjustable. The suite fits with zero
   headroom, and only because variant exclusion drops one side.
5. **An evaluator is locked while its config is ENABLED.** Pause → update →
   resume, or a rubric edit fails mid-deploy.

## Confirm against your account

1. Does k=5 at `effort: high` complete inside 300 s on a ~19k-token prompt?
2. Does a ~1.5 KB JSON `explanation` survive intact and render readably?
3. Is the account's Transaction Search change approved, and its cost understood?
4. Do the chatbot's spans carry `session.id`, and do TOOL spans carry status?

---

## This folder is one of three

The same ten judges deploy three ways. This is **AgentCore code-based Lambda**. The siblings are
[`../arize-integration`](../arize-integration) and [`../agentcore-native`](../agentcore-native); the comparison and the choice between
them is in [`../README.md`](../README.md).

Each folder is standalone and duplicates what it needs — rubrics, test data,
docs, diagrams. That is deliberate: you should be able to hand any one of them to
a team without the others.

**In this folder:** [architecture.html](architecture.html) ·
[judges.html](judges.html) · [docs/TESTING.md](docs/TESTING.md) ·
[docs/DESIGN.md](docs/DESIGN.md) · [docs/LIMITS.md](docs/LIMITS.md) ·
[data/](data/)

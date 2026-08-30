# AgentCore native

The helpfulness judge suite as **native AgentCore `llmAsAJudge` evaluators**.
AgentCore calls the model. No Lambda, no SDK, no API key, no code you run.

Standalone. **[architecture.html](architecture.html)** is the one-page picture.

> **Status: designed and tested offline, never run against an AWS account.**
> Shapes verified against botocore `1.43.83` service models. **192 offline checks
> pass** with no credentials.

---

## Choose between three paths before reading further

| Path | Who runs the judge | Rubrics | Use when |
|---|---|---|---|
| **`../agentcore-integration`** | your Lambda | unchanged | fidelity matters; the numbers must match the anchor set |
| **this folder** | AgentCore | **rewritten** | you want judges running this week with zero infrastructure |
| **`../arize-integration`** | Arize | one file rewritten | Arize is already the system of record |

This folder is the **simplicity-first** option, and it is honest about the price:
the prompts that run are not the prompts that were validated.

## What it gives up

AgentCore appends its own standardization prompt forcing `{reasoning, score}`,
and states plainly: *"Do not include output formatting instructions in your
original evaluator instruction."* Its placeholder vocabulary is a closed set —
`{context}`, `{assistant_turn}`, `{tool_turn}`, `{available_tools}` — with no
custom-variable mechanism.

Three consequences, none avoidable:

1. **The rubrics must be rewritten.** `transform.py` strips the output contract
   and re-points every transcript-marker reference at a placeholder. `_scoring-tail.md`
   is not even vendored — its entire job is prescribing an output shape AgentCore
   forbids you from writing.
2. **The structured verdict is gone.** Check verdicts compress into a flag line
   inside `reasoning`, against a ~250-word budget. **Evidence spans do not fit**,
   so they are lost.
3. **k=5 is gone.** There is no repetition parameter anywhere in the service. Every
   score is a single sample, which the published flip-rate literature says is close
   to noise on the harder dimensions.

## What it gives back

- **No operational surface at all.** No Lambda, no deployment package, no secret,
  no 300-second ceiling, no 6 MB input cap, no cold starts.
- **AgentCore assembles the conversation.** `{context}` is filled by the service,
  so you are not responsible for transcript rendering.
- **Declarative from end to end** — CloudFormation, Terraform and CDK resource
  types, same as the Lambda path.

## Quick start — nothing touches AWS

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tests.test_suite      # 192 checks, no credentials
bin/native-judges list
bin/native-judges diff                    # exactly what the transform removes, per judge
bin/native-judges show --judge signal-density --full
bin/native-judges validate                # strict: enums, patterns, bounds
bin/native-judges score --dry-run         # score the transformed prompts; nothing sent
```

`score` reports three outcomes, not two. An answer that broke the output contract
is `unparseable` and stays in `n`, in `fp0`/`fn0` and in the band columns — that
failure rate is the measurement. A call that never got an answer at all (a 429, a
401, a dropped connection) is `transport`, excluded from every column and printed
on its own line, because reporting an expired key as "this judge missed a hard
failure" would be worse than reporting nothing. The default draw is the 10
deployable judges; `--judge capability-honesty-traced` scores the variant that
mutual exclusion keeps out of the default set.

`diff` is the command to run first. It prints, per judge, every section removed
and how many marker references were rewritten — so the divergence from the
reviewed rubrics is auditable rather than implicit.

## Deploying

```bash
bin/native-judges provision --apply
bin/native-judges online-config --tier critical \
    --log-group /aws/bedrock-agentcore/runtimes/chat --service chatbot \
    --role arn:aws:iam::123456789012:role/EvalExec --apply
```

CloudWatch setup is identical to the Lambda path and is not duplicated here —
see **[../agentcore-integration/docs/CLOUDWATCH.md](../agentcore-integration/docs/CLOUDWATCH.md)**.
Transaction Search, `session.id` on every span, and the two IAM roles are all
still required.

## The reasoning contract

The only channel for anything beyond a score. `transform.py` appends this to
every rubric:

```
CHK C1=y C2=n C3=y C4=y C5=y C6=n | TRIG y | BL -
```

One token per check in rubric order, then whether the trigger fired, then the
bright line that fired or `-`. Then prose naming the deciding check ids.

That is recoverable with a regex in Logs Insights, so per-check questions stay
answerable. What is **not** recoverable: the span that decided each check, the
evidence list, confidence, and every per-judge extra field.

## Known limits

Full list in [docs/LIMITS.md](docs/LIMITS.md). In severity order:

1. **The anchor-set validation does not transfer.** Different prompts, so the
   numbers are not comparable to the validated baseline without a full
   re-baseline. This is the one that decides whether this path is acceptable.
2. **k=1 only.** No repetition, no median, no spread, no human-review routing.
3. **Evidence spans lost** to the reasoning word budget.
4. **Reasoning effort is unverified** — `additionalModelRequestFields` is a
   free-form structure and is where Anthropic thinking config plausibly goes,
   but nothing documents it. `--thinking` populates it; test before relying on it.
5. **Model is a Bedrock model**, not the pinned `claude-opus-5` the suite was
   validated with. Another reason the numbers move.

## Confirm before committing

1. Does `additionalModelRequestFields` accept Anthropic thinking config?
2. What exactly does `{context}` render as? The shape is undocumented, and every
   rubric's meaning depends on it.
3. Does the `CHK …` flag line survive the standardization prompt intact, or does
   the model prose over it?
4. Run the anchor set both ways — this path and `../agentcore-integration` — and
   measure the gap. That number is the price of the simplicity.

---

## This folder is one of three

The same ten judges deploy three ways. This is **AgentCore native llmAsAJudge**. The siblings are
[`../arize-integration`](../arize-integration) and [`../agentcore-integration`](../agentcore-integration); the comparison and the choice between
them is in [`../README.md`](../README.md).

Each folder is standalone and duplicates what it needs — rubrics, test data,
docs, diagrams. That is deliberate: you should be able to hand any one of them to
a team without the others.

**In this folder:** [architecture.html](architecture.html) ·
[judges.html](judges.html) · [docs/TESTING.md](docs/TESTING.md) ·
[docs/DESIGN.md](docs/DESIGN.md) · [docs/LIMITS.md](docs/LIMITS.md) ·
[data/](data/)

# What the chatbot must emit

The native design trades Arize assembling the conversation for **you** assembling
it. That makes the tracing layer part of the eval contract. This is the whole of
that contract.

## Attributes, per assistant turn

Set these on the **LLM span** for each assistant turn.

| Attribute | Type | On which spans | Purpose |
|---|---|---|---|
| `judge.transcript` | string | every assistant turn | The entire conversation up to and including this turn, pre-rendered, with **this** turn marked `>>> TARGET`. The 8 turn judges read it. |
| `judge.conversation_transcript` | string | final assistant turn only | Same conversation rendered with **no** turn marked. The 2 conversation judges read it. |
| `judge.target_turn` | int | every assistant turn | Index of the scored turn. |
| `judge.conversation_id` | string | every assistant turn | For correlation. |
| `judge.renderer_version` | string | every assistant turn | **Not optional.** See below. |
| `judge.is_final_turn` | bool | every assistant turn | What the conversation-judge task filters on. |
| `session.id` | string | every span | Sessions do not exist in Arize without it. |

`arize_judges.instrument.span_attributes()` produces all of these.

## The rendering is the contract

The rubrics and `_preamble.md` reference the transcript's markers **by name** —
`[BEGIN TRANSCRIPT]`, `--- turn N | CUSTOMER ---`, ` >>> TARGET`, and the
two-space-indented `  [tool trace: ...]` line. If the rendering drifts, every
rubric's meaning drifts with it and **no test will tell you**.

`arize_judges.instrument.render_transcript` is byte-identical to the parent
repo's `harness/transcript.py`; the test suite asserts it. The cleanest
implementation is to import one of them into your tracing layer so there is
literally one renderer.

`judge.renderer_version` exists so that when scores move you can tell a rendering
change from a chatbot change. Bump it whenever the renderer changes, and treat
that bump the way you treat a rubric edit.

## Tool traces

`capability-honesty-traced` reconciles in **both directions** — what the turn
claimed, and what the trace shows it actually did, including work the turn
silently dropped. The trace must support both.

Each entry needs: **call name, an args summary, the returned value or the error,
and an explicit status.**

```
get_balance(account=Roth IRA) -> $84,210
get_contribution_ytd(year=2026) -> $4,500
get_pending(2026) -> 1 pending $500 contribution
get_plan_rules(plan=X) -> ERROR timeout
```

A call that **errored, timed out, returned empty, or returned partial data must
be visibly marked as such.** Check C5 asks whether the turn disclosed a failed
lookup rather than answering around it — and it fails silently if the trace only
ever shows successes.

Where no calls were made the renderer emits `[tool trace: none recorded]`. That
sentinel is load-bearing: the rubric branches on that exact string and drops to
`confidence: low`, because "the trace was not captured" is a different state from
"no calls happened". Never omit the line.

**Also wrap actual tool execution in an OpenInference TOOL span.** Auto-
instrumentors trace the model's *request* for a tool call, not your function
running. The judge reads the rendered ledger rather than the spans, but the two
must come from the same source or they will disagree at the worst moment.

## Filtering

Task filters only work reliably on indexed attributes. Filter on
`span_kind = 'LLM'` plus the project, **not** on `attributes.judge.*` — custom
metadata keys may not be indexed and will silently match nothing, which looks
exactly like "the judges found no problems".

## One thing to confirm before you commit

**Is there an ingest-side cap on a single span attribute value?**

A full pre-rendered transcript can plausibly run 20–100 KB. Nothing in Arize's
tracing docs states a cap, and the 100,000-char limit that *is* documented
applies to trace/session variable **assembly**, not span attribute ingest.

If a cap exists, the transcript must be windowed — all customer turns, the last
N assistant turns verbatim, earlier ones elided with an explicit marker. That is
a change to what the judge sees, which means it is a rubric-meaning change and
needs its own anchor-set run. Find out first.

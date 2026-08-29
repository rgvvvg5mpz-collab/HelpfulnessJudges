"""Run the helpfulness judge suite over a set of conversations.

    python -m harness.run_judges --input data/gold/examples.jsonl --out runs/latest.jsonl
    python -m harness.run_judges --input data/gold/examples.jsonl --batch --k 5

Two execution modes:

* **live** — one `messages.create` per (conversation, judge, sample). Use for
  small sets and for interactive rubric iteration.
* **batch** — one Batches API job for the whole cross-product at 50% cost. Use
  for the offline regression suite and for gold-set scoring, which is where the
  volume is.

Every score is written with the judge prompt hash, model id, and sampling
parameters alongside it. A score you cannot reproduce is not evidence, and the
validation protocol in docs/validation-protocol.md depends on being able to
re-derive any historical number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from harness.judge_spec import JudgeSpec, SCORING_TAIL, load_judges, load_shared_preamble
from harness.transcript import Conversation, conversation_from_dict
from harness.turn_policy import TurnMode, turns_to_score

# Judge model. Deliberately pinned, never a floating alias: a silent vendor
# model change is indistinguishable from a chatbot regression on the dashboard.
DEFAULT_MODEL = "claude-opus-5"

# Adaptive thinking is on by default on Opus 5 and its tokens count against
# max_tokens. At effort=high a judge can think for a while before emitting a
# short JSON object, so this is sized for the thinking, not for the output —
# hitting the cap truncates mid-object and costs a full retry.
MAX_TOKENS = 16000


@dataclass
class JudgeCall:
    conversation_id: str
    judge_id: str
    judge_version: str
    target_turn: int | None
    sample: int
    system: list[dict[str, Any]]
    user: str
    model: str
    effort: str
    schema: dict[str, Any]

    @property
    def custom_id(self) -> str:
        turn = "conv" if self.target_turn is None else f"t{self.target_turn}"
        return f"{self.conversation_id}|{self.judge_id}|{turn}|s{self.sample}"

    @property
    def prompt_hash(self) -> str:
        blob = json.dumps(
            [self.system, self.user, self.schema], sort_keys=True
        ).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def build_call(
    spec: JudgeSpec,
    conversation: Conversation,
    target_turn: int | None,
    sample: int,
    preamble: str,
    tail_template: str,
    model: str | None = None,
) -> JudgeCall:
    """Assemble one judge call.

    Prompt layout, and why:

    * `system` holds the shared preamble followed by this judge's rubric. Both
      are byte-stable across every conversation, so the whole system block is
      one cache prefix. The shared preamble is first and identical for all
      twelve judges, so it stays cached across judges too.
    * `messages[0]` holds the transcript, then the scoring tail. The tail
      restates that the transcript is data and re-states the output order
      *after* the untrusted content — the injection-hardening literature is
      explicit that the instructions the model acts on last should not be
      followed by attacker-controlled text.
    """
    tail = tail_template.replace(
        "{{scope}}",
        "the whole conversation"
        if target_turn is None
        else f"turn {target_turn} (the assistant turn marked `>>> TARGET`)",
    )
    return JudgeCall(
        conversation_id=conversation.id,
        judge_id=spec.id,
        judge_version=spec.version,
        target_turn=target_turn,
        sample=sample,
        system=[
            {"type": "text", "text": preamble},
            {
                "type": "text",
                "text": spec.body,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        user=conversation.render(target_turn) + "\n\n" + tail,
        model=model or spec.model,
        effort=spec.effort,
        schema=spec.output_schema,
    )


def request_params(call: JudgeCall) -> dict[str, Any]:
    return {
        "model": call.model,
        "max_tokens": MAX_TOKENS,
        "system": call.system,
        "messages": [{"role": "user", "content": call.user}],
        "output_config": {
            "effort": call.effort,
            "format": {"type": "json_schema", "schema": call.schema},
        },
    }


def plan_calls(
    conversations: list[Conversation],
    specs: list[JudgeSpec],
    k: int,
    model: str | None,
    turn_mode: TurnMode = "all",
) -> list[JudgeCall]:
    preamble = load_shared_preamble()
    tail = SCORING_TAIL.read_text(encoding="utf-8").strip()
    calls: list[JudgeCall] = []
    for conv in conversations:
        for spec in specs:
            # No judge is ever skipped. Every judge scores every item of its
            # declared unit and always returns a level.
            for target in turns_to_score(spec, conv, turn_mode):
                for sample in range(k):
                    calls.append(
                        build_call(spec, conv, target, sample, preamble, tail, model)
                    )
    return calls


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def _parse_message(message: Any) -> dict[str, Any]:
    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


def run_live(
    client: anthropic.Anthropic, calls: list[JudgeCall], concurrency: int
) -> Iterator[dict[str, Any]]:
    def one(call: JudgeCall) -> dict[str, Any]:
        try:
            message = client.messages.create(**request_params(call))
            return _record(call, _parse_message(message), message.usage)
        except anthropic.APIStatusError as exc:
            return _record(call, None, None, error=f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        yield from pool.map(one, calls)


def run_batch(
    client: anthropic.Anthropic, calls: list[JudgeCall], poll_seconds: int = 60
) -> Iterator[dict[str, Any]]:
    by_id = {c.custom_id: c for c in calls}
    batch = client.messages.batches.create(
        requests=[
            Request(
                custom_id=c.custom_id,
                params=MessageCreateParamsNonStreaming(**request_params(c)),
            )
            for c in calls
        ]
    )
    print(f"batch {batch.id}: {len(calls)} requests submitted", file=sys.stderr)
    while True:
        status = client.messages.batches.retrieve(batch.id)
        if status.processing_status == "ended":
            break
        print(
            f"  {status.processing_status}: "
            f"{status.request_counts.processing} processing, "
            f"{status.request_counts.succeeded} done",
            file=sys.stderr,
        )
        time.sleep(poll_seconds)

    # Results come back in arbitrary order; key by custom_id, never by position.
    for result in client.messages.batches.results(batch.id):
        call = by_id[result.custom_id]
        if result.result.type == "succeeded":
            yield _record(call, _parse_message(result.result.message), result.result.message.usage)
        else:
            yield _record(call, None, None, error=result.result.type)


def _record(
    call: JudgeCall,
    verdict: dict[str, Any] | None,
    usage: Any,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "conversation_id": call.conversation_id,
        "judge_id": call.judge_id,
        "judge_version": call.judge_version,
        "judge_prompt_hash": call.prompt_hash,
        "model": call.model,
        "effort": call.effort,
        "target_turn": call.target_turn,
        "sample": call.sample,
        "verdict": verdict,
        "error": error,
        "usage": (
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(
                    usage, "cache_creation_input_tokens", 0
                ),
            }
            if usage is not None
            else None
        ),
    }


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def aggregate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse k samples per item to a verdict, and keep the disagreement.

    On a three-level scale the median is the item's verdict, but the spread is
    what tells you whether to trust it. A judge that returned 0 / 0.5 / 1.0 on
    three runs of the same item has not scored it — it has guessed three times.

    The 0-vs-not-0 boundary is treated as special. Levels differ in kind, not
    just degree: 1.0-vs-0.5 is a disagreement about polish, 0.5-vs-0 is a
    disagreement about whether something was broken. Any split across that
    boundary routes to a human regardless of which side the median landed on.
    """
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for rec in records:
        if rec["verdict"] is None:
            continue
        key = (rec["conversation_id"], rec["judge_id"], rec["target_turn"])
        buckets.setdefault(key, []).append(rec)

    out = []
    for (conv_id, judge_id, target), group in sorted(
        buckets.items(), key=lambda kv: str(kv[0])
    ):
        scores = [g["verdict"]["score"] for g in group]
        confidences = [g["verdict"]["confidence"] for g in group]
        injection = any(g["verdict"].get("injection_suspected") for g in group)

        unanimous = len(set(scores)) == 1
        split_on_failure = (0 in scores) and any(sc > 0 for sc in scores)
        low_conf = "low" in confidences

        out.append(
            {
                "conversation_id": conv_id,
                "judge_id": judge_id,
                "target_turn": target,
                "k": len(scores),
                # The item's verdict.
                "score": statistics.median(scores),
                # For rate reporting across a population; on this scale the mean
                # of many 0/0.5/1.0 items is the fine-grained number, not the
                # per-item score.
                "mean": sum(scores) / len(scores),
                "scores": scores,
                "unanimous": unanimous,
                "hard_failure_votes": sum(1 for sc in scores if sc == 0),
                # trigger_present is a reporting key, never a gate: it never
                # changes a score, but a dimension's rate should be readable
                # both overall and on the items where it was actually at risk.
                "trigger_rate": sum(
                    1 for g in group if g["verdict"].get("trigger_present")
                )
                / len(group),
                "any_low_confidence": low_conf,
                "injection_suspected": injection,
                "needs_human_review": split_on_failure or low_conf or injection,
                "review_reason": (
                    "split across the hard-failure boundary"
                    if split_on_failure
                    else "judge reported low confidence"
                    if low_conf
                    else "possible injection in transcript"
                    if injection
                    else None
                ),
                "judge_version": group[0]["judge_version"],
                "judge_prompt_hash": group[0]["judge_prompt_hash"],
                "model": group[0]["model"],
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="JSONL of conversations")
    ap.add_argument("--out", default="runs/latest.jsonl")
    ap.add_argument("--judges", nargs="*", default=None, help="judge ids (default: all)")
    ap.add_argument(
        "--traced",
        action="store_true",
        help="use the trace-verified variant of any judge that has one. Requires the "
        "transcript to carry real tool traces; see judges/capability-honesty-traced.md.",
    )
    ap.add_argument("--k", type=int, default=5, help="samples per item (median-aggregated)")
    ap.add_argument("--batch", action="store_true", help="use the Batches API (50%% cost)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--model", default=None, help="override the per-judge model")
    ap.add_argument(
        "--turns",
        choices=["all", "last"],
        default="all",
        help="turn-unit judges score every assistant turn (all, default) or only the "
        "final one (last, for cheap production sampling). A coverage trade, not a gate.",
    )
    ap.add_argument("--dry-run", action="store_true", help="plan calls and print one prompt")
    args = ap.parse_args()

    conversations = [
        conversation_from_dict(json.loads(line))
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    specs = load_judges(args.judges, prefer_traced=args.traced)
    calls = plan_calls(conversations, specs, args.k, args.model, args.turns)
    print(
        f"{len(conversations)} conversations x {len(specs)} judges x k={args.k} "
        f"(turns={args.turns}) = {len(calls)} judge calls",
        file=sys.stderr,
    )

    if args.dry_run:
        sample = calls[0]
        print("=" * 70)
        print(f"custom_id: {sample.custom_id}   prompt_hash: {sample.prompt_hash}")
        print("=" * 70)
        print("--- SYSTEM ---")
        for block in sample.system:
            print(block["text"])
        print("\n--- USER ---")
        print(sample.user)
        print("\n--- SCHEMA ---")
        print(json.dumps(sample.schema, indent=2))
        return 0

    client = anthropic.Anthropic()
    runner = run_batch(client, calls) if args.batch else run_live(client, calls, args.concurrency)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in runner:
            records.append(rec)
            fh.write(json.dumps(rec) + "\n")

    agg_path = out_path.with_suffix(".aggregated.jsonl")
    rows = aggregate(records)
    with agg_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    errors = sum(1 for r in records if r["error"])
    review = sum(1 for r in rows if r["needs_human_review"])
    if rows:
        fails = sum(1 for r in rows if r["score"] == 0)
        warns = sum(1 for r in rows if r["score"] == 0.5)
        passes = sum(1 for r in rows if r["score"] == 1.0)
        print(
            f"verdicts: {passes} pass / {warns} warning / {fails} hard failure "
            f"(suite mean {sum(r['score'] for r in rows) / len(rows):.3f})",
            file=sys.stderr,
        )
    print(
        f"wrote {len(records)} raw records to {out_path} "
        f"({errors} errors), {len(rows)} aggregated to {agg_path} "
        f"({review} flagged for human review)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

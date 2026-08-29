"""Run one judge against one rendered transcript, via the Anthropic Messages API.

This is the module that makes the AgentCore path higher-fidelity than the Arize
one. Because a code-based evaluator is our own Lambda, we construct the request
ourselves and therefore keep everything the hosted-evaluator paths take away:

  * `output_config.format` json_schema — the verdict is schema-enforced at
    GENERATION time, not merely parsed afterwards.
  * `output_config.effort` — the suite's `effort: high`, which Arize cannot
    express for Anthropic at all (no reasoning control in its schema).
  * `cache_control` on the shared preamble — k=5 repeats an identical prefix
    five times, and all ten rubrics share the same 132-line preamble.
  * k=5 with a median and the spread, which no hosted evaluator offers.

Deliberately NOT routed through bedrock-runtime: Claude Opus 5 does not support
structured outputs on Bedrock, so that would force a choice between the pinned
judge model and the json_schema guarantee — and changing judge model requires a
full anchor-set re-baseline.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
from dataclasses import dataclass, field
from typing import Any

from .spec import Rubric, load_preamble, load_tail, output_schema

MODEL = os.environ.get("JUDGE_MODEL", "claude-opus-5")
EFFORT = os.environ.get("JUDGE_EFFORT", "high")
MAX_TOKENS = int(os.environ.get("JUDGE_MAX_TOKENS", "16000"))
K = int(os.environ.get("JUDGE_K", "5"))


@dataclass
class Verdict:
    judge_id: str
    score: float
    payload: dict[str, Any]
    samples: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {1.0: "1.0", 0.5: "0.5", 0.0: "0"}[float(self.score)]

    @property
    def unanimous(self) -> bool:
        return len(set(self.samples)) <= 1

    @property
    def hard_failure_votes(self) -> int:
        return sum(1 for s in self.samples if s == 0)

    @property
    def needs_human_review(self) -> bool:
        """Split across the 0-vs-not-0 boundary, low confidence, or injection.

        The levels differ in kind: 1.0-vs-0.5 is a disagreement about polish,
        0.5-vs-0 about whether something was broken. Only the second escalates.
        """
        split = 0 in self.samples and any(s > 0 for s in self.samples)
        return bool(
            split
            or self.payload.get("confidence") == "low"
            or self.payload.get("injection_suspected")
        )


def build_request(rubric: Rubric, transcript: str, scope: str) -> dict[str, Any]:
    """The Messages API request for one judge call. Pure — no network."""
    tail = load_tail().replace("{{scope}}", scope)
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [
            # Byte-identical across all ten judges and every k, so it is the
            # cache prefix. Keep it first and never interpolate into it.
            {"type": "text", "text": load_preamble()},
            {"type": "text", "text": rubric.body,
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": f"{transcript}\n\n{tail}"}],
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": output_schema(rubric)},
        },
    }


def _one(client: Any, req: dict[str, Any]) -> dict[str, Any]:
    msg = client.messages.create(**req)
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def run(rubric: Rubric, transcript: str, scope: str, *, k: int = K,
        client: Any = None) -> Verdict:
    """Score one item at k samples and take the median.

    k fires concurrently because a code-based evaluator has a hard Lambda
    ceiling (300s max, configurable down). Five sequential Opus calls at
    effort:high would not fit; five concurrent ones are roughly one call's
    latency plus jitter.
    """
    if client is None:
        import anthropic  # lazy: offline commands must not need the SDK
        client = anthropic.Anthropic()

    req = build_request(rubric, transcript, scope)
    payloads: list[dict[str, Any]] = []
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=k) as pool:
        for fut in [pool.submit(_one, client, req) for _ in range(k)]:
            try:
                payloads.append(fut.result())
            except Exception as e:  # noqa: BLE001 — one bad sample must not sink the item
                errors.append(f"{type(e).__name__}: {e}")

    if not payloads:
        raise RuntimeError(f"{rubric.id}: all {k} samples failed: {errors[:2]}")

    scores = [float(p["score"]) for p in payloads]
    median = statistics.median(scores)
    # Report the payload belonging to a sample that agrees with the median, so
    # the reasoning a human reads actually justifies the score they see.
    chosen = next((p for p in payloads if float(p["score"]) == median), payloads[0])
    return Verdict(judge_id=rubric.id, score=median, payload=chosen,
                   samples=scores, errors=errors)

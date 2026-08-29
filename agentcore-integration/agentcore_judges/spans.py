"""Reconstruct a conversation from AgentCore's CloudWatch/OTel spans.

This is the module that makes CloudWatch the trace source. AgentCore hands a
code-based evaluator raw `sessionSpans` — OpenTelemetry spans read from the
CloudWatch log groups named in the online evaluation config — and this turns them
back into the transcript the rubrics were written against.

The rendering is byte-identical to the parent repo's `harness/transcript.py`. The
rubrics and `_preamble.md` reference its markers BY NAME — `[BEGIN TRANSCRIPT]`,
`--- turn N | CUSTOMER ---`, ` >>> TARGET`, `  [tool trace: ...]`. If the
rendering drifts, every rubric's meaning drifts with it and no test will say so.
A test in this folder asserts the two agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

RENDERER_VERSION = "transcript/1.0.0"

Role = Literal["customer", "assistant"]

# OTel GenAI / OpenInference attribute keys we read, most specific first.
INPUT_KEYS = ("gen_ai.prompt", "input.value", "gen_ai.input.messages")
OUTPUT_KEYS = ("gen_ai.completion", "output.value", "gen_ai.output.messages")
TOOL_NAME_KEYS = ("gen_ai.tool.name", "tool.name")
TOOL_ARGS_KEYS = ("gen_ai.tool.arguments", "tool.parameters", "input.value")
TOOL_RESULT_KEYS = ("gen_ai.tool.result", "output.value")


@dataclass
class Turn:
    role: Role
    text: str
    tool_trace: list[str] = field(default_factory=list)


@dataclass
class Conversation:
    session_id: str
    turns: list[Turn]
    trace_ids: list[str] = field(default_factory=list)

    def assistant_turn_indices(self) -> list[int]:
        return [i for i, t in enumerate(self.turns) if t.role == "assistant"]

    def render(self, target_turn: int | None) -> str:
        """Byte-identical to harness/transcript.py::Conversation.render."""
        lines = ["[BEGIN TRANSCRIPT]"]
        for i, turn in enumerate(self.turns):
            marker = " >>> TARGET" if i == target_turn else ""
            label = "CUSTOMER" if turn.role == "customer" else "ASSISTANT"
            lines.append(f"\n--- turn {i} | {label}{marker} ---")
            lines.append(turn.text)
            if turn.tool_trace:
                lines.append("  [tool trace: " + "; ".join(turn.tool_trace) + "]")
            elif turn.role == "assistant":
                # Load-bearing sentinel: capability-honesty-traced branches on
                # this exact string and drops to confidence: low.
                lines.append("  [tool trace: none recorded]")
        return "\n".join(lines)


def _attr(span: dict[str, Any], keys: Iterable[str]) -> str | None:
    attrs = span.get("attributes") or {}
    for k in keys:
        v = attrs.get(k)
        if v not in (None, ""):
            return v if isinstance(v, str) else str(v)
    return None


def _kind(span: dict[str, Any]) -> str:
    a = span.get("attributes") or {}
    return str(
        a.get("openinference.span.kind")
        or a.get("gen_ai.operation.name")
        or span.get("name", "")
    ).upper()


def _start(span: dict[str, Any]) -> int:
    for k in ("startTimeUnixNano", "start_time_unix_nano", "startTime", "timeUnixNano"):
        if span.get(k) is not None:
            try:
                return int(span[k])
            except (TypeError, ValueError):
                continue
    return 0


def _tool_entry(span: dict[str, Any]) -> str:
    """One tool-trace line.

    The status matters as much as the result: `capability-honesty-traced`'s C5
    asks whether the turn disclosed a failed lookup rather than answering around
    it, and that check fails silently if the trace only ever shows successes.
    """
    name = _attr(span, TOOL_NAME_KEYS) or "tool"
    args = _attr(span, TOOL_ARGS_KEYS) or ""
    status = ((span.get("status") or {}).get("code") or "").upper()
    if status in ("ERROR", "STATUS_CODE_ERROR"):
        msg = (span.get("status") or {}).get("message") or "error"
        return f"{name}({args}) -> ERROR {msg}"
    result = _attr(span, TOOL_RESULT_KEYS)
    if result is None:
        return f"{name}({args}) -> (no result recorded)"
    if result.strip() in ("", "[]", "{}", "null"):
        return f"{name}({args}) -> EMPTY"
    return f"{name}({args}) -> {result}"


def _trace_of(span: dict[str, Any]) -> str:
    return str(span.get("traceId") or (span.get("context") or {}).get("trace_id") or "")


def from_session_spans(session_spans: list[dict[str, Any]],
                       session_id: str = "") -> Conversation:
    """Rebuild the conversation from an AgentCore `sessionSpans` payload.

    Spans are grouped BY TRACE first, because one trace is one request/response
    turn. Tool spans attach to the assistant turn of *their own* trace — not to
    whichever assistant turn happened to be built most recently. Getting this
    wrong reconciles a turn's claims against a different turn's tool calls, which
    is precisely the failure `capability-honesty-traced` exists to detect.
    """
    spans = sorted(session_spans or [], key=_start)

    order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sp in spans:
        tid = _trace_of(sp)
        if tid not in grouped:
            grouped[tid] = []
            order.append(tid)
        grouped[tid].append(sp)

    turns: list[Turn] = []
    trace_ids: list[str] = []

    for tid in order:
        group = grouped[tid]
        tools = [_tool_entry(sp) for sp in group if "TOOL" in _kind(sp)]
        llm = [sp for sp in group
               if any(k in _kind(sp) for k in ("LLM", "CHAT", "AGENT", "CHAIN"))]
        if not llm:
            # A trace with only tool spans: hang them on the previous assistant
            # turn, which is the best available attribution.
            if tools and turns and turns[-1].role == "assistant":
                turns[-1].tool_trace.extend(tools)
            continue
        if tid:
            trace_ids.append(tid)
        for sp in llm:
            user = _attr(sp, INPUT_KEYS)
            assistant = _attr(sp, OUTPUT_KEYS)
            if user:
                turns.append(Turn("customer", user))
            if assistant is not None:
                turns.append(Turn("assistant", assistant, list(tools)))
                tools = []
        if tools and turns and turns[-1].role == "assistant":
            turns[-1].tool_trace.extend(tools)

    return Conversation(session_id=session_id, turns=turns, trace_ids=trace_ids)


def target_index(conv: Conversation, trace_ids: list[str] | None) -> int | None:
    """Which assistant turn a TRACE-level evaluation is pointed at.

    AgentCore passes `evaluationTarget.traceIds`. One trace is one request/
    response turn, so its position among the traces gives the turn. Falls back to
    the last assistant turn, which is the common single-turn case.
    """
    idx = conv.assistant_turn_indices()
    if not idx:
        return None
    if trace_ids:
        for pos, tid in enumerate(conv.trace_ids):
            if tid in trace_ids:
                return idx[pos] if pos < len(idx) else idx[-1]
    return idx[-1]

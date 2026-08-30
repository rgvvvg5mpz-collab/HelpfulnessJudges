"""Compile a vendored rubric into an Arize AX template string.

The rubrics stay the source of truth reviewed by compliance. The AX template is
a *build artifact* with a hash — never hand-edited in the Eval Hub, or the git
rubrics and the deployed rubrics diverge within a month and nobody can say which
one produced a given score.

Template layout, mirroring what the parent harness sends to Anthropic:

    <_preamble.md>            byte-identical to the reviewed original
    <rubric body>             byte-identical, frontmatter stripped
    [BEGIN TRANSCRIPT]
    {transcript}              the one mapped variable
    <_scoring-tail-ax.md>     rewritten: payload rides in `explanation`

BRACE STYLE IS UNRESOLVED UPSTREAM. Arize's REST schema and EvaluatorTemplate
docs show single-brace `{var}`; the `ax` CLI and Arize's own evaluator skill show
double-brace `{{var}}`. A half-converted template does not error — it renders the
variable as literal text and the judge silently scores a placeholder. Settle it
with one throwaway evaluator and the Data Preview before compiling all eleven
(docs/RUNBOOK.md step 2), then pin the answer here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .spec import Rubric, load_preamble, load_tail

BraceStyle = str  # "single" | "double"


def _escape(text: str, style: BraceStyle) -> str:
    """Protect literal braces in rubric prose from the server-side formatter."""
    if style == "single":
        # f-string style: a literal brace must be doubled.
        return text.replace("{", "{{").replace("}", "}}")
    if style == "double":
        # Mustache style: only `{{` opens a tag, so single braces are safe.
        # Our rubrics contain `{}` in JSON examples but no `{{`, which we assert.
        if "{{" in text:
            raise ValueError(
                "rubric contains a literal '{{' which would be read as a mustache "
                "tag; escape it before rendering in double-brace mode"
            )
        return text
    raise ValueError(f"unknown brace style {style!r}")


def unescape(text: str, style: BraceStyle) -> str:
    """Undo `_escape`. AX applies `.format` server-side, which collapses the
    doubled braces back to single ones before the model ever sees the prompt.
    Anything scoring the template locally has to do the same or it grades a
    prompt the platform will never send — the rubrics carry `{}` in their JSON
    examples, so the difference is real, not theoretical.
    """
    if style == "single":
        return text.replace("{{", "{").replace("}}", "}")
    return text


def _var(name: str, style: BraceStyle) -> str:
    return f"{{{name}}}" if style == "single" else f"{{{{{name}}}}}"


@dataclass(frozen=True)
class RenderedTemplate:
    judge_id: str
    unit: str
    variable: str
    template: str
    brace_style: BraceStyle = "single"

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()[:16]

    @property
    def chars(self) -> int:
        return len(self.template)


def render(rubric: Rubric, brace_style: BraceStyle = "single") -> RenderedTemplate:
    """Compile one rubric. Deterministic — same input, same hash."""
    variable = "transcript" if rubric.unit == "turn" else "conversation_transcript"
    scope = (
        "the assistant turn marked `>>> TARGET`"
        if rubric.unit == "turn"
        else "the whole conversation"
    )

    tail = load_tail().replace("{{scope}}", scope)

    parts = [
        _escape(load_preamble(), brace_style),
        "",
        "---",
        "",
        _escape(rubric.body, brace_style),
        "",
        "---",
        "",
        _var(variable, brace_style),
        "",
        _escape(tail, brace_style),
    ]
    return RenderedTemplate(
        judge_id=rubric.id,
        unit=rubric.unit,
        variable=variable,
        template="\n".join(parts),
        brace_style=brace_style,
    )


def render_all(rubrics: list[Rubric], brace_style: BraceStyle = "single") -> list[RenderedTemplate]:
    return [render(r, brace_style) for r in rubrics]

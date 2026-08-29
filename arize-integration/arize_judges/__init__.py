"""Deploy the helpfulness judge suite as native Arize AX template evaluators.

Standalone: this package does not import from the parent repository. The rubrics
it compiles are vendored under `rubrics/`, with hashes in
`rubrics/RUBRIC_PROVENANCE.json` so drift from the reviewed originals is
detectable rather than silent.

Nothing here calls Arize unless you invoke `provision`, `backfill` or `export`.
`render`, `verify` and `check-drift` are offline and need only PyYAML.
"""

__version__ = "1.0.0"

SCALE_CHOICES = {"1.0": 1.0, "0.5": 0.5, "0": 0.0}
"""Arize `classification_choices`.

The label strings are the numerals themselves, not fail/warn/pass. That is
deliberate and load-bearing: it lets `_preamble.md`'s scale section and every
rubric's Score section stay byte-identical to the reviewed originals, which is
what keeps the anchor-set diff small. Arize enforces these three as a tool enum
and writes `.score` 1.0/0.5/0.0 alongside the string `.label`.
"""

PAYLOAD_VERSION = 1
TRANSCRIPT_ATTR = "attributes.judge.transcript"
CONVERSATION_ATTR = "attributes.judge.conversation_transcript"

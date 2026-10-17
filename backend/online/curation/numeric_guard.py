"""Numeric- and period-verbatim guards for the FFF rewriter. Never calls an LLM.

Every numeric token an FFF emits must appear verbatim in the source
proposition it claims — same shared check (`contracts.numeric.appears_verbatim`)
the segmenter and CoVe's comparator use, so all guards agree by construction.
Failing values are rejected, never repaired.

Period guard exists because of a traced real failure: CoVe missed a
wrong-year answer that actually originated upstream, when the FFF rewriter
relabeled a proposition stated "as of Dec 31, 2022" as `period_label:
"FY2023"` to match the question. The value guard didn't cover this since
period wasn't checked. A verifier can only confirm claims against the
evidence it's given, so a lying period sails through undetected downstream.
Rejects only on confirmed mismatch, never on failure to parse.
"""
from __future__ import annotations

from contracts.numeric import appears_verbatim, parse_fiscal_period


def validate_numeric_verbatim(value_text: str, source_text: str) -> bool:
    """True if every numeric token in `value_text` shows up, normalized, in
    `source_text`. No numeric tokens at all vacuously passes."""
    return appears_verbatim(value_text, source_text)


def validate_period_verbatim(period_label: str, source_text: str) -> bool:
    """True if `period_label` matches the period the source actually
    states, compared as canonical (fiscal_year, quarter) tuples.

    Passes if either side fails to parse (can't confirm a violation, won't
    guess one) — an accepted false-negative risk, but the only false
    positives this closes are confirmable fabricated periods."""
    fff_period = parse_fiscal_period(period_label)
    source_period = parse_fiscal_period(source_text)
    if fff_period is None or source_period is None:
        return True
    return fff_period == source_period

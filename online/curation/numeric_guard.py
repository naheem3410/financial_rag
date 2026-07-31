"""Numeric- and period-verbatim guards for the FFF rewriter. Must never
call an LLM.

Every numeric token an FFF rewriter emits in `value_text` must appear
verbatim in the source proposition it claims to rewrite. This is the exact
same guard `offline/propositions/segmenter_llm.py` applies to the
proposition segmenter and CoVe's comparator applies to verification
claims — one shared implementation (`contracts.numeric.appears_verbatim`),
so every numeric guard in the system agrees by construction, not by
convention. A value failing this check is rejected, never repaired.

The period-verbatim guard exists because of a real, traced failure: a live
run found one case where CoVe's independent verification failed to flag a
wrong-year answer, and the corruption turned out to happen upstream of
CoVe entirely -- the FFF rewriter itself relabeled a proposition genuinely
stated "as of December 31, 2022" with `period_label: "FY2023"`, matching
what the question asked for rather than what the source actually said. The
numeric-verbatim guard only ever checked that the value was faithful; the
period was unguarded, so nothing stopped this. A fact's period is as much
a part of it as its value. Once an FFF asserts a false period, no
downstream step can recover: the generator trusts it, and CoVe's
independent verification checks the draft against this same evidence -- a
verifier with no channel back to the true source text will correctly (from
its perspective) confirm a claim against evidence that was already lying
about its own period. This guard closes that gap the same way the numeric
guard closes the value gap: reject the FFF, never repair it, on a
confirmed disagreement -- never on mere inability to parse.
"""
from __future__ import annotations

from contracts.numeric import appears_verbatim, parse_fiscal_period


def validate_numeric_verbatim(value_text: str, source_text: str) -> bool:
    """True if every numeric token in `value_text` appears, normalized,
    somewhere in `source_text`. A candidate with no numeric tokens at all
    vacuously passes (nothing to verify) — matches `appears_verbatim`'s own
    contract."""
    return appears_verbatim(value_text, source_text)


def validate_period_verbatim(period_label: str, source_text: str) -> bool:
    """True if `period_label` is faithful to the period the source
    proposition itself states, compared as canonical `(fiscal_year,
    quarter)` tuples (`contracts.numeric.parse_fiscal_period`) — the same
    normalization CoVe's comparator uses, so both agree by construction.

    Rejects only on a confirmed disagreement: if either `period_label` or
    `source_text` doesn't parse to a recognizable period, this guard
    passes rather than guessing a violation it can't confirm — the same
    "no digit present = nothing to check" permissiveness
    `validate_numeric_verbatim` already applies to values. A false negative
    here (an unparseable but genuinely wrong period slipping through) is an
    accepted risk this guard doesn't try to close; it only closes the
    specific, confirmable case where the FFF rewriter fabricated a period
    the source never stated."""
    fff_period = parse_fiscal_period(period_label)
    source_period = parse_fiscal_period(source_text)
    if fff_period is None or source_period is None:
        return True
    return fff_period == source_period

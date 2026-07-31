"""Phase 0 acceptance tests.

These eleven tests encode the thesis. Four are load-bearing: if any fails, the
faithfulness claim is unsupported regardless of what the RAGAS numbers say.

At Phase 0, none of the components under test exist yet — every test below
fails via `ImportError`/`ModuleNotFoundError` until Phase 1-5 implement the
referenced modules. Imports are local to each test function so one missing
module does not mask the other ten. Do not implement the referenced modules
as part of Phase 0; that is the point of this file.

The four load-bearing tests (test_numeric_comparison_deterministic,
test_citation_no_llm, test_cove_independence, test_numeric_verbatim_guard),
plus test_path_b_no_llm, use the `llm_call_counter` / `payload_capture`
fixtures from `conftest.py` instead of ad hoc monkeypatching, so their
pass/fail is grounded in the real `LLMClient.complete` boundary — see
`test_llm_instrumentation.py` for the meta-tests proving those fixtures work.
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.models import (
    Confidence,
    FFF,
    Proposition,
    PropositionMetadata,
    RequestContext,
    SourceRef,
    VerificationClaimResult,
)


def _make_proposition(proposition_id: str, text: str, page: int = 34) -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="Acme Corp",
            filing_type="10-Q",
            filing_date="2024-11-05",
            section="MD&A",
            page=page,
            source_uri="acme_10q_2024q3.pdf",
        ),
    )


def test_parser_plain_text(tmp_path):
    """A prose-only file with no tables parses cleanly. Parsing is
    format-driven, not content-driven: table logic must simply not fire."""
    from offline.parsers.layout_parser import parse_document

    text = (
        "3M Company reported net sales of $32.2 billion for fiscal year 2018. "
        "The increase was driven by organic growth across all business segments."
    )
    src = tmp_path / "prose.txt"
    src.write_text(text, encoding="utf-8")

    parsed = parse_document(src)

    assert parsed.tables == []
    assert "3M Company" in parsed.text


def test_path_b_no_llm(llm_call_counter):
    """CSV/XLSX/XBRL ingestion (Path B) issues zero LLM calls.
    A structured row is already a proposition; templating it is pure code.
    Exercises all three synthetic fixtures (tests/fixtures/path_b/) end to
    end — read -> map_schema -> template_row — through the real,
    now-implemented Path B modules (see tests/test_path_b.py for unit-level
    coverage of the mapping/templating logic itself)."""
    from offline.parsers.schema_mapper import (
        map_schema,
        read_csv_rows,
        read_xbrl_facts,
        read_xlsx_rows,
    )
    from offline.propositions.row_templating import template_rows

    fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "path_b"

    csv_rows = [map_schema(r, source_format="csv") for r in read_csv_rows(fixtures_dir / "metrics.csv")]
    xlsx_rows = [map_schema(r, source_format="xlsx") for r in read_xlsx_rows(fixtures_dir / "metrics.xlsx")]
    xbrl_rows = [map_schema(r, source_format="xbrl") for r in read_xbrl_facts(fixtures_dir / "facts.xbrl")]

    propositions = template_rows(csv_rows) + template_rows(xlsx_rows) + template_rows(xbrl_rows)

    assert len(propositions) == 3 + 2 + 1
    assert all(p.ingestion_path == "B_structured" for p in propositions)
    assert llm_call_counter.count == 0


def test_numeric_verbatim_guard(llm_call_counter):
    """An FFF whose value_text contains a digit absent from its source
    proposition must be rejected by the deterministic guard. The guard is
    deterministic: it must reject the fabrication by string comparison
    alone, never by asking an LLM, so a wrong future implementation that
    "checks with the model instead" is caught here too."""
    from online.curation.numeric_guard import validate_numeric_verbatim

    source = _make_proposition(
        "p_1", "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
    )

    fabricated_value_text = "19.9 percent"  # not present in the source span
    genuine_value_text = "18.2 percent"

    assert validate_numeric_verbatim(fabricated_value_text, source.text) is False
    assert validate_numeric_verbatim(genuine_value_text, source.text) is True
    assert llm_call_counter.count == 0


def test_placeholder_roundtrip():
    """Every substituted entity is restored exactly. The placeholder map is
    per-request state, never a global."""
    from online.placeholders.remapper import remap_query, reverse_map

    ctx = RequestContext(request_id="r_1", config_hash="cfg_abc")
    query = "How did Acme Corp's operating margin compare to Beta Inc's?"

    remapped = remap_query(query, ctx)
    restored = reverse_map(remapped, ctx)

    assert restored == query
    assert "Acme Corp" not in remapped
    assert "Beta Inc" not in remapped


def test_cove_independence(payload_capture):
    """Draft D is structurally absent from the independent verification
    call. `payload_capture` records what actually crossed the
    real `LLMClient.complete` boundary, so this checks the true call site
    rather than a hand-built dict a wrong implementation could bypass. The
    orchestrator function takes no `draft` parameter at all — independence is
    enforced by the signature, not by a prompt instruction the model could
    ignore."""
    from online.verification.cove_orchestrator import execute_independent_verification

    draft = "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."
    evidence = [
        FFF(
            fff_id="f_001",
            proposition_id="p_1",
            subject="<ENT_1>",
            metric="operating_margin",
            period_label="Q3 FY2024",
            value_text="18.2 percent",
            provenance_tier="retrieved",
            source_ref=SourceRef(filing_type="10-Q", filing_date="2024-11-05", page=34),
        )
    ]
    question = "What was <ENT_1> operating margin for Q3 FY2024?"

    execute_independent_verification(question, evidence)

    assert len(payload_capture) == 1
    sent = payload_capture[0]
    assert "draft" not in sent
    assert draft not in json.dumps(sent, default=str)


def test_numeric_comparison_deterministic(llm_call_counter):
    """Numeric claim verification issues zero LLM calls. 18.2 == 18.2 is a
    code-level equality check, never a model judgment."""
    from online.verification.numeric_comparator import numeric_match

    claim = {
        "asserted_value": "18.2",
        "asserted_unit": "percent",
        "period_label": "Q3 FY2024",
        "scale": "unit",
    }
    verification = {
        "answer_value": "18.2",
        "answer_unit": "percent",
        "period_label": "Q3 FY2024",
        "scale": "unit",
    }

    assert numeric_match(claim, verification) is True
    assert llm_call_counter.count == 0


def test_citation_no_llm(llm_call_counter):
    """Citation binding issues zero LLM calls. Citations are looked up from
    proposition_id metadata; an LLM never writes one."""
    from online.output.citation_binder import bind_citations

    prop = _make_proposition(
        "p_1", "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."
    )

    citations = bind_citations(
        answer_text="Acme Corp operating margin was 18.2 percent.",
        supporting_propositions=[prop],
    )

    assert llm_call_counter.count == 0
    assert citations[0]["source_uri"] == "acme_10q_2024q3.pdf"


def test_npce_off_passthrough():
    """With NPCE disabled, QueryTransformer is the identity: exactly one
    sub-query, no split, no collision detection."""
    from online.analysis.query_splitter import PassthroughQueryTransformer

    ctx = RequestContext(request_id="r_1", config_hash="cfg_abc")
    transformer = PassthroughQueryTransformer()

    sub_queries = transformer.transform(
        "What was Acme Corp's operating margin in Q3 FY2024?", ctx
    )

    assert len(sub_queries) == 1


def test_cove_off_passthrough():
    """With CoVe disabled, Verifier is a pass-through: the draft reaches
    citation binding unmodified."""
    from online.verification.cove_orchestrator import PassthroughVerifier

    ctx = RequestContext(request_id="r_1", config_hash="cfg_abc")
    verifier = PassthroughVerifier()
    draft = "Acme Corp operating margin for Q3 FY2024 was 18.2 percent."

    result = verifier.verify(draft, evidence=[], ctx=ctx)

    assert result.answer_text == draft
    assert result.verification_trace == ()


def test_three_index_invariant():
    """Dense index, sparse index, and metadata store must index an
    identical set of proposition_ids. Hybrid retrieval and citation binding
    both depend on this. Uses a FakeEmbedder (tests/doubles.py): this
    invariant is about the three stores agreeing on proposition_id sets, not
    about embedding quality, so the real BAAI/bge-base-en-v1.5 checkpoint
    (tests/test_bge_embedder.py) isn't needed here to keep this test fast."""
    from doubles import FakeEmbedder
    from offline.indexing.index_writers import IndexWriters

    props = [
        _make_proposition("p_1", "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent."),
        _make_proposition("p_2", "<ENT_1> net sales for Q3 FY2024 were $8.5 billion."),
    ]

    writers = IndexWriters(embedder=FakeEmbedder(dimension=8))
    writers.write(props)

    dense_ids = set(writers.dense_index.ids())
    sparse_ids = set(writers.sparse_index.ids())
    meta_ids = set(writers.metadata_store.ids())

    assert dense_ids == sparse_ids == meta_ids == {"p_1", "p_2"}


def test_abstention_on_missing_evidence():
    """A claim with no supporting evidence yields abstention, never a
    guess."""
    from online.output.abstention_gate import decide

    trace = [
        VerificationClaimResult(
            claim_id="c_1",
            claim_text="Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
            is_numeric=True,
            verdict="unsupported",
        )
    ]

    decision = decide(trace)

    assert decision.abstained is True
    assert "not supported" in decision.message.lower()

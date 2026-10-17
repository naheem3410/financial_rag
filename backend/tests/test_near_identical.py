"""online/curation/near_identical.py: several versions of the same fact
(decision 11, "top results almost the same, differ in one detail").
`detect_near_identical_versions` is pure code, no model; `answer_each_version`
is tested with a fake LLM client, no real model call."""
from __future__ import annotations

from contracts.models import Confidence, NumericRecord, Period, Proposition, PropositionMetadata, SubQuery
from online.curation.fff_rewriter import rewrite_to_ffts
from online.curation.near_identical import answer_each_version, detect_near_identical_versions


class _SequencedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, payload):
        self.calls.append(payload)
        return self._responses.pop(0)


def _prop(proposition_id: str, value: float, basis: str = "reported", fiscal_year: int = 2022, quarter=None, text: str = "text") -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(
            value=value, scale="millions", currency="USD", unit="absolute",
            period=Period(fiscal_year=fiscal_year, quarter=quarter, basis=basis),
        ),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="3M Company", filing_type="10-K", filing_date="2022-12-31",
            section="", page=1, source_uri="3M_2022_10K",
        ),
    )


# --- detect_near_identical_versions ---


def test_a_reported_and_restated_value_for_the_same_period_is_a_collision():
    props = [
        _prop("p_1", value=5777.0, basis="reported"),
        _prop("p_2", value=5820.0, basis="restated"),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "basis"
    labels = [label for label, _ in result.groups]
    assert labels == ["originally reported", "restated"]


def test_two_facts_with_the_same_basis_and_value_are_not_a_collision():
    """Genuinely the same fact retrieved twice -- nothing to flag."""
    props = [_prop("p_1", value=5777.0, basis="reported"), _prop("p_2", value=5777.0, basis="reported")]

    result = detect_near_identical_versions(props)

    assert result is None


def test_different_bases_but_the_same_value_is_not_flagged():
    """The plan's own bar is "differ in one detail" -- if the number is
    actually identical, there is nothing meaningfully different to show."""
    props = [_prop("p_1", value=5777.0, basis="reported"), _prop("p_2", value=5777.0, basis="restated")]

    result = detect_near_identical_versions(props)

    assert result is None


def test_a_single_proposition_is_never_a_collision():
    result = detect_near_identical_versions([_prop("p_1", value=5777.0)])

    assert result is None


def test_facts_from_different_periods_are_not_compared_to_each_other():
    props = [
        _prop("p_1", value=5777.0, basis="reported", fiscal_year=2022),
        _prop("p_2", value=6000.0, basis="restated", fiscal_year=2021),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_non_numeric_propositions_are_ignored():
    non_numeric = Proposition(
        proposition_id="p_text", text="some prose", provenance_tier="retrieved", ingestion_path="A_unstructured",
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(company="3M Company", filing_type="10-K", filing_date="2022-12-31", section="", page=1, source_uri="x"),
    )
    props = [non_numeric, _prop("p_1", value=5777.0)]

    result = detect_near_identical_versions(props)

    assert result is None


def test_quarterly_and_annual_periods_are_kept_separate():
    props = [
        _prop("p_1", value=5777.0, basis="reported", fiscal_year=2022, quarter=None),
        _prop("p_2", value=1500.0, basis="restated", fiscal_year=2022, quarter=4),
    ]

    result = detect_near_identical_versions(props)

    assert result is None  # different (fiscal_year, quarter) keys -- not the same period at all


def test_groups_carry_the_right_propositions():
    p_reported = _prop("p_1", value=5777.0, basis="reported")
    p_restated = _prop("p_2", value=5820.0, basis="restated")

    result = detect_near_identical_versions([p_reported, p_restated])

    assert result is not None
    groups_by_label = dict(result.groups)
    assert groups_by_label["originally reported"] == (p_reported,)
    assert groups_by_label["restated"] == (p_restated,)


# --- answer_each_version ---


def test_answer_each_version_labels_both_answers():
    p_reported = _prop("p_1", value=5777.0, basis="reported", text="3M Company had net income of $5,777 million for FY2022.")
    p_restated = _prop("p_2", value=5820.0, basis="restated", text="3M Company had restated net income of $5,820 million for FY2022.")
    collision = detect_near_identical_versions([p_reported, p_restated])
    assert collision is not None

    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="net_income", period=Period(fiscal_year=2022), period_label="FY2022")

    fff_response_reported = {"reasoning": "...", "facts": [
        {"proposition_id": "p_1", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,777 million"},
    ]}
    fff_response_restated = {"reasoning": "...", "facts": [
        {"proposition_id": "p_2", "subject": "<ENT_1>", "metric": "net_income", "period_label": "FY2022", "value_text": "$5,820 million"},
    ]}
    # fff_id is deterministic -- derive the real ids the same way other tests do,
    # so generate_answer's used_fff_ids guard has something real to match.
    real_ffts_reported = rewrite_to_ffts("q", [p_reported], _SequencedClient([fff_response_reported]), subject_override="<ENT_1>")
    real_ffts_restated = rewrite_to_ffts("q", [p_restated], _SequencedClient([fff_response_restated]), subject_override="<ENT_1>")

    fff_client = _SequencedClient([fff_response_reported, fff_response_restated])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,777 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,777 million.", "used_fff_ids": [real_ffts_reported[0].fff_id]},
        {"reasoning": "...", "answerable": True,
         "answers": [{"metric": "net_income", "answer_value": "$5,820 million", "answer_unit": "millions"}],
         "answer_text": "<ENT_1>'s net income was $5,820 million.", "used_fff_ids": [real_ffts_restated[0].fff_id]},
    ])

    results = answer_each_version(sub_query, "q", collision, fff_client, generator_client)

    assert len(results) == 2
    sub_answers = [sa for sa, _ in results]
    metrics = {sa.sub_query.metric for sa in sub_answers}
    assert metrics == {"net_income (originally reported)", "net_income (restated)"}
    labelled_answer_metrics = {a.metric for sa in sub_answers for a in sa.generated.answers}
    assert labelled_answer_metrics == {"net_income (originally reported)", "net_income (restated)"}
    for _, ffts in results:
        assert len(ffts) == 1  # each version paired with only its own facts, never the other's


def test_answer_each_version_gives_each_version_a_distinct_sub_query_id():
    """pair_facts keys ctx.fff_sets by sub_query_id -- two versions sharing
    one id would silently overwrite each other's facts."""
    p_reported = _prop("p_1", value=5777.0, basis="reported")
    p_restated = _prop("p_2", value=5820.0, basis="restated")
    collision = detect_near_identical_versions([p_reported, p_restated])
    assert collision is not None
    sub_query = SubQuery(sub_query_id="sq_1", entity="<ENT_1>", metric="net_income", period=Period(fiscal_year=2022), period_label="FY2022")

    fff_client = _SequencedClient([
        {"reasoning": "...", "facts": []}, {"reasoning": "...", "facts": []},
    ])
    generator_client = _SequencedClient([
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
        {"reasoning": "...", "answerable": False, "answers": [], "answer_text": "no evidence", "used_fff_ids": []},
    ])

    results = answer_each_version(sub_query, "q", collision, fff_client, generator_client)

    ids = {sa.sub_query.sub_query_id for sa, _ in results}
    assert len(ids) == 2
    assert "sq_1" not in ids  # both are distinct from the original, un-suffixed id

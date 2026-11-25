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


def _prop(proposition_id: str, value: float, basis: str = "reported", fiscal_year: int = 2022, quarter=None, text: str = "text", metrics: tuple = (), scale: str = "millions") -> Proposition:
    return Proposition(
        proposition_id=proposition_id,
        text=text,
        provenance_tier="retrieved",
        ingestion_path="A_unstructured",
        numeric=NumericRecord(
            value=value, scale=scale, currency="USD", unit="absolute",
            period=Period(fiscal_year=fiscal_year, quarter=quarter, basis=basis),
        ),
        confidence=Confidence(extraction_tier="rules"),
        metadata=PropositionMetadata(
            company="3M Company", filing_type="10-K", filing_date="2022-12-31",
            section="", page=1, source_uri="3M_2022_10K",
        ),
        metrics=metrics,
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


# --- decision 21, fix 2: overlapping metric wording within the same period/basis ---


def test_overlapping_metric_labels_with_different_values_is_a_collision():
    """Mirrors the real General Mills failure: "total receivables" and
    "customers receivables" both tagged `accounts_receivable`, same period,
    same basis, genuinely different values."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
        _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million.", metrics=("accounts_receivable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"
    labels = {label for label, _ in result.groups}
    assert labels == {"total", "customers"}


def test_two_overlapping_candidates_with_the_same_value_are_not_flagged():
    """Same canonical metric, same value -- genuinely the same fact, nothing
    to disambiguate."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
        _prop("p_2", value=1692.1, text="Trade receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_non_overlapping_metrics_with_different_values_are_not_flagged():
    """Two different real metrics for the same period and basis (e.g. net
    income and total revenue) are not a wording collision -- they were
    never the same candidate to begin with, the normal single-candidate
    case this check must not fragment."""
    props = [
        _prop("p_1", value=5777.0, text="Net income was $5,777 million.", metrics=("net_income",)),
        _prop("p_2", value=34229.0, text="Total revenue was $34,229 million.", metrics=("total_revenue",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_propositions_with_no_metric_labels_are_not_flagged():
    """Nothing to compare overlap on without the canonical `metrics` field
    -- do not guess from raw text alone."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million."),
        _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million."),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_metric_wording_collision_groups_carry_the_right_propositions():
    p_total = _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",))
    p_customer = _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million.", metrics=("accounts_receivable",))

    result = detect_near_identical_versions([p_total, p_customer])

    assert result is not None
    groups_by_label = dict(result.groups)
    assert groups_by_label["total"] == (p_total,)
    assert groups_by_label["customers"] == (p_customer,)


def test_metric_wording_check_does_not_override_a_real_basis_collision():
    """When both a basis difference and a metric-id overlap are present,
    the existing basis check (decision 11, more specific) still wins --
    fix 2 only fills the gap the basis check leaves open, never replaces it."""
    props = [
        _prop("p_1", value=5777.0, basis="reported", text="Net income, as reported, $5,777 million.", metrics=("net_income",)),
        _prop("p_2", value=5820.0, basis="restated", text="Net income, as restated, $5,820 million.", metrics=("net_income",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "basis"


# --- decision 30: metric-wording collision restricted to the top 2 ranked props ---


def test_genuine_ambiguity_both_candidates_in_top_two_still_fires():
    """General Mills shape: two real candidates, same label, different
    value, both near the top of the reranked pool (ranks 1 and 2)."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
        _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million.", metrics=("accounts_receivable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"


def test_noise_decoy_far_down_the_ranked_pool_is_not_flagged():
    """Adobe/American Water Works shape: the correct answer ranks #1, an
    unrelated same-labelled mention ranks #4 -- one proposition clearly
    outranks the other, so this is not genuine ambiguity."""
    props = [
        _prop("p_1", value=629551.0, text="Net income was $629,551 thousand.", metrics=("net_income",)),
        _prop("p_2", value=1.0, text="Unrelated proposition.", metrics=()),
        _prop("p_3", value=2.0, text="Another unrelated proposition.", metrics=()),
        _prop("p_4", value=400000.0, text="Net income attributable to noncontrolling interests was $400,000 thousand.", metrics=("net_income",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_boundary_second_candidate_at_rank_two_still_fires():
    """Rank 2 is inside the top-2 window -- still genuine ambiguity."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
        _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million.", metrics=("accounts_receivable",)),
        _prop("p_3", value=99.0, text="Unrelated proposition.", metrics=()),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"


def test_boundary_second_candidate_at_rank_three_does_not_fire():
    """Rank 3 is outside the top-2 window -- the same pair that fires at
    rank 2 above no longer fires once pushed one slot further down."""
    props = [
        _prop("p_1", value=1692.1, text="Total receivables were $1,692.1 million.", metrics=("accounts_receivable",)),
        _prop("p_3", value=99.0, text="Unrelated proposition.", metrics=()),
        _prop("p_2", value=1720.4, text="Customers receivables were $1,720.4 million.", metrics=("accounts_receivable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


# --- decision 34: the top-2 window is per-period, not the whole pool ---


def test_wrong_period_candidate_does_not_steal_a_top_two_slot_from_its_own_period_decision_34():
    """AES shape (decision 33's regression): a different-period candidate
    ranks between the two genuinely ambiguous same-period candidates. Before
    decision 34, the top-2 window was taken from the whole pool, so the
    wrong-period line occupied one of only two slots and the real pair never
    both landed inside it. The window must instead be the top 2 of each
    period's OWN candidates."""
    props = [
        _prop("p_470m", value=470.0, fiscal_year=2022, quarter=None, text="Increase in accounts payable was $470 million.", metrics=("accounts_payable",)),
        _prop("p_1730m", value=1730.0, fiscal_year=2022, quarter=4, text="Accounts payable was $1,730 million.", metrics=("accounts_payable",)),
        _prop("p_33m", value=33.0, fiscal_year=2022, quarter=4, text="Accounts payable to related parties was $33 million.", metrics=("accounts_payable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"
    values = {p.numeric.value for _, group in result.groups for p in group}
    assert values == {1730.0, 33.0}


# --- decision 39: a fact reported at two scales is one fact, not two ---


def test_same_fact_in_millions_and_billions_is_not_a_basis_collision():
    """Real Adobe shape: the same receivables balance, one proposition in
    billions, one in millions. Comparing raw floats sees 2.07 and 2065 as
    different numbers; comparing base units sees they are the same fact."""
    props = [
        _prop("p_1", value=2.07, basis="reported", text="Receivables were $2.07 billion.", scale="billions"),
        _prop("p_2", value=2070.0, basis="restated", text="Receivables were $2,070 million.", scale="millions"),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_same_fact_in_millions_and_billions_is_not_a_metric_wording_collision():
    props = [
        _prop(
            "p_1", value=2.07, text="Trade receivables were $2.07 billion.",
            metrics=("accounts_receivable",), scale="billions",
        ),
        _prop(
            "p_2", value=2070.0, text="Total receivables were $2,070 million.",
            metrics=("accounts_receivable",), scale="millions",
        ),
    ]

    result = detect_near_identical_versions(props)

    assert result is None


def test_genuinely_different_values_at_different_scales_still_collide():
    """Scale normalization must not hide a real collision -- $2.07 billion
    and $1,720 million are still two different numbers once both are
    converted to the same base unit."""
    props = [
        _prop(
            "p_1", value=2.07, text="Total receivables were $2.07 billion.",
            metrics=("accounts_receivable",), scale="billions",
        ),
        _prop(
            "p_2", value=1720.0, text="Customers receivables were $1,720 million.",
            metrics=("accounts_receivable",), scale="millions",
        ),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"


def test_wrong_period_candidate_in_the_other_rank_order_still_does_not_steal_the_slot_decision_34():
    """Same AES shape, reranked in the other order decision 33 observed
    (wrong-period candidate still ranked ahead of both real candidates) --
    the fix must not depend on which rank the wrong-period line lands at."""
    props = [
        _prop("p_470m", value=470.0, fiscal_year=2022, quarter=None, text="Increase in accounts payable was $470 million.", metrics=("accounts_payable",)),
        _prop("p_33m", value=33.0, fiscal_year=2022, quarter=4, text="Accounts payable to related parties was $33 million.", metrics=("accounts_payable",)),
        _prop("p_1730m", value=1730.0, fiscal_year=2022, quarter=4, text="Accounts payable was $1,730 million.", metrics=("accounts_payable",)),
    ]

    result = detect_near_identical_versions(props)

    assert result is not None
    assert result.differing_detail == "metric_wording"
    values = {p.numeric.value for _, group in result.groups for p in group}
    assert values == {1730.0, 33.0}


# --- optional reranker-score tie margin ---


def _boeing_shape():
    # Real Boeing FY2022 logits: headline 5.22, supply-chain note -1.78 (7.0 apart).
    headline = _prop("p_head", value=10200.0, metrics=("accounts_payable",),
                     text="The Boeing Company had accounts payable of $10,200 million at December 31, 2022.")
    note = _prop("p_note", value=2.5, scale="billions", metrics=("accounts_payable",),
                 text="At December 31, 2022, Accounts payable included $2.5 billion payable to suppliers.")
    return [headline, note], {"p_head": 5.22, "p_note": -1.78}


def test_without_a_margin_a_far_weaker_second_fact_still_fires():
    props, _ = _boeing_shape()
    assert detect_near_identical_versions(props) is not None


def test_with_a_margin_a_far_weaker_second_fact_is_not_a_rival():
    props, logits = _boeing_shape()
    assert detect_near_identical_versions(props, logits=logits, tie_margin=3.0) is None


def test_with_a_margin_a_genuinely_close_pair_still_fires():
    # Real General Mills FY2022 shape: total vs customers receivables, 0.24 apart.
    props = [
        _prop("p_total", value=1692.1, metrics=("accounts_receivable",), text="total receivables of $1,692.1 million"),
        _prop("p_cust", value=1720.4, metrics=("accounts_receivable",), text="customers receivables of $1,720.4 million"),
    ]
    result = detect_near_identical_versions(props, logits={"p_total": 5.74, "p_cust": 5.50}, tie_margin=3.0)
    assert result is not None and result.differing_detail == "metric_wording"


def test_a_margin_without_scores_changes_nothing():
    props, _ = _boeing_shape()
    assert detect_near_identical_versions(props, logits=None, tie_margin=3.0) is not None

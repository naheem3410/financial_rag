"""BGE embedder tests. `test_apply_query_prefix` needs no model; the rest
load the real pinned checkpoint (slow CPU inference) to verify against real output."""
from __future__ import annotations

from offline.embedding.bge_embedder import (
    EXPECTED_DIMENSION,
    MODEL_NAME,
    MODEL_REVISION,
    QUERY_PREFIX,
    BGEEmbedder,
    apply_query_prefix,
)


def test_apply_query_prefix_prepends_to_every_text():
    result = apply_query_prefix(["operating margin", "net sales"])

    assert result == [
        f"{QUERY_PREFIX}operating margin",
        f"{QUERY_PREFIX}net sales",
    ]


def test_apply_query_prefix_on_empty_list():
    assert apply_query_prefix([]) == []


def test_embedder_reports_pinned_identity_without_loading_the_model():
    """model_name/revision must be readable without touching the network
    or loading the model, since the manifest records them before any encoding."""
    embedder = BGEEmbedder()

    assert embedder.model_name == MODEL_NAME
    assert embedder.revision == MODEL_REVISION
    assert embedder._model is None  # still unloaded


def test_real_model_dimension_matches_expected():
    embedder = BGEEmbedder()

    assert embedder.dimension == EXPECTED_DIMENSION == 768


def test_real_model_query_and_passage_encoding_differ_for_identical_text():
    """Query and passage encodings of the same string must differ, since
    only the query path prepends BGE's search prefix."""
    embedder = BGEEmbedder()
    text = "Acme Corp operating margin for Q3 FY2024"

    [passage_vector] = embedder.encode_passages([text])
    [query_vector] = embedder.encode_queries([text])

    assert passage_vector != query_vector
    assert len(passage_vector) == len(query_vector) == EXPECTED_DIMENSION


def test_real_model_relevant_passage_scores_higher_as_a_query_than_unrelated_one():
    """Sanity check that the pinned checkpoint retrieves sensibly: a query
    should sit closer (cosine) to a relevant passage than an unrelated one."""
    import math

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b)

    embedder = BGEEmbedder()
    [query_vector] = embedder.encode_queries(["What was Acme Corp's operating margin in Q3 FY2024?"])
    relevant, unrelated = embedder.encode_passages(
        [
            "Acme Corp operating margin for Q3 FY2024 was 18.2 percent.",
            "The Eiffel Tower is a wrought-iron lattice tower in Paris, France.",
        ]
    )

    assert cosine(query_vector, relevant) > cosine(query_vector, unrelated)

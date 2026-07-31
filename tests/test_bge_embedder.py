"""Shared BGE embedder tests.

`test_apply_query_prefix` is fast and needs no model. The rest load the real
pinned BAAI/bge-base-en-v1.5 checkpoint via sentence-transformers — slower
(CPU inference), but this is exactly the asymmetric-prefix and dimension
behavior the user asked to be verified for real, not just asserted against a
fake.
"""
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
    """model_name/revision must be readable without touching the network or
    loading the model — the run manifest needs to record these even before
    any encoding happens."""
    embedder = BGEEmbedder()

    assert embedder.model_name == MODEL_NAME
    assert embedder.revision == MODEL_REVISION
    assert embedder._model is None  # still unloaded


def test_real_model_dimension_matches_expected():
    embedder = BGEEmbedder()

    assert embedder.dimension == EXPECTED_DIMENSION == 768


def test_real_model_query_and_passage_encoding_differ_for_identical_text():
    """The asymmetric-prefix requirement, verified against the real model:
    encoding the same string as a query vs. as a passage must not produce
    the same vector, because only the query path prepends BGE's
    "Represent this sentence for searching relevant passages: " prefix.
    """
    embedder = BGEEmbedder()
    text = "Acme Corp operating margin for Q3 FY2024"

    [passage_vector] = embedder.encode_passages([text])
    [query_vector] = embedder.encode_queries([text])

    assert passage_vector != query_vector
    assert len(passage_vector) == len(query_vector) == EXPECTED_DIMENSION


def test_real_model_relevant_passage_scores_higher_as_a_query_than_unrelated_one():
    """A weak end-to-end sanity check that the pinned checkpoint actually
    retrieves sensibly: a query embedded with the BGE prefix should be
    closer (cosine) to a genuinely relevant passage than to an unrelated one.
    """
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

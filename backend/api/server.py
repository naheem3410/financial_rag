"""HTTP layer over the C4 pipeline so the web UI has something to call.
It only passes questions in and shapes the answer that comes back.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date as date_type
from pathlib import Path
from typing import Any, Callable, Optional

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from config.schema import ExperimentConfig, load_config
from contracts.models import PlaceholderRegistryEntry
from infra.connections import BoxUnavailable, connect_mongo, connect_postgres, connect_redis
from infra.dictionary import ensure_dictionary_indexes, load_dictionary_into_redis, log_unmatched_phrase, lookup_metric, seed_default_dictionary
from infra.events import EventBus, LocalEventBus
from llm.routing import build_llm_client
from offline.documents.document_card import DEFAULT_DOCUMENT_TYPES, DocumentCardError
from offline.documents.file_store import FileStore, LocalFileStore
from offline.documents.policy_fact_store import PolicyFactStore
from offline.documents.policy_ingestor import check_policy_facts_stored, ingest_policy_document, remove_policy_facts
from offline.documents.upload_job import submit_upload
from offline.embedding.bge_embedder import BGEEmbedder
from offline.indexing.build import load_index
from offline.indexing.metadata_store_postgres import PostgresMetadataStore
from offline.indexing.repair import start_background_index_check
from offline.labeling.metric_labeler import DEFAULT_METRIC_ALIASES
from offline.registry.entities import build_registry
from online.pipeline_c4 import C4AnswerResult, answer_question
from online.retrieval.reranker import CrossEncoderReranker
from online.routing.how_it_happened import describe_how_it_happened

logger = logging.getLogger("haqqai.api")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = os.environ.get("HAQQAI_CONFIG", str(BACKEND_ROOT / "config/experiments/c4_full.yaml"))
DENSE_INDEX_PATH = os.environ.get("HAQQAI_DENSE_INDEX", str(BACKEND_ROOT / "data/index/dense_index_v2"))
POSTGRES_DSN = os.environ.get(
    "HAQQAI_POSTGRES_DSN",
    "postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2"
    "?keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=3",
)
MONGO_URI = os.environ.get("HAQQAI_MONGO_URI", "mongodb://localhost:27017")
REDIS_URL = os.environ.get("HAQQAI_REDIS_URL", "redis://localhost:6379")

# torch's default pool oversubscribes many-core laptops and made embedding about 3x slower
TORCH_THREADS = int(os.environ.get("HAQQAI_TORCH_THREADS", "8"))

_PLACEHOLDER_RE = re.compile(r"<ENT_\d+>")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AnswerValueOut(BaseModel):
    metric: str
    value: str
    unit: str


class CitationOut(BaseModel):
    proposition_id: str
    source_uri: str
    filing_type: str
    filing_date: str
    page_index: int  # zero-indexed, same convention as the index
    page_number: int  # what a PDF viewer shows
    evidence: str


class ClaimOut(BaseModel):
    claim_id: str
    claim_text: str
    is_numeric: bool
    verdict: str


class VerificationOut(BaseModel):
    claims: list[ClaimOut]
    verified_count: int
    total_count: int
    score: float


class PipelineOut(BaseModel):
    name: str
    sub_query_count: int
    fact_count: int
    latency_ms: int
    models: dict[str, str]


class StepOut(BaseModel):
    step_id: int
    question: str
    answer_text: str
    answerable: bool


class HowItHappenedOut(BaseModel):
    """Step 4.7: the road taken and why, in plain words, for the screen to
    show. `several_versions_note` is null unless the "several near-identical
    results" check (decision 11, online/curation/near_identical.py) actually
    found two genuinely different values for the same period."""

    road: str
    reason: str
    question_understood: str
    pieces: list[str]
    retry_attempted: bool
    retry_names_tried: list[str]
    several_versions_note: Optional[str]
    steps: list[StepOut]
    checked_against_original_question: bool
    summary: str


class AskResponse(BaseModel):
    question: str
    status: str  # "answered" or "abstained"
    answer_text: str
    abstention_message: str
    answers: list[AnswerValueOut]
    citations: list[CitationOut]
    verification: VerificationOut
    pipeline: PipelineOut
    how_it_happened: HowItHappenedOut


class DocumentUploadOut(BaseModel):
    doc_id: str
    status: str  # "accepted" | "skipped_already_there"


class DocumentEventOut(BaseModel):
    seq: int
    type: str
    message: str
    data: dict[str, Any]


class DocumentEventsOut(BaseModel):
    events: list[DocumentEventOut]
    done: bool  # an "indexed" or "failed" event has been seen
    failed: bool


@dataclass
class Runtime:
    config: ExperimentConfig
    embedder: BGEEmbedder
    reranker: CrossEncoderReranker
    bundle: Any
    registry: list[PlaceholderRegistryEntry]
    clients: dict[str, Any]
    models: dict[str, str]
    restore_names: Callable[[str], str]
    info: dict[str, Any]
    lock: threading.Lock
    # Step 2.2: None when MongoDB/Redis are unavailable at startup -- free-text
    # metrics are then simply off for this run, never a reason to refuse to answer.
    dictionary_lookup: Optional[Callable[[str], Optional[str]]] = None
    unmatched_logger: Optional[Callable[[str], None]] = None
    # Step 3.4: the upload screen's backing resources. None of this is used
    # by the question-answering path above; kept on the same Runtime only
    # because one process serves both.
    # The DSN documents_conn/documents_bus were actually opened against --
    # never read POSTGRES_DSN directly for a background thread's own
    # connection, or a test pointed at a different database would silently
    # leak work onto the real one instead.
    postgres_dsn: str = POSTGRES_DSN
    documents_conn: Optional[psycopg.Connection] = None
    file_store: Optional[FileStore] = None
    policy_fact_store: Optional[PolicyFactStore] = None
    documents_bus: Optional[EventBus] = None
    antecedent_llm_client: Optional[Any] = None

    def reconnect(self) -> None:
        """The store holds one connection; if the OS drops it while idle, open a fresh one."""
        try:
            self.bundle.metadata_store.close()
        except Exception:
            pass
        self.bundle = replace(self.bundle, metadata_store=PostgresMetadataStore(POSTGRES_DSN))

    def reconnect_documents(self) -> None:
        try:
            self.documents_conn.close()
        except Exception:
            pass
        self.documents_conn = connect_postgres(self.postgres_dsn, autocommit=True)
        self.documents_bus = LocalEventBus(self.documents_conn)


def make_name_restorer(registry: list[PlaceholderRegistryEntry]) -> Callable[[str], str]:
    longest: dict[str, str] = {}
    for entry in registry:
        if len(entry.surface_form) > len(longest.get(entry.placeholder, "")):
            longest[entry.placeholder] = entry.surface_form

    def restore(text: str) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: longest.get(m.group(0), m.group(0)), text)

    return restore


def build_response(
    result: C4AnswerResult,
    runtime_models: dict[str, str],
    restore: Callable[[str], str],
    get_evidence: Callable[[str], str],
    latency_ms: int,
) -> AskResponse:
    claims = [
        ClaimOut(
            claim_id=str(c["claim_id"]),
            claim_text=restore(str(c["claim_text"])),
            is_numeric=bool(c["is_numeric"]),
            verdict=str(c["verdict"]),
        )
        for c in result.verification_trace
    ]
    verified = sum(1 for c in claims if c.verdict == "match")
    total = len(claims)

    citations = [
        CitationOut(
            proposition_id=c["proposition_id"],
            source_uri=c["source_uri"],
            filing_type=str(c["filing_type"]),
            filing_date=str(c["filing_date"]),
            page_index=int(c["page"]),
            page_number=int(c["page"]) + 1,
            evidence=get_evidence(c["proposition_id"]),
        )
        for c in result.citations
    ]

    return AskResponse(
        question=result.question,
        status="abstained" if result.abstained else "answered",
        answer_text=restore(result.answer_text),
        abstention_message=restore(result.abstention_message) if result.abstained else "",
        answers=[AnswerValueOut(metric=a.metric, value=a.value, unit=a.unit) for a in result.answers],
        citations=citations,
        verification=VerificationOut(
            claims=claims,
            verified_count=verified,
            total_count=total,
            score=round(verified / total, 4) if total else 0.0,
        ),
        pipeline=PipelineOut(
            name="c4_full",
            sub_query_count=result.sub_query_count,
            fact_count=result.fff_count,
            latency_ms=latency_ms,
            models=runtime_models,
        ),
        how_it_happened=_build_how_it_happened(result),
    )


def _build_how_it_happened(result: C4AnswerResult) -> HowItHappenedOut:
    how = describe_how_it_happened(result)
    return HowItHappenedOut(
        road=how.road,
        reason=how.reason,
        question_understood=how.question_understood,
        pieces=list(how.pieces),
        retry_attempted=how.retry_attempted,
        retry_names_tried=list(how.retry_names_tried),
        several_versions_note=how.several_versions_note,
        steps=[
            StepOut(step_id=s["step_id"], question=s["question"], answer_text=s["answer_text"], answerable=s["answerable"])
            for s in how.steps
        ],
        checked_against_original_question=how.checked_against_original_question,
        summary=how.summary,
    )


def _load_runtime() -> Runtime:
    import torch

    torch.set_num_threads(TORCH_THREADS)
    config = load_config(CONFIG_PATH)
    embedder = BGEEmbedder()
    bundle = load_index(DENSE_INDEX_PATH, POSTGRES_DSN, dimension=embedder.dimension)
    # Fires and forgets: the app never waits on this. If a fact drifted out
    # of an index it heals itself in the background (step 1.3); only a huge
    # mismatch is left alone rather than silently patched.
    start_background_index_check(bundle, embedder, DENSE_INDEX_PATH, DENSE_INDEX_PATH + ".sparse.json", POSTGRES_DSN)

    propositions = bundle.metadata_store.all()
    # Step 2.1: the shared company list, kept current by ingestion itself --
    # no more scanning every proposition to rediscover companies/tickers.
    entities = bundle.metadata_store.entities()
    registry = build_registry(entities)
    dictionary_lookup, unmatched_logger = _load_metric_dictionary()

    sites = config.llm.call_sites
    models = {
        "query_splitter": sites["query_splitter"].model,
        "fff_rewriter": sites["fff_rewriter"].model,
        "generator": sites["generator"].model,
        "cove_verification": sites["cove_verification"].model,
    }
    clients = {
        "splitter": build_llm_client("query_splitter", config),
        "fff": build_llm_client("fff_rewriter", config),
        "generator": build_llm_client("generator", config),
        "claim_extraction": build_llm_client("cove_verification", config, schema_key="cove_claim_extraction"),
        "independent": build_llm_client("cove_verification", config, schema_key="cove_independent_verification"),
        "prose": build_llm_client("cove_verification", config, schema_key="cove_prose_comparison"),
        # Step 4.6: required call site, always configured (config/schema.py's
        # seven), so this is always real -- never the optional-fallback None.
        "step_planner": build_llm_client("step_planner", config),
    }

    info = {
        "pipeline": "c4_full",
        "companies": sorted(e.company for e in entities),
        "documents": sorted({p.metadata.source_uri for p in propositions}),
        "proposition_count": len(propositions),
        "models": models,
        "document_types": list(DEFAULT_DOCUMENT_TYPES),
    }
    reranker = CrossEncoderReranker()
    # both models load lazily; do it now so the first user question isn't the slow one
    reranker._load()
    embedder.encode_queries(["warm up"])

    documents_conn = connect_postgres(POSTGRES_DSN, autocommit=True)
    antecedent_llm_client = (
        build_llm_client("antecedent_extraction_fallback", config)
        if "antecedent_extraction_fallback" in config.llm.call_sites else None
    )

    return Runtime(
        config=config,
        embedder=embedder,
        reranker=reranker,
        bundle=bundle,
        registry=registry,
        clients=clients,
        models=models,
        restore_names=make_name_restorer(registry),
        info=info,
        lock=threading.Lock(),
        dictionary_lookup=dictionary_lookup,
        unmatched_logger=unmatched_logger,
        documents_conn=documents_conn,
        file_store=LocalFileStore(),
        policy_fact_store=PolicyFactStore(POSTGRES_DSN),
        documents_bus=LocalEventBus(documents_conn),
        antecedent_llm_client=antecedent_llm_client,
    )


def _load_metric_dictionary():
    """Step 2.2: MongoDB is the source, Redis the fast copy everything looks
    phrases up in. Either box missing just means free-text metrics are off
    for this run -- never a reason to refuse to start."""
    try:
        mongo_client = connect_mongo(MONGO_URI)
        redis_client = connect_redis(REDIS_URL)
        collection = mongo_client["haqqai"]["metric_dictionary"]
        unmatched_collection = mongo_client["haqqai"]["unmatched_phrases"]
        ensure_dictionary_indexes(collection)
        seed_default_dictionary(collection, DEFAULT_METRIC_ALIASES)
        load_dictionary_into_redis(collection, redis_client)
        return (
            lambda phrase: lookup_metric(redis_client, phrase),
            lambda phrase: log_unmatched_phrase(unmatched_collection, phrase),
        )
    except BoxUnavailable as exc:
        logger.warning("metric dictionary unavailable this run, free-text metrics are off: %s", exc)
        return None, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = _load_runtime()
    yield
    rt: Runtime = app.state.runtime
    rt.bundle.metadata_store.close()
    if rt.policy_fact_store is not None:
        rt.policy_fact_store.close()
    if rt.documents_conn is not None:
        rt.documents_conn.close()


app = FastAPI(title="HaqqAI API", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info")
def info() -> dict[str, Any]:
    return app.state.runtime.info


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    rt: Runtime = app.state.runtime
    question = req.question.strip()
    if len(question) < 3:
        raise HTTPException(status_code=422, detail="Question is too short.")

    def run() -> C4AnswerResult:
        return answer_question(
            question, rt.bundle, rt.embedder, rt.reranker,
            rt.clients["splitter"], rt.clients["fff"], rt.clients["generator"],
            rt.clients["claim_extraction"], rt.clients["independent"], rt.clients["prose"],
            rt.config, registry=rt.registry,
            fff_rewriter_model=rt.models["fff_rewriter"],
            generator_model=rt.models["generator"],
            query_splitter_model=rt.models["query_splitter"],
            cove_verification_model=rt.models["cove_verification"],
            dictionary_lookup=rt.dictionary_lookup,
            unmatched_logger=rt.unmatched_logger,
            step_planner_client=rt.clients["step_planner"],
        )

    try:
        # one request at a time: the models and rate limiter are shared state
        with rt.lock:
            started = time.monotonic()  # after the queue, so the time shown is real work
            try:
                result = run()
            except psycopg.OperationalError:
                logger.warning("metadata store connection dropped, reconnecting once")
                rt.reconnect()
                result = run()
    except Exception:
        logger.exception("pipeline failed for question %r", question)
        raise HTTPException(status_code=502, detail="The answer pipeline failed. Check the API logs.")

    latency_ms = int((time.monotonic() - started) * 1000)

    def get_evidence(proposition_id: str) -> str:
        try:
            return rt.bundle.metadata_store.get(proposition_id).text
        except KeyError:
            return ""

    return build_response(result, rt.models, rt.restore_names, get_evidence, latency_ms)


@app.post("/api/documents", response_model=DocumentUploadOut)
async def upload_document(
    ticket: str = Form(..., description="A code the browser makes up once per upload attempt; sending it again returns the same result instead of uploading twice."),
    title: str = Form(...),
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    product_area: Optional[str] = Form(None),
    audience: str = Form("everyone"),
    effective_date: Optional[str] = Form(None, description='"YYYY-MM-DD", or left out for "not sure" (uses the upload date)'),
    doc_version_label: Optional[str] = Form(None),
) -> DocumentUploadOut:
    rt: Runtime = app.state.runtime
    file_bytes = await file.read()
    doc_id = hashlib.sha256(ticket.encode("utf-8")).hexdigest()[:12]
    parsed_date = date_type.fromisoformat(effective_date) if effective_date else None

    card_kwargs = {
        "title": title, "doc_type": doc_type, "file_name": file.filename or "upload",
        "product_area": product_area, "audience": audience,
        "effective_date": parsed_date, "doc_version_label": doc_version_label,
    }

    def ingest_fn(card, data: bytes):
        return ingest_policy_document(card, data, rt.policy_fact_store, antecedent_llm_client=rt.antecedent_llm_client)

    def remove_fn(fact_ids):
        remove_policy_facts(rt.policy_fact_store, fact_ids)

    def check_stores_fn(fact_ids):
        return check_policy_facts_stored(rt.policy_fact_store, fact_ids)

    try:
        outcome = submit_upload(
            rt.documents_conn, rt.file_store, rt.documents_bus, ticket, doc_id, file_bytes, card_kwargs,
            ingest_fn, remove_fn=remove_fn, check_stores_fn=check_stores_fn, postgres_dsn=rt.postgres_dsn,
        )
    except DocumentCardError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except psycopg.OperationalError:
        logger.warning("documents store connection dropped, reconnecting once")
        rt.reconnect_documents()
        try:
            outcome = submit_upload(
                rt.documents_conn, rt.file_store, rt.documents_bus, ticket, doc_id, file_bytes, card_kwargs,
                ingest_fn, remove_fn=remove_fn, check_stores_fn=check_stores_fn, postgres_dsn=rt.postgres_dsn,
            )
        except psycopg.OperationalError:
            raise HTTPException(status_code=503, detail="Cannot reach the database right now. Try again shortly.")

    return DocumentUploadOut(doc_id=outcome.result["doc_id"], status=outcome.result["status"])


@app.get("/api/documents/{doc_id}/events", response_model=DocumentEventsOut)
def document_events(doc_id: str, after_seq: int = 0) -> DocumentEventsOut:
    """A poller the upload screen calls every second or two -- the simple
    default this session chose over a websocket/SSE connection; the shape
    (events since a sequence number) is the same either way, so switching
    the transport later would not change what this returns."""
    rt: Runtime = app.state.runtime
    try:
        raw_events = rt.documents_bus.events_since(doc_id, after_seq)
    except psycopg.OperationalError:
        rt.reconnect_documents()
        raw_events = rt.documents_bus.events_since(doc_id, after_seq)

    events = [DocumentEventOut(seq=seq, type=e.type, message=e.message, data=e.data) for seq, e in raw_events]
    types_seen = {e.type for e in events}
    return DocumentEventsOut(
        events=events,
        done="document.indexed" in types_seen or "document.failed" in types_seen or "document.skipped" in types_seen,
        failed="document.failed" in types_seen,
    )

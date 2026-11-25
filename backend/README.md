# Financial RAG — Noun-Phrase Collision Elimination + Chain-of-Verification

The aim of this study is to develop and evaluate a hallucination-aware financial RAG
framework that integrates Noun-Phrase Collision Elimination and Chain-of-Verification, in
order to measurably reduce hallucination and improve the reliability of RAG-based systems
for enterprise financial applications [13], [32]. The work is driven by the need to
address accuracy bottlenecks in high-stakes corporate settings, where current RAG
paradigms often generate ungrounded or contradictory responses even when given relevant
context.

Two techniques are layered on top of a hybrid retrieval baseline and evaluated
independently and in combination:

- **NPCE** (Noun-Phrase Collision Elimination) — splits a question that asks for several
  similar-sounding figures into separate, single-metric sub-queries so retrieval can't
  confuse them.
- **CoVe** (Chain-of-Verification) — independently re-checks every claim in a draft answer
  against the cited evidence before it's allowed out, using deterministic comparison for
  numbers.

### Simplified system architecture

```mermaid
flowchart LR
    subgraph Offline["Offline — build once"]
        DOCS[Financial filings] --> SEG[Segment into atomic facts]
        SEG --> IDX[(Dense + sparse + metadata index)]
    end

    subgraph Online["Online — per question"]
        Q[User question] --> SPLIT["NPCE: split into one-metric sub-questions"]
        SPLIT --> RET[Retrieve + rerank per sub-question]
        RET --> REWRITE[Rewrite into verbatim-guarded facts]
        REWRITE --> GEN[Generate draft answer]
        GEN --> VERIFY["CoVe: independently re-check every claim"]
        VERIFY --> OUT[Answer + citations, or abstain]
    end

    IDX -.-> RET
```

This README is a practical run guide. For the exact prompt used at every LLM call site,
see [`PROMPT_SPECIFICATIONS.md`](PROMPT_SPECIFICATIONS.md).

All backend code, tests, and data live under `backend/`; a `frontend/` directory sits
alongside it for the UI. Every command and relative path below (steps 2 onward) assumes
your shell's working directory is `backend/`, not the repo root.

---

## 1. Prerequisites

- Python 3.11+ (developed and tested on 3.14)
- Docker (for PostgreSQL)
- An OpenAI API key with access to `gpt-4.1-mini` (all five LLM call sites in the primary
  configs are pinned to `gpt-4.1-mini-2025-04-14`; one optional escalation path uses
  `gpt-5.6-luna`)
- ~2 GB free disk/RAM headroom the first time you run anything — `sentence-transformers`
  downloads and loads the embedding model (`BAAI/bge-base-en-v1.5`) and reranker
  (`BAAI/bge-reranker-base`) from HuggingFace on first use

## 2. Install

```bash
git clone <this-repo-url>
cd financial_rag/backend
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## 3. Start PostgreSQL and create the databases

The metadata store is real Postgres, not a stub. On a fresh machine, start it (and Mongo
and Redis, reserved for later work, see `docker-compose.yml`) with:

```bash
docker compose up -d
```

Every box in that file is set to restart on its own if it stops or the computer reboots
(`restart: unless-stopped`) — also turn on "start Docker Desktop at sign-in" in Docker
Desktop's settings, since a box can only restart itself if Docker itself is running. If
you already have a hand-created `financial_rag_postgres` container from before this file
existed, `docker compose up` will refuse to touch it (name clash) rather than risk your
data; instead just run `docker update --restart unless-stopped financial_rag_postgres`
once, and keep using it as before.

If a box does not come up within about 10 seconds, every part of the app that talks to it
gives you a plain message naming which one (see `infra/connections.py`) instead of hanging.

Then create the three databases the project uses (tables inside each are created
automatically on first connect — no migrations to run):

```bash
python -c "
import psycopg
conn = psycopg.connect('postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag', autocommit=True)
for db in ('financial_rag_v2', 'financial_rag_c0', 'financial_rag_test'):
    conn.execute(f'CREATE DATABASE {db} OWNER financial_rag')
print('done')
"
```

(`financial_rag_v2` holds the shared proposition index C1–C4 use; `financial_rag_c0` holds
C0's separate fixed-size-chunk index; `financial_rag_test` is only for the test suite.)

## 4. Set your API key

```bash
# Windows (PowerShell): $env:OPENAI_API_KEY = "sk-..."
export OPENAI_API_KEY="sk-..."
```

`ANTHROPIC_API_KEY` is only needed if you point a call site at `provider: anthropic` in a
config — none of the shipped configs do this by default.

## 5. Get the corpus

`data/pdfs/` is **not** included in this repository (see `.gitignore`) — FinanceBench's
underlying SEC filings aren't ours to redistribute. You need to obtain the filings
yourself and place them at `data/pdfs/<doc_name>.pdf`.

The full corpus is FinanceBench (Islam et al., [arXiv:2311.11944](https://arxiv.org/abs/2311.11944)),
84 documents. For the primary evaluation set used throughout this project, you only need
these 5 (all public SEC 10-K/10-Q filings, obtainable from SEC EDGAR or wherever you
source FinanceBench's filing set):

```
3M_2018_10K.pdf
3M_2022_10K.pdf
3M_2023Q2_10Q.pdf
ACTIVISIONBLIZZARD_2019_10K.pdf
ADOBE_2016_10K.pdf
```

This exact list is also committed at `data/metric_axis_required_docs.txt` and is checked
programmatically (`offline/corpus.py:assert_matches_required_docs`) before any indexing
run proceeds — if a filename doesn't match exactly, the build stops rather than silently
indexing the wrong document.

## 6. Sanity check: run the test suite

```bash
pytest -q
```

Expect **561 passed, 3 skipped**, no errors, in a couple of minutes — the suite uses fake
LLM clients almost everywhere (no API cost), but the Postgres-backed tests need the real
container from step 3 to be running. If Postgres isn't up, you'll see
`psycopg.errors.ConnectionTimeout` on the DB-dependent tests specifically
(`test_full_build.py`, `test_index_build.py`, `test_metadata_filter.py`,
`test_metadata_store_postgres.py`) — everything else still passes.

## 7. Quick smoke test (cheap, ~15–20 min, real API calls)

Before committing to the full corpus, it's worth proving the whole offline→online path
works end to end on just one document. This indexes `3M_2023Q2_10Q` (92 pages) into
`financial_rag_v2` and asks it one question:

```python
from offline.embedding.bge_embedder import BGEEmbedder
from offline.full_build import run_full_build
from offline.indexing.build import load_index
from config.schema import load_config

config = load_config("config/experiments/c1_baseline.yaml")
embedder = BGEEmbedder()

manifest = run_full_build(
    doc_names=["3M_2023Q2_10Q"],
    config=config,
    pdf_paths={"3M_2023Q2_10Q": "data/pdfs/3M_2023Q2_10Q.pdf"},
    company_names={"3M_2023Q2_10Q": "3M Company"},
    checkpoint_path="data/smoke_checkpoint.json",
    max_spend_usd=3.0,
    embedder=embedder,
    dense_index_path="data/index/dense_index_v2",
    postgres_dsn="postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2",
)
print(manifest.index_build.proposition_count, "propositions indexed, $", manifest.total_cost_usd)
```

This is real spend against your OpenAI account (roughly $0.50–0.60 for this one document)
and is checkpointed — if it's interrupted, re-running the same command resumes rather than
re-billing completed batches.

Then ask it something (see step 9 for the full pattern across all five cells).

## 8. Full build: index the 5-document evaluation corpus

Once you've confirmed step 7 works, index all 5 documents the same way — call
`run_full_build` with all 5 `doc_names`/`pdf_paths`/`company_names` instead of just one.
Two builds are needed:

- **C1–C4's shared index** (`financial_rag_v2`, proposition granularity) — this must be
  built exactly **once** and reused unchanged across all four cells; rebuilding it between
  cells would re-invoke the LLM segmenter and could silently produce a different
  proposition set per cell, confounding the whole comparison.
- **C0's separate index** (`financial_rag_c0`, fixed-size chunking) — built with
  `offline/chunking/fixed_size_chunker.py` + `offline/indexing/build.py:build_index`
  instead of `run_full_build`, since C0 is a naive baseline with no LLM-based segmentation
  step. See `config/experiments/c0_naive.yaml` for its settings.

Budget real time and real money for this: indexing the full corpus involves several
hundred real LLM segmentation calls. Expect on the order of a few dollars and up to a few
hours depending on your OpenAI rate-limit tier and network conditions — `max_workers` on
`run_full_build` controls concurrency, and the whole process is checkpointed per document
and per batch, so a crash or a sleeping laptop doesn't lose completed work.

## 9. Ask a question

Once an index exists, you can query any of the five cells directly. Each cell is a
function in `online/pipeline_c{0,1,2,3,4}.py` — they share the same
`bundle`/`embedder`/`reranker` but differ in which LLM clients they need
(`llm/routing.py:build_llm_client`, using the matching `config/experiments/c*.yaml`).

```python
from config.schema import load_config
from offline.embedding.bge_embedder import BGEEmbedder
from offline.indexing.build import load_index
from online.retrieval.reranker import CrossEncoderReranker
from llm.routing import build_llm_client
from online.pipeline_c1 import answer_question

embedder = BGEEmbedder()
bundle = load_index(
    dense_index_path="data/index/dense_index_v2",
    postgres_dsn="postgresql://financial_rag:financial_rag_dev@localhost:5432/financial_rag_v2",
    dimension=embedder.dimension,
)
reranker = CrossEncoderReranker()
config = load_config("config/experiments/c1_baseline.yaml")

result = answer_question(
    "What were 3M's accounts payable and accounts receivable for FY2018? Report both figures separately. (in USD millions)",
    bundle, embedder, reranker,
    build_llm_client("fff_rewriter", config), build_llm_client("generator", config),
    config, fff_rewriter_model=config.llm.call_sites["fff_rewriter"].model,
    generator_model=config.llm.call_sites["generator"].model,
)
print(result.answer_text, result.citations)
```

C2/C3/C4 need one or two more real LLM clients built the same way (CoVe's three
sub-calls, and/or the query splitter) — see each pipeline module's `answer_question`
signature for the exact client list, or `online/pipeline_c4.py` for the full set. C0 uses
`online/pipeline_c0.py` against the separate `financial_rag_c0` index instead.

## 10. Reproduce the evaluation results

The gold-key evaluation set is `evaluation/data/financebench_collision_metric_axis.jsonl`
(37 questions derived from FinanceBench's own gold evidence). Each cell has a matching gate
script, `evaluation/run_c{0,1,2,3,4}_gate.py`, which runs every question through that cell's
real pipeline and scores it by value against the gold key. For k=3 majority-vote
self-consistency, use `evaluation/self_consistency.py:run_self_consistency` wrapping the
same gate's `run_c*_question_once` function. These are also real-money, checkpointed,
potentially long-running operations — read the module docstrings before running one against
the full set.

## 11. Known limitations (stated plainly)

- The corpus used throughout is **PDF-only**. Path B (structured XBRL/CSV/XLSX ingestion)
  and the OCR tier are implemented and unit-tested but **never exercised on this corpus**
  — don't read passing tests there as validated production behavior on real structured
  filings.
- Primary results are reported on 37 questions over 5 documents (3 companies) — promising,
  not yet confirmed at the scale of the full 84-document FinanceBench corpus.

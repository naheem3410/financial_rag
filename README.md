# HaqqAI — Financial RAG with Noun-Phrase Collision Elimination and Chain-of-Verification

This is a question-answering system for financial filings (10-Ks, 10-Qs). You ask it a
question about a company's numbers and it answers only from the actual indexed filings,
with a citation back to the page it came from. If the filings don't support an answer,
it says so instead of guessing.

It started as a Master's project looking at a specific, narrow failure mode in RAG
systems for financial documents: when a question mentions two similar things at once —
two companies, two reporting periods, two near-identical line items in the same
filing — a standard retrieval system tends to blend facts from both of them together.
A model can end up reporting Boeing's revenue under Lockheed Martin's name just because
both companies showed up in the same batch of retrieved text. The project calls this a
"noun-phrase collision," and most of the work here is about getting rid of it rather
than hoping the model sorts it out on its own.

## What it actually does differently

Two main ideas, on top of a fairly ordinary RAG pipeline:

**Noun-Phrase Collision Elimination (NPCE).** A multi-part question gets split into
separate, single-entity sub-questions before anything is retrieved. Then, before any of
those sub-questions reaches a language model, every real company name in it gets
replaced with a neutral placeholder token. The result is that no model call in the
whole pipeline is ever shown two real, similarly-named companies at the same time — it's
not asked to be careful, it's structurally prevented from mixing them up.

**Chain-of-Verification (CoVe).** Once a draft answer is produced, every factual claim
in it gets pulled out and independently re-derived from the retrieved evidence, with no
memory of what the draft said. Numeric claims are compared by exact code-level equality,
not another LLM's opinion. If any claim in the answer doesn't check out, the whole
answer is withheld rather than handed over half-right. "I don't know" is treated as a
correct, useful answer here, not a failure.

## How it's put together

- Backend: Python, FastAPI, a proposition-level fact store in Postgres, FAISS for dense
  retrieval, a cross-encoder reranker, and OpenAI models for the language-model steps.
- Frontend: Next.js. One tab to ask a question and see the full trace of how it was
  answered — the verification check, the citation, the exact evidence quote. A second
  tab holds the full evaluation results: every test question, every setup's answer,
  side by side.
- Three systems get compared against each other throughout this project: a plain LLM
  with no retrieval at all, a naive RAG baseline (dense search, no reranking, no
  verification), and the full system described above.

## How it's evaluated

366 real questions, built on top of the FinanceBench benchmark, split into three
categories depending on what kind of collision they set up: two companies named
together, two reporting periods named together, or two near-identical metrics in one
filing. Every setup runs over the same questions, scored on precision, recall, coverage,
and how often it's wrong while still sounding confident — plus a second, independent
check from an LLM judge (RAGAS) on whether the answers stay faithful to their evidence.

The honest result: the full system clearly wins on the exact problem it was built for —
questions naming two companies at once, where its win is large enough that it holds up
statistically, not just as a lucky sample. It also answers more questions overall
without giving up. It's not better at everything, though — on plain, no-trick single-fact
lookups, the simpler naive RAG setup is still slightly ahead, and that's reported as-is
rather than smoothed over.

## Status

Working end to end — API, web UI, a real indexed set of filings, and a completed
evaluation run across all three setups.

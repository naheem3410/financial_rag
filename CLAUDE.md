# CLAUDE.md — Standing Instructions

Project: **Financial RAG with Noun-Phrase Collision Elimination + Chain-of-Verification.**
Full specification: `ARCHITECTURE.md`. Read it before implementing anything. This file
restates the constraints that must hold in **every** commit.

---

## 1. The one rule that governs everything

> **An LLM is used only where linguistic generation or semantic judgment is required.
> Every numeric, structural, and provenance operation is deterministic code.**

The thesis of this project is a *faithfulness guarantee*. If an LLM can touch a digit,
write a citation, or decide whether two numbers are equal, the guarantee is only as
strong as the model's sampling that day. Do not weaken this for convenience.

**There are exactly six LLM call sites** (`ARCHITECTURE.md` §11). If you find yourself
adding a seventh, stop and ask.

1. Proposition Segmenter (Path A only)
2. Numeric Extractor — qualifier resolution only, never emits digits
3. Query Splitter — slot parsing only, the split itself is deterministic
4. FFF Rewriter — guarded by numeric-verbatim validation
5. Generator
6. CoVe — question extraction, independent execution, prose comparison only

---

## 2. Never do these

- ❌ **Never** implement numeric comparison as an LLM call. `18.2 == 18.2` is code. (§6.12.4)
- ❌ **Never** let an LLM write or format a citation. Citations are looked up from
  `proposition_id` metadata. (§6.15)
- ❌ **Never** disable `numeric_verbatim` or `deterministic_numeric_comparison` in config.
- ❌ **Never** send an LLM call on Path B (XBRL/CSV/XLSX). Structured input is templated. (§4.4)
- ❌ **Never** include the draft `D` in a CoVe verification prompt. Independence is
  structural, not a prompt instruction. (§6.12.3)
- ❌ **Never** promote a `DET` component to `LLM` because a task "sounds linguistic."
  Check the engine table in §3 first.
- ❌ **Never** put the placeholder map in a global. It is per-request state. (§5.4)
- ❌ **Never** change `ARCHITECTURE.md` §5.1 (Proposition record). It is a frozen contract;
  CoVe's numeric comparison depends on it.

---

## 3. Invariants that must always hold

| Invariant | Where |
|---|---|
| Dense index, sparse index, and metadata store contain **identical** `proposition_id` sets | §2.1 |
| §5.1 proposition schema is unchanged | §5.1 |
| Source propositions survive until FFF rewrite time (the numeric guard needs them) | §8.3 |
| `evidence_page_num` is **zero-indexed** — match it everywhere | §8A.1 |
| All experiment cells C1–C4 share an **identical retrieval core** | §9.2 |
| Every LLM call: `temperature=0`, fixed seed, pinned model version, schema-validated | §7.1 |

---

## 4. Build order — do not skip ahead

**Phase 0** — `contracts/`, `config/`, and all eleven acceptance tests from §10.5 written
as **failing tests**. Stop and report.
**Phase 1** — Offline plane. Path B first (deterministic, easiest to verify), then Path A.
**Phase 2** — Retrieval core. Validate with Hit Rate / MRR / NDCG before layering anything on.
**Phase 3** — C1 baseline, end to end. First demoable checkpoint.
**Phase 4** — NPCE modules (C3), then CoVe (C2), then C4.
**Phase 5** — Evaluation harness and ablation runner.

Tests before implementation. Guarantees are enforced from the first commit, not retrofitted.

---

## 5. Acceptance tests (§10.5) — these encode the thesis

Four of these are load-bearing. If any fails, the faithfulness claim is unsupported no
matter what the RAGAS numbers say:

- `test_numeric_comparison_deterministic` — numeric comparison issues **zero** LLM calls
- `test_citation_no_llm` — citation binding issues **zero** LLM calls
- `test_cove_independence` — draft `D` absent from verification prompts
- `test_numeric_verbatim_guard` — FFF with an unsourced digit is rejected

The other seven: `test_parser_plain_text`, `test_path_b_no_llm`, `test_placeholder_roundtrip`,
`test_npce_off_passthrough`, `test_cove_off_passthrough`, `test_three_index_invariant`,
`test_abstention_on_missing_evidence`.

---

## 6. Experiment configuration

Five cells. Toggling a cell swaps the implementation bound to `QueryTransformer` and
`Verifier` (§10.2). **No other code changes between cells.**

| Cell | `enable_npce` | `enable_cove` | Notes |
|---|---|---|---|
| C0 | — | — | Naive RAG: fixed-size chunks, dense-only, no rerank. Outside the factorial. |
| C1 | false | false | Baseline: hybrid + metadata filter + rerank |
| C2 | false | true | |
| C3 | true | false | |
| C4 | true | true | Full framework |

C0 is **not** C1. Keep them distinct.

---

## 7. Data notes

- Corpus: FinanceBench (Islam et al., arXiv:2311.11944). Index only the **84 documents**
  referenced by the question set, not all 362.
- Corpus is **PDF only**. Path B and the OCR tier will be built and tested but
  **unexercised on this corpus** — report as such, never as validated capability.
- `evaluation/data/financebench_collision.jsonl` is **pre-annotation**: metric-axis and
  temporal-axis gold answers are `null` until the human verification pass completes.
  Do not treat nulls as ground truth or attempt to fill them with a model.
- Primary results run on the **extraction-only** stratum. The system has no calculation
  module (Program-of-Thoughts is deferred, §13.1), so arithmetic questions will correctly
  abstain.

---

## 8. When something is ambiguous

Prefer, in order: (1) what `ARCHITECTURE.md` states explicitly, (2) the deterministic
option, (3) ask. Do not resolve ambiguity by adding an LLM call.

Anything labelled **[implementation choice]** in `ARCHITECTURE.md` is genuinely yours to
pick — swap it behind its interface in `contracts/interfaces.py`. Anything not so labelled
is prescribed by the source papers.

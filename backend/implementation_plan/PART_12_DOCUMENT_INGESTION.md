# Master Technical Architecture and Implementation Plan
## Part 12 — Document Ingestion

Implements `ENTERPRISE_PLATFORM_ARCHITECTURE.md` §12.1 (Path A/B, genuinely exercised) and §12.4
(ingestion hardening). Builds on Part 1 §2.32–§2.34 and Part 6's object chain.

---

## Pipeline

```
Upload
   │
   ▼
[1] File validation ──────── MIME-type check against an allowlist per document category;
   │                         reject on mismatch between declared and actual content type
   │                         (magic-byte sniffing, not filename extension alone)
   ▼
[2] Malware scanning ──────── Malware Scanning (Part 1 §2.33) — every file, before parsing
   │
   ├── infected/ambiguous ──▶ [1a] Quarantine (isolated storage, never reachable by the
   │                               parsing pipeline; retained per security-incident policy,
   │                               flagged to Security Operations, Part 17)
   │
   ▼ (clean)
[3] Extraction ─────────────── Layout/Table-Aware Parser (Path A, deterministic) or Schema
   │                         Mapper (Path B, deterministic) — format-driven, content-agnostic
   ▼
[4] OCR (conditional) ──────── OCR (Part 1 §2.34) — only if the document has no extractable
   │                         text layer; emits mandatory ocr_confidence per proposition
   ▼
[5] Classification ─────────── Assigns one of the nine classification levels (architecture
   │                         §12.2) — set explicitly at upload time by the uploading user's
   │                         declared intent, not inferred from content, and validated against
   │                         the uploader's own authorization to assign that level
   ▼
[6] Chunking / Proposition extraction ── Proposition Segmenter (Path A, LLM Call Site 1) or
   │                                    Row-to-Proposition Templating (Path B, zero LLM calls)
   ▼
[7] Embedding ────────────────── The one shared Embedder (architecture §4.9 of the inherited
   │                            research contracts)
   ▼
[8] Indexing ─────────────────── Index Writers — dense + sparse + metadata, one logical write,
                                three-index invariant verified post-write (Part 6 §6)
```

---

## 1. File Limits

| Control | Rule |
|---|---|
| Max file size | Configurable per tenant, defaulting conservatively (e.g. 100 MB); enforced at upload, before the file is fully received where the transport supports streaming size checks |
| Max archive expansion (if archives are accepted at all) | A hard ceiling on decompressed size and file count, to prevent a zip-bomb-style resource exhaustion attack — archives are rejected by default unless a tenant explicitly enables them for a specific ingestion path |
| Max pages (Path A) | Configurable; an oversized document is rejected with an explicit message, never silently truncated and partially indexed |

## 2. MIME Validation

Declared content type is checked against a per-category allowlist (PDF, DOCX, XLSX, CSV, common
image formats for OCR, XBRL). The actual file content is sniffed (magic bytes) and must match the
declared type — a mismatch is treated as a validation failure, not a best-effort attempt to parse
anyway.

## 3. Macro Handling

Office documents (DOCX/XLSX) containing macros are either **rejected outright** (default posture)
or have macros **stripped before any further processing**, per tenant configuration — macros are
never executed under any circumstance, since the ingestion pipeline has no legitimate reason to
run arbitrary code embedded in an uploaded document.

## 4. Malformed Document Handling

A document that fails to parse cleanly at step [3] (corrupted PDF structure, unreadable XLSX) is
**rejected with a specific error**, logged, and never partially indexed — there is no "best effort"
parsing path that silently drops the unparseable sections and indexes the rest without flagging
it.

## 5. Prompt Injection at Ingestion

Content extracted at step [3] is treated as **data, not instructions**, from the moment it enters
the pipeline (architecture §8.4) — this applies at ingestion just as it applies at retrieval time.
A document containing text engineered to look like a system instruction (e.g. "IMPORTANT: ignore
previous instructions and...") is segmented into propositions like any other text; it carries no
special authority when later retrieved and handed to the Generator or an agentic tool, because the
Context Builder (Part 1 §2.10) and the FFF Rewriter's guarded structure never grant retrieved
content an instruction-level role in the prompt.

## 6. Duplicate Detection

`documents.content_hash` (Part 5 §4) is computed at upload, before parsing. A hash match against
an existing document (within the same tenant) is treated as either:
- **An exact duplicate** — the new upload is deduped, no reprocessing occurs, the existing
  document's record is returned to the uploader.
- **A new version of an existing logical document** (same source, different content) — determined
  by matching on the document's own identifying metadata (filename pattern, source system
  reference) rather than content hash, since a genuinely revised policy document will have a
  different hash by definition; this creates a new `document_versions` row (§7 below) rather than
  a wholly new `documents` row.

## 7. Versioning

A new version of an existing document creates a `document_versions` row (Part 5 §4) with its own
`effective_date`; the previously-current version's `superseded_by` is set to point at the new one.
**Both versions' propositions remain indexed** — retrieval-time filtering (Part 6 §5) is what
ensures a query resolves to the correct version, not deletion of the old one, since historical
queries (compliance, "what was the policy in 2023") require the old version to still exist.

## 8. Deletion

Deleting a document (subject to retention policy and legal-hold check, architecture §12.5/§12.6)
cascades to: all `document_versions`, all `propositions` extracted from any of those versions
(removed from all three indexes — dense, sparse, metadata — in the same transactional operation
that the three-index invariant check verifies, Part 6 §6), and any cache entry keyed to that
document's version. This mirrors the conversation-deletion cascade rule (Part 7 §6) applied to the
document domain.

---

*End of Part 12. Part 13 (PII Architecture) follows immediately.*

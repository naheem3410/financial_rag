# Prompt Specifications — The Six LLM Call Sites

Specifies every LLM call in the system: input payload, output schema, system prompt,
validation, and failure behaviour.

**Global rules, applied to all six:**

1. **Structured input.** Typed JSON payload. Never concatenated prose, never a flattened
   evidence blob.
2. **Structured output.** Declared JSON schema, validated on receipt.
3. **`reasoning` field first.** Every output schema opens with a free-text `reasoning`
   string. This preserves reasoning quality under constrained decoding. It is **logged for
   the audit trace and never parsed for facts.**
4. **Determinism.** `temperature=0`, fixed seed, pinned model version, recorded in the run
   manifest.
5. **Failure policy.** Schema-invalid or guard-failing output → bounded retry (max 2) →
   deterministic fallback. **The fallback always drops the item; it never invents one.**
6. **`schema_version`** on every payload. Do not silently alter a schema.

---

## Call Site 1 — Proposition Segmenter

Offline, Path A only. Splits parsed prose into atomic, self-contained propositions
(DenseX granularity).

**System prompt**
```
You split financial document text into atomic propositions.

An atomic proposition is a single self-contained factual statement that concerns
exactly one entity, one metric, and one period where those apply. It must be
understandable without surrounding context: restate the subject explicitly rather
than using pronouns or references like "the company" or "that year".

RULES
- Copy every number, date, and identifier EXACTLY as it appears in the source.
  Never round, reformat, recompute, or infer a numeric value.
- Do not add facts. If the source does not state something, it does not appear.
- Do not merge two facts into one proposition.
- Narrative text with no financial figures still yields propositions; segment it
  the same way.
- If a sentence contains multiple line items or multiple periods, emit one
  proposition per item/period combination.
```

**Input**
```json
{ "schema_version": "1.0",
  "source_span": "string — parsed text, one section or table",
  "document_context": { "company": "3M", "filing_type": "10-K", "fiscal_year": 2018,
                        "section": "Consolidated Statement of Cash Flows", "page": 34 } }
```

**Output**
```json
{ "reasoning": "string — logged, never parsed for facts",
  "propositions": [ { "text": "string", "source_offset_hint": "string|null" } ] }
```

**Validation (deterministic, post-call)**
- Every numeric token in each proposition must appear verbatim in `source_span`. Any
  proposition failing this is **dropped**, not repaired.
- Empty `propositions` on non-empty input → retry once, then log and skip the span.

---

## Call Site 2 — Numeric Qualifier Resolution

Offline. **Tier 3 only** — invoked solely when XBRL tags (tier 1) and rules/regex
(tier 2) have both failed to resolve a qualifier. The most safety-critical call.

**System prompt**
```
You resolve ambiguous temporal and unit qualifiers in financial statements.

You are given a proposition and its document context. Your only task is to
CLASSIFY what an ambiguous qualifier refers to — for example, which fiscal year
"the quarter" or "the prior period" denotes, or whether figures are in thousands,
millions, or billions.

ABSOLUTE RULE
You never output a numeric value. You never restate, round, or recompute a figure.
You return only classifications drawn from the allowed enumerations below. If the
context does not determine the answer, return null. Guessing is a failure; null
is a correct answer.
```

**Input**
```json
{ "schema_version": "1.0",
  "proposition_text": "string",
  "ambiguous_qualifier": "string — e.g. 'the prior period'",
  "document_context": { "company": "3M", "filing_type": "10-K", "fiscal_year": 2018,
                        "filing_date": "2019-02-07", "section": "string" } }
```

**Output**
```json
{ "reasoning": "string",
  "fiscal_year": 2017,
  "quarter": null,
  "basis": "reported",
  "scale": "millions",
  "currency": "USD",
  "resolvable": true }
```
Enumerations — `basis`: `reported | ttm | restated | null`; `scale`: `unit | thousands |
millions | billions | null`; `quarter`: `1 | 2 | 3 | 4 | null`.

**Validation**
- **Reject any output containing a digit outside the enumerated fields.** Hard failure.
- `resolvable: false` → the proposition is stored with `numeric: null`. This is correct
  behaviour, not an error.
- Set `confidence.extraction_tier = "llm_qualifier"` so downstream components know this
  record was inferred rather than tagged or matched.

---

## Call Site 3 — Query Slot Parsing

Online. Parses natural language into slots. **The split itself is a Cartesian product in
Python** — the model never decides how many sub-queries to emit.

**System prompt**
```
You parse a financial question into structured slots.

Extract every entity (company), every metric, and every period the question refers
to. List each one separately, even when the question phrases them together — "operating
and net margin" is two metrics; "Q3 and Q4" is two periods.

Use the canonical metric identifiers from the provided lexicon. If a metric in the
question is not in the lexicon, return its surface form in `unmapped_metrics` rather
than forcing a match.

Do not answer the question. Do not decompose it. Do not add entities, metrics, or
periods that the question does not mention.
```

**Input**
```json
{ "schema_version": "1.0",
  "query": "How did Acme Corp's operating margin and net margin compare between Q3 and Q4 FY2024?",
  "metric_lexicon": ["operating_margin", "net_profit_margin", "gross_margin", "..."] }
```

**Output**
```json
{ "reasoning": "string",
  "entities": ["Acme Corp"],
  "metrics": ["operating_margin", "net_profit_margin"],
  "unmapped_metrics": [],
  "periods": [ { "fiscal_year": 2024, "quarter": 3 }, { "fiscal_year": 2024, "quarter": 4 } ],
  "filing_types": [] }
```

**Downstream (deterministic)** — sub-queries are `entities × metrics × periods`. Here
1 × 2 × 2 = **4**. Completeness of the split is provable by construction, which is the
whole reason the LLM does not perform it.

**Validation**
- Empty `entities` or `metrics` → fall back to treating the query as a single sub-query
  (NPCE-off behaviour) and log.
- `unmapped_metrics` non-empty → log for lexicon expansion; proceed with what mapped.

---

## Call Site 4 — FFF Rewriter

Online. Rewrites retrieved propositions into Fully-Formatted Facts. **Guarded.**

**System prompt**
```
You rewrite retrieved financial passages into Fully-Formatted Facts.

A Fully-Formatted Fact is one atomic statement that:
  - concerns exactly one entity, one metric, and one period
  - restates its subject noun explicitly (never "it", "the company", "that year")
  - carries its value exactly as written in the source

RULES
- Copy every number, unit, currency, and date EXACTLY. Never round, convert scale,
  recompute, or normalise a figure.
- Emit only facts concerning the requested entity. Discard everything about any
  other entity, even if it appears in the same passage. This is the point of the
  task: preventing two similar entities from reaching the model together.
- Do not infer. If the passage does not state a value, do not produce a fact for it.
- If no proposition concerns the requested entity, return an empty list. An empty
  list is a correct answer.
```

**Input**
```json
{ "schema_version": "1.0",
  "sub_query": { "company": "<ENT_1>", "metric": "operating_margin", "period": "Q3 FY2024" },
  "propositions": [ { "proposition_id": "p_9931", "text": "string",
                      "source_ref": { "filing_type": "10-Q", "filing_date": "2024-11-05",
                                      "page": 34 } } ] }
```

**Output**
```json
{ "reasoning": "string",
  "facts": [ { "proposition_id": "p_9931", "subject": "<ENT_1>", "metric": "operating_margin",
               "period_label": "Q3 FY2024", "value_text": "18.2 percent" } ] }
```

**Validation — the numeric-verbatim guard (mandatory, deterministic)**
1. Every numeric token in `value_text` must appear **verbatim** in the source proposition
   identified by `proposition_id`.
2. `subject` must equal the sub-query's placeholder token exactly.
3. `proposition_id` must be one of the supplied propositions.
4. **`period_label` must be verbatim to the source proposition** (see below).

Any fact failing any check is **rejected**. Retry up to twice, then drop the fact. Without
this guard the rewriter is a silent hallucination vector upstream of everything else.

`source_ref` is **not** taken from model output — it is joined deterministically from
proposition metadata.

**Period-verbatim guard.** `period_label` must match the source proposition's own stated
period, not just what the question asked for. Checked by
`online/curation/numeric_guard.py:validate_period_verbatim`: both sides are parsed to a
canonical `(fiscal_year, quarter)` tuple via `contracts.numeric.parse_fiscal_period` — the
same normalization CoVe's comparator uses in Step 4, so both agree by construction — and
compared as tuples. Rejects only on a confirmed disagreement: if either side doesn't parse
to a recognizable period, the guard passes rather than guessing. Deterministic, zero LLM
calls, same as every other guard at this call site.

---

## Call Site 5 — Generator

Online, one call per sub-query. Answers from paired FFFs only.

**System prompt**
```
You answer a single financial question using only the facts provided.

Every fact you have concerns exactly one entity, one metric, and one period. The
question concerns the same one. There is nothing to disambiguate and nothing to
combine across entities.

RULES
- Use only the supplied facts. Do not use prior knowledge about this company or
  any other.
- Reproduce values exactly as they appear in the facts.
- Do not compute, derive, convert, or annualise anything. If the question requires
  arithmetic the facts do not already contain, set `answerable: false`.
- If the facts do not contain the answer, set `answerable: false` and leave
  `answer_value` null. This is correct behaviour, not failure.
- Cite the fff_id of every fact you used.
```

**Input**
```json
{ "schema_version": "1.0",
  "sub_query": { "company": "<ENT_1>", "metric": "operating_margin", "period": "Q3 FY2024" },
  "facts": [ { "fff_id": "f_001", "subject": "<ENT_1>", "metric": "operating_margin",
               "period_label": "Q3 FY2024", "value_text": "18.2 percent" } ] }
```

**Output**
```json
{ "reasoning": "string",
  "answerable": true,
  "answer_value": "18.2",
  "answer_unit": "percent",
  "answer_text": "<ENT_1> operating margin for Q3 FY2024 was 18.2 percent.",
  "used_fff_ids": ["f_001"] }
```

**Validation**
- `answer_value` must appear verbatim in at least one supplied fact.
- `used_fff_ids` must all exist in `facts`; empty while `answerable: true` → reject.
- `answerable: false` propagates to the Abstention Gate.

---

## Call Site 6 — Chain-of-Verification

Three sub-calls. **The numeric comparison in step 4 is NOT among them** — it is
deterministic code.

### 6a — Claim extraction

**System prompt**
```
You extract every atomic factual claim from a draft financial answer.

An atomic claim is a single assertion of one value, for one entity, one metric,
one period. Split compound sentences into separate claims.

Extract claims exactly as the draft states them. Do not evaluate, correct, or
comment on whether they are right — that happens later and by a different process.
```

**Input** — `{ "schema_version": "1.0", "draft": "string" }`

**Output**
```json
{ "reasoning": "string",
  "claims": [ { "claim_id": "c_1", "claim_text": "string", "entity": "<ENT_1>",
                "metric": "operating_margin", "period_label": "Q3 FY2024",
                "asserted_value": "18.2", "asserted_unit": "percent",
                "is_numeric": true } ] }
```

Verification questions are generated **deterministically** from these fields by template:
`"What was {entity} {metric} for {period_label}?"`

### 6b — Independent execution

> **Structural requirement: the draft `D` must be absent from this payload.** The
> orchestrator constructs the prompt without it. This is enforced in code and asserted by
> `test_cove_independence` — it is not a prompt instruction the model could ignore.
>
> **Enforcement detail.** The function that performs this call —
> `execute_independent_verification(question, evidence)` in
> `online/verification/cove_orchestrator.py` — takes **no `draft` parameter at
> all**. Independence is a signature-level guarantee, not merely an omitted
> payload field: there is no argument through which a later change could
> thread the draft back in "for context" without that change being visible as
> a signature edit, and without `test_cove_independence` failing. Do not add
> one.

**System prompt**
```
You answer a factual question using only the evidence provided.

You have no other context. Do not speculate about what the expected answer might be.

If the evidence supports an answer, give it exactly as the evidence states it.
If the evidence does not support an answer, set supported to false. "Not supported"
is a correct and useful answer — it is how unsupported claims get caught.
```

**Input**
```json
{ "schema_version": "1.0",
  "verification_question": "What was <ENT_1> operating margin for Q3 FY2024?",
  "evidence": [ { "fff_id": "f_001", "subject": "<ENT_1>", "metric": "operating_margin",
                  "period_label": "Q3 FY2024", "value_text": "18.2 percent" } ] }
```

**Output**
```json
{ "reasoning": "string",
  "supported": true,
  "answer_value": "18.2",
  "answer_unit": "percent",
  "supporting_fff_ids": ["f_001"] }
```

### 6c — Prose claim comparison (non-numeric only)

Invoked **only** when `is_numeric == false`. Numeric claims go to the deterministic
comparator.

**System prompt**
```
You compare two non-numeric statements and judge whether they assert the same thing.

Return "match" only if they assert the same fact. Return "conflict" if they assert
different facts. Return "unsupported" if the verification result asserts nothing.

Differences of wording, order, or verbosity are not conflicts. Differences of
substance are.
```

**Output** — `{ "reasoning": "string", "verdict": "match | conflict | unsupported" }`

### Step 4 — Revise: the deterministic path

**Not an LLM call.** For every claim where `is_numeric == true`, compare the two
independently-produced structured records — value compared numerically
(`normalize_value`, never raw string equality), the rest by canonicalized equality
(below):

```python
def numeric_match(claim, verification) -> bool:
    return (normalize_value(claim.asserted_value) == normalize_value(verification.answer_value)
            and units_match(claim.asserted_unit, claim.scale, verification.answer_unit, verification.scale)
            and periods_match(claim.period_label, verification.period_label)
            and canonical_scale(claim.asserted_unit, claim.scale) == canonical_scale(verification.answer_unit, verification.scale))
```

Never ask a model whether 18.2% equals 18.2%. Delegating a deterministic equality check
reintroduces exactly the unreliability this system exists to remove.

**Period/unit canonicalization (`online/verification/numeric_comparator.py`).** Claim
extraction (6a) and independent verification (6b) are two independent LLM calls with no
shared vocabulary constraint, so both sides are canonicalized before comparison rather than
compared as raw strings:
- `periods_match` parses both sides' `period_label` to a canonical `(fiscal_year, quarter)`
  tuple (`"FY2018"`, `"fiscal year 2018"`, and `"year ended December 31, 2018"` all parse to
  `(2018, None)`) and compares tuples, falling back to raw string equality when either side
  is unparseable (e.g. a bare "quarter ended <date>" with no explicit `Q#` — inferring the
  quarter from a date requires knowing a company's fiscal-year-end convention, which cannot
  be assumed safely, so it is left unparsed rather than guessed). Two genuinely different
  fiscal years never collapse together — `FY2022` vs `FY2023` still conflicts every time,
  however phrased.
- `units_match`/`canonical_scale` treat a field that merely restates a scale word (e.g.
  `unit="millions"`) as "no real unit stated" rather than a genuine value — a side that
  states no real unit does not conflict by omission, but two sides that both state real,
  different units (`"USD"` vs `"EUR"`) still conflict. Currency synonyms (`"$"`/`"dollars"`/
  `"USD"`) fold to one token, and a compound phrase combining both in one string (e.g.
  `"millions of dollars"`) is decomposed into `(scale_word, "USD")` before the normal
  unit/scale canonicalization runs.
- `strip_scale_word` (`contracts/numeric.py`) strips an embedded scale word out of a raw
  value string (`"$1,848 million"` → `"1,848"`) before `normalize_value` runs, since
  `normalize_value` doesn't fold scale words by design and won't parse one left in.

None of this touches `numeric_match`'s own equality logic, only the normalization of its
inputs — it never weakens detection of a genuinely different value, period, scale, or
currency.

**Outcomes:** match → keep; conflict → drop, flag in trace; unsupported → drop, route to
Abstention Gate.

**Note on what this comparator cannot fix.** All the normalization above assumes the
evidence (the FFF) itself is faithful — it only ever compares what the claim asserts against
what the evidence says. If the evidence is already mislabeled — exactly what Call Site 4's
period-verbatim guard exists to catch — independent verification will correctly confirm the
claim against that mislabeled evidence — this comparator has no channel back to the true
source proposition text, only to the FFF. The fix for that failure mode lives upstream, at
Call Site 4, not here.

---

## Summary

| # | Call site | Guard | On failure |
|---|---|---|---|
| 1 | Proposition Segmenter | Numeric verbatim vs source span | Drop proposition |
| 2 | Numeric Qualifier Resolution | **Reject any digit in output** | `numeric: null` |
| 3 | Query Slot Parsing | Non-empty entities + metrics | Single sub-query fallback |
| 4 | FFF Rewriter | **Numeric verbatim vs proposition** | Drop fact |
| 5 | Generator | Value verbatim in facts | `answerable: false` |
| 6a | Claim extraction | Schema | Retry, then abstain |
| 6b | Independent execution | **Draft absent from payload** | `supported: false` |
| 6c | Prose comparison | Non-numeric claims only | Treat as unsupported |
| — | **Numeric comparison** | **Deterministic — no LLM** | n/a |

Every failure path drops information. **No failure path invents it.**

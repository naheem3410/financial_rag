# Issues and open decisions

This file is kept up to date. Add to it when something is found, decided or fixed, and record the
change in the log at the bottom. Last updated: 2026-09-20.

---

## Master's deliverables: where things stand

| Phase | Status | Notes |
|---|---|---|
| 1. Benchmark extension | Done | 366 verified items (37 original plus 329 new) over 15 filings. The new items split into 89 temporal, 96 entity and 144 metric. Four independent checking rounds, two real bugs found and fixed. |
| 2. Pipeline extension and re-evaluation | Not started | See the to do list below. |
| 3. Enterprise architecture design | Done | Architecture document, the 25 part implementation plan and the system design PDF. |
| 4. Prototype and evaluation | Partly done | API and web UI work end to end on the full C4 pipeline. Multi tenant access control, the audit service, RAGAS scoring and the penetration style test are not built. |

Thesis text for objectives, scope, methodology and the evaluation plan is drafted. The results
chapter waits on Phase 2.

### Phase 2 to do list

- [ ] Index the 10 filings that are not indexed yet. The 366 items use 15 filings and only 5 are
  indexed, so today's index can answer 82 of the 366 (24 temporal, 58 metric, no entity items).
  To index: Adobe 2022, AES 2022, American Water Works 2020, Best Buy 2019, Boeing 2022, Costco 2021,
  General Mills 2020 and 2022, Lockheed Martin 2020, PepsiCo 2021. Real model calls, roughly a few
  dollars and up to a few hours. Measured 2026-09-20 on a 7 page sample of one filing: 271 facts, 5
  model requests, about 7 minutes running one batch at a time, and one batch fell back to the
  stronger model. The full run needs several workers (`max_workers`) or it will take many hours.
- [ ] Build the plain LLM baseline (no retrieval). DECIDED 2026-09-20. The naive RAG baseline (C0)
  already exists.
- [ ] Define what "expansion" means in the Query Router, then build C4+ as one configuration.
- [ ] Adapt the evaluation harness to run all 366 items and report results by collision axis.
- [ ] Run the three way comparison: Extended System, plain LLM, naive RAG. Pilot of about 20 items
  first, then the full run.
- [ ] Write the results chapter.

---

## Open decisions

1. **Plain LLM baseline.** DECIDED 2026-09-20: yes, build it. Not built. A small setup that sends the
   question straight to the model with no retrieval, no facts and no citations. It uses the same
   settings style as the other cells, so the model can be swapped, and it must fit the same test harness
   so all setups run on the same 366 items and report by collision type. Because it has no sources, an
   answer cannot be checked against evidence, so it is scored against the gold answers only.
2. **Size of the live run.** DECIDED 2026-09-20: option A, a small test first. About 20 items spread
   across the three collision types, run through the setups to check that everything works, and to
   measure the cost and time. Then the full 366. At one to two minutes a question, the full C4 run alone
   is about 9 hours as things stand, which is why the small test comes first. Not run yet.
   Notes: pick the 20 by a fixed rule (an equal share of temporal, entity and metric items, and include
   items that need the missing filings only after those are indexed), write the list down, and keep the
   same 20 for every setup so the results can be compared.
3. **Reuse the stored vectors** (issue 2 below). DECIDED 2026-09-20, see 13. Makes the run practical,
   but touches the retrieval core shared by every cell, so scores must be shown to be unchanged.
4. **Fix the repeated unit refusals** (issue 1 below). DECIDED 2026-09-20: option A, a small rule that
   drops a unit that already ends the value ("2,266 million" plus "million" stays "2,266 million"). Not
   built. It could shift the C3 and C4 numbers already reported, so after the fix re-run the gates and
   record what moved, and keep the old numbers alongside the new ones in the thesis notes.
   Notes: put the rule where the draft is put together (`assemble_draft`), keep it deterministic with no
   model, add a test with the repeated unit case, and do not weaken the checker itself.
5. **What "expansion" means in the Query Router.** DECIDED 2026-09-20 through the router decisions (11):
   expansion is the "try again with other names" road, after a weak search. The text below is the
   older discussion. Only the reworded question set is still open. Options at the time: (1) normalise wording,
   such as aliases, "net sales", "fiscal 2018"; (2) fill in missing parts, such as "last year" or
   "both companies"; (3) rewrite the query with a model to retrieve better. Recommendation: use
   1 plus the safe half of 2, built as a rule based first pass (clear single question goes
   straight to retrieval, several companies, metrics or periods are decomposed by code, wording it
   cannot parse falls back to the model splitter), and skip 3. Verification stays on for every
   question. The benchmark is cleanly worded, so expansion would show a speed gain but almost no
   accuracy gain on it. Still to decide: agree with this direction, and whether to add a small
   set (30 to 40) of reworded versions of existing items to show an accuracy benefit.
6. **Generate more benchmark items?** DECIDED 2026-09-20: option B, stay with the 366 items for now.
   There is still room for about 72 more entity and 59 more metric items, so more can be added later if
   a reviewer asks or if the results show a gap. New non finance questions (the 40 to 60 test set for
   the paper shelf) are a separate job and are still planned.
7. **Calculation module.** Deferred until the core deliverables are done.
8. **Candidate retrieval differs between C1/C2 and C3/C4** (issue 7 below). DECIDED 2026-09-20: choice B.
   CHANGED 2026-09-20 (owner): the Master's compares only three setups: plain AI, naive RAG (C0) and
   C4+. C1, C2, C3 and the two ablations are not run. Old configs and numbers stay as history.
9. **How the question splitter gets its metric vocabulary.** DECIDED 2026-09-20: (1) pass unmapped
   metric phrases through as free text, so every question goes through NPCE, and (2) build a bigger
   alias dictionary from several sources, not only XBRL. Stored in MongoDB and loaded into Redis at
   startup (decided 2026-09-20). Not built yet, see the decision under Q1 below.
10. **Where the company registry is built** (issue 9 below). Recommended: build it during ingestion,
    store it in Postgres, and have the API read it. DECIDED 2026-09-20, not built. One shared function
    for the API, the loading code and the tests.
11. **Router design.** DECIDED 2026-09-20, not built. Order, unsure rule, step by step now, and a "how it
    happened" section are all agreed, see "Q3 follow up" and its decisions. The step planner as a new
    model job (the seventh) was approved by the owner on 2026-09-20.
12. **Filter by metric too.** DECIDED 2026-09-20, not built. Needs a metric label on every fact (new and
    already stored), a change to the frozen fact record, a fallback when the filter leaves too little,
    and results shown with the metric filter and the splitting each switched on and off.
13. **Reuse stored vectors.** DECIDED 2026-09-20, not built. Needs a lookup of a vector by fact id, an
    error when a vector is missing, and an old versus new comparison of scores and answers first.
14. **Keyword index not at startup.** DECIDED 2026-09-20, not built: build it at ingestion and save it
    (option A under Q6).
15. **Add to existing indexes.** DECIDED 2026-09-20, not built: uploads must add to the database, the
    vector index and the saved keyword index together, as one job, with rules for repeats, new versions
    and deletes. See the notes under Q6.
16. **The database keeps stopping** (issue 3). DECIDED 2026-09-20, not built: the container restarts by
    itself, and every program gives up after about 10 seconds with a clear message.

Still open after the 10 questions of 2026-09-20 (small):
- Whether to add 30 to 40 reworded versions of existing finance questions, to show an accuracy benefit
  from the router and "try again" road (the old part of decision 5).
- Whether all the new work (router, paper split, fix the question, try again, step by step, metric
  filter, stored vectors, entities table, upload path, plain AI, naive RAG and C4+ only, unit fix, database
  fixes) fits the Master's deadline. The final date is December 1, 2026, about 72 days from 2026-09-20.
  The owner believes there may be time to build much more, even the whole platform. The Master's scope
  (`masters_deliverables/MASTERS_PROJECT_DELIVERABLES.md`) is a prototype of one module core plus
  governance, not the whole platform, and the thesis needs writing time. To be planned honestly.
- When to index the 10 missing filings, and with how many workers, since the small test and the full run
  both need them for most items.
- When to start building, and in what order. Suggested order for the router work is in "Q3 follow up".

---

## Known issues

### 1. The single figure example is flaky

**What happens.** The question "What was 3M's accounts payable for FY2018? (in USD millions)"
declined to answer in about 3 of 6 recent runs. The rest verified correctly (2,266 million, page 58).

**Why.** The generator sometimes returns the unit twice, for example value "2,266 million"
with unit "million". The draft then reads "2,266 million million", and the verification step
correctly flags it as a conflict and abstains. The safety net works as designed, but it makes
a poor first demo.

**Where.** `online/generation/assembler.py` (`assemble_draft` joins value and unit with a space)
and the generator output in `online/output/generator.py`.

**The catch.** Normalising units in the pipeline changes research behaviour, so it could move
the C3 and C4 numbers reported in `Findings_and_Conclusion.md`. Any fix needs a re-run of the
gate scripts to confirm what changed.

**DECIDED 2026-09-20 (owner): do the first option below. Not built yet.**

**Options.**
- Strip a unit that already ends the value, inside `assemble_draft` (small, deterministic).
- Tighten the generator schema or prompt so value holds the number only.
- Leave the pipeline alone and pick more stable demo questions.

**Already mitigated in the UI.** `frontend/src/components/AnswerView.tsx` hides a duplicated
unit in the value chips, but the abstention itself still happens upstream.

### 2. Answers are slow (about 50 seconds to 2 minutes)

**What happens.** A verified answer takes roughly 50 seconds for one metric and about 2 minutes
for two metrics.

**Why.** `online/retrieval/hybrid.py` re-embeds every candidate fact on every question
(`embedder.encode_passages([...])`) instead of reading the vectors already stored in the dense
index. A 3M FY2018 question has 897 candidates, and each sub-question repeats the work.

**Already done.** Torch's default thread pool oversubscribed this 28 thread laptop and made
embedding about 3x slower (0.237 vs 0.069 seconds per fact). `api/server.py` now caps it at 8
threads (`HAQQAI_TORCH_THREADS`), which took one question from 155 s to 47 s. It also loads the
reranker and embedder at startup so the first question is not the slow one.

**DECIDED 2026-09-20: do this fix (option 1 under Q5). Not built yet.**

**The real fix.** Use the stored vectors from `PersistentDenseIndex` for the metadata filtered
candidates instead of re-encoding them. That should bring a question down to a few seconds
apart from the model calls. It touches the shared retrieval core (all cells C1 to C4 must keep an
identical retrieval core, see `CLAUDE.md`), so it needs a deliberate decision and a check that
scores are unchanged. Also worth confirming that cosine scores from stored vectors match the
freshly encoded ones.

### 3. The database keeps stopping

The Postgres container (`financial_rag_postgres`) has stopped on its own twice when Docker Desktop
restarted. It recovers with all 21,913 propositions intact. On 2026-09-20 Docker Desktop itself was
off, so the API could not start and any script that opens the database hangs with no timeout.
Fix: start Docker Desktop, then `docker start financial_rag_postgres`.

**DECIDED 2026-09-20 (owner): do both. Not built.**
- A. Set the container to start again by itself whenever Docker starts (a restart policy, for example
  "unless stopped"). It can be set on the existing container without recreating it, so no data is
  touched. Docker Desktop itself must also be set to start when the computer starts, or this does not help.
- B. Make every program that opens the database give up after about 10 seconds with a clear message,
  such as "the database is not running, start Docker and the database container". This covers the
  loading code, the API startup and the test setup, so nothing hangs quietly again. The API's database
  address already has keep alive settings but no connect time limit.

### 4. The question splitter and entity registry are not general purpose

The splitter only recognises a fixed list of 19 metrics, and the company registry is built by hand
per corpus. If none of a question's metrics are on the list, the split quietly falls back to a
single lookup. Checked 2026-09-20: all 10 metrics used by the 366 benchmark items are inside the
list, so this does not distort the evaluation. It does limit use beyond the benchmark.

### 5. The metadata filter does not use the metric

DECIDED 2026-09-20: add a metric filter. See the decision after Q4 below.

Found 2026-09-20 by running `filter_candidates` on a real sub-question. It filters on company,
fiscal year, quarter and the first filing type only. The metric is ignored, so a question about
one figure still pulls every fact for that company and year: 897 candidates for 3M FY2018, which
is what the slow answers in issue 2 come from. Narrowing by metric before embedding would be
another way to cut the time, but like reusing stored vectors it changes the shared retrieval core.

Two related behaviours worth knowing:
- If the entity is not a placeholder and no year is given (the no NPCE case), no filter applies
  and all 21,913 facts come back.
- A company that is not in the index gives an empty list, which is why the answer is a refusal.

Also found: the old query analyzer's metric list (margins, revenue, EPS, GAAP) does not include
the benchmark's metrics, so its collision detection would not fire on accounts payable versus
accounts receivable. It would need extending before a router can use it.

### 6. The API handles one question at a time

DECIDED 2026-09-20 (owner): option A, leave it for now. It is fine for testing alone and for the funding
demo. Revisit two steps later: cancel a question when the person leaves the page, and running several
questions together, which belongs with the multi tenant platform work. Not changed. Add it to the list
when the platform layer starts.

An abandoned browser request keeps running on the server until it finishes, and the next request
waits behind it.

### 7. C1/C2 and C3/C4 find their candidate facts in different ways

Found 2026-09-20 while answering the storage questions below.

- C1 and C2 (NPCE off) call `retrieve_fused` in `evaluation/run_retrieval_gate.py`. It searches the
  persisted FAISS index and the keyword index across the whole corpus and takes the top 20 of each.
  There is no company or year filter, because with no slots parsed there is nothing to filter on.
- C3 and C4 (NPCE on) call `filter_candidates` then `hybrid.retrieve`. They filter in Postgres by
  company, year and filing type, re-embed every remaining fact, and build a fresh keyword index over
  just those facts, so keyword statistics come from the filtered set and not the whole corpus.
- Ranking (RRF, time weighting, rerank) is shared. Candidate generation is not.

**Why it matters.** The reported NPCE gain mixes two effects: splitting the question, and getting a
hard company and year filter as a side effect of parsing the slots. A wrong year lookalike cannot
appear at all once the year filter is applied. A reviewer can fairly ask which effect did the work.
The config comment for C1 also says "metadata filter", which is not true of the code.

**Options.** Align the cells so all four use the same candidate generation; or keep them and add
two ablations, "filter but no split" and "split but no filter", to attribute the gain. The second
is the more informative result for the thesis.

**CHANGED 2026-09-20 (owner): only three setups are tested: plain AI, naive RAG (C0) and C4+.
C1, C2, C3 and the two ablations are not run, so this issue is closed by decision, not by a fix.**
Consequences to state in the results chapter: the gain of C4+ cannot be split into splitting, filter and
check, and C0 (whole corpus) and C4+ (filter first) do not share a candidate step. The earlier
decision below is kept as history.

**Earlier decision (superseded): keep the cells and add the two ablations.** Notes for the build:
- The two new setups sit beside C0 to C4 and are named plainly in the results, for example
  "split, no filter" and "filter, no split".
- Now that the filter will also use the metric (decision 12), "filter" has three parts: company and
  year, and the metric. Decide whether the ablation switches them on and off together or one at a time.
  Suggested: together first, then the metric alone, because the metric filter is the new part.
- The per question steps must let the filter and the splitting each be switched on and off in settings,
  with no other code differences between setups.
- Run them on the same 366 items and report by collision type.
- More runs, but the stored vector change (decision 13) is what makes that affordable.

### 8. The persisted vector and keyword indexes are not used by C3, C4 or the API

`PersistentDenseIndex` (FAISS `IndexFlatIP`, exact search) and the startup keyword index are only
read by C0, C1, C2 and the retrieval gate. C3, C4 and the API ignore them and recompute vectors from
Postgres text on every question. This is the source of the slow answers in issue 2. See the answers
below for the options.

### 9. The company registry is built in two places and not during ingestion

`offline/registry/placeholder_builder.build_registry` is never called by `run_full_build` or anything
else in the pipeline. `api/server.py` has its own `build_registry` that reads the indexed company
names at startup. Two implementations, and no stored source of truth. See the answers below.

---

## Design questions and answers (2026-09-20)

**Q1. Is the 19 metric list in the question splitter sustainable? Do we need every financial term in
a fast store such as MongoDB or a knowledge graph, or a model?**

Not as a gate. Today the list is handed to the model, which maps the question's wording onto those
ids. If none map, `metrics` is empty and NPCE falls back to one lookup. The list is not needed for
retrieval (the metadata filter ignores the metric). It only names the sub questions and stops the
model inventing ids.

| Option | Good | Bad |
|---|---|---|
| Keep a short list, pass unmapped phrases through as free text | Tiny change, deterministic, no new system, NPCE works for any metric | No canonical id or alias for unknown terms |
| Alias table from the XBRL taxonomy (US GAAP and IFRS labels) plus statement line items | Authoritative, free, versioned, covers "net sales" = revenue | Needs loading and upkeep, company specific labels still miss |
| Model does open extraction, with a check that each phrase appears in the question | Handles anything | Cost, latency, nondeterminism. The check stops inventions, not mislabelling |
| Embeddings match a phrase to the nearest concept with a threshold | Catches unseen synonyms | The confusable pairs (payable and receivable) are the nearest neighbours, so it cannot be the only judge |

Recommendation: the first two now, embeddings only as a suggestion queue (log unmapped phrases,
review, add the alias). Storage: a table in Postgres or a file in the repo is enough, a few thousand
short strings, read only. MongoDB adds a second system for a lookup Postgres already does. A
knowledge graph pays off for relationships (for example total current assets is the sum of its
parts), which is what the deferred calculation module needs, not collision detection.

**Q1 in plain words (the explanation given to the owner).**

What we want: when someone asks a money question, break it into small simple questions so similar
things are not mixed up. "What were 3M's accounts payable and accounts receivable?" becomes two
questions. To do that the system must spot the money words. Today it only knows 19 of them. If a
question uses a word that is not on the list, the system gives up on splitting and asks one big
question. That is fine for our test questions and not fine for real ones.

Four ways to fix it:
1. Let unknown words through exactly as the person wrote them. Tiny change, nothing new needed, and
   splitting works for any word. Downside: we do not learn that "net sales" and "revenue" mean the same.
2. Build a bigger dictionary from an official source that also lists other names for each word.
   Trustworthy, but it needs setting up and tidying, and a company's odd wording can still slip through.
3. Let the AI read freely and pick out the words. Handles anything, but costs more, is slower and can
   answer differently on different days.
4. Match words by meaning. Catches new words, but it is easily fooled: "payable" and "receivable" look
   almost the same to it, and they are the two things most needed apart. Not to be trusted alone.

Suggested: 1 and 2 now, and 4 only as a helper that says "new word seen, please check" so a person
adds it to the dictionary. A plain table or file is enough to hold the dictionary. A knowledge graph
is only worth it later for the maths feature that is on hold.

**Decision (owner, 2026-09-20).** Go with option 1 and option 2.
- Option 1 means every question passes through NPCE. Unmapped metric phrases become free text sub
  questions instead of causing a fallback to one lookup. The routing work now in development is
  expected to cover the remaining cases.
- Option 2: use more sources than XBRL so coverage is wide.
- Storage (owner, 2026-09-20): MongoDB holds the dictionary as the source of truth. Each entry keeps the
  canonical word, its other names, where it came from, a licence note and who reviewed it. When the API
  starts, the dictionary is loaded into Redis for fast look ups. Notes:
  - This adds two new things to run, MongoDB and Redis, on top of Postgres. Boxes have stopped by
    themselves before, so start all three from one compose file with restart policies, and make every
    program give up after about 10 seconds with a clear message (decision 16).
  - If Redis is empty or down at look up time, fall back to the copy already in memory or to MongoDB. If
    both are down, say so clearly and quickly.
  - When a new alias is approved, refresh Redis without a restart.
  - Keep it all behind one look up function, so the rest of the code does not care where the words live.
  - Libraries to add: `pymongo` and `redis` for Python. Check they install on Python 3.14.
  - Tests use a fake look up function, plus a few tests against real containers.

**Notes and next steps (not started).**
- Benchmark results are unaffected by option 1: all 10 metrics used by the 366 items are already on
  the list, so no benchmark question changes behaviour.
- Option 1 still falls back to one lookup when a question has no company or no metric phrase at all.
  That case is left for the router.
- Change needed: in `LLMQuerySplitter.transform`, use the metrics and the unmapped metrics together,
  keeping only phrases that appear word for word in the question.
- Candidate sources for the dictionary, to be confirmed for licence and quality before use: the US GAAP
  and IFRS taxonomies, the row labels found in the filings already ingested (real company wording),
  official glossaries from accounting bodies, banking and regulatory terms relevant to Nigerian
  institutions (for example central bank reporting terms), the metric fields in our own benchmark,
  and phrases logged from real questions that did not match.
- (Superseded on 2026-09-20 by the storage decision above.) Redis versus a table: for a few thousand
  short read only entries either works. Redis helps if
  several API copies must share one dictionary that can be updated without a restart. It adds a
  second system to run and to persist. Decide when the routing design is settled.

**Q2. Can this work for non finance collisions? Break the query into words, drop stop words, compare
embeddings, treat close ones as a collision, hand to the model for individual facts, then combine.**

Mostly sound, with changes.
- Compare noun phrases, not single words. "Operating margin" and "net margin" differ only in a
  modifier and share a head word, which single word comparison loses. Stop word removal plus noun
  phrase chunking is fine.
- Embedding closeness is a collision risk signal, the reverse of its usual use.
- There is no universal threshold. Similarity scores from this embedding model are compressed high
  and depend on the domain. Calibrate on labelled pairs. The benchmark gives finance labels, a
  general domain needs its own labelled set.
- Collisions happen in two places: similar phrases in the question, and near identical passages in
  the retrieved evidence that differ in one slot (the crowding failure).
- The model only labels slots, with a check that each phrase appears in the question. Code builds
  the combinations and retrieves per sub question.
- Combining stays deterministic when the answers are independent lookups (lists, one line per
  figure). It stops being deterministic for comparison ("which is larger"), aggregation, and multi hop
  questions where the second question depends on the first answer. Those need the calculation module
  or a verified model step.
- Honest limit: this is untested outside finance. A small general benchmark is needed to claim it.

**Q2 follow up: using NPCE for non finance questions (owner asked for thoughts and a solution).**

Goal in plain words: someone asks something that is not about money, such as "What are the fees for
a savings account and a current account?", and the system answers each item separately so the
answers do not get mixed up, the same as for money questions.

Thoughts:
1. The idea is sound. Mixing up two similar things is not special to finance.
2. Finance worked because of two helpers besides splitting: a word list (being loosened) and a hard
   filter by company and year that throws away lookalike facts. For other topics the filter needs
   something to filter on, such as policy version, effective date, product or department. Those must be
   attached to the documents when they are loaded. Without them, splitting alone helps less.
3. Most collisions in a question are visible from structure: "A and B", "A or B", "between A and B",
   "both", "each". Meaning closeness is a backup for the sneaky ones.
4. Splitting can keep the person's own sentence and swap in one item at a time, so no finance shaped
   template is needed.
5. Combining is easy when each answer stands alone. A comparison, a total, or an answer that needs an
   earlier answer needs a model to write it, and a checker afterwards.
6. The guarantee is weaker outside numbers. Money figures are checked exactly by code. For wording, a
   model judges whether two sentences agree, so the promise is weaker and should be stated that way.
7. Splitting too much is costly, because each piece runs the whole pipeline. Split only when confident
   and cap the number of pieces.

Proposed solution (not started):
1. Find the groups: the existing model call, with a general instruction and no word list, lists groups
   of lookalike items. Code checks that every phrase appears word for word in the question.
2. Build the pieces: swap in one item at a time, combine across groups, and cap the count.
3. Metadata for general documents: agree fields at load time (document type, version, effective date,
   audience, product) and use them as a filter when the question names one.
4. Check the evidence: if the top passages say almost the same thing and differ in one detail (a date,
   version or number), answer for each with labels or ask which one is meant.
5. Combine and check: labelled list for simple cases. For compare or total, a model writes from the
   pieces only and a checker verifies it.
6. Meaning closeness later, once real examples exist to set the threshold.
7. Test set: 40 to 60 non finance questions with the correct split, plus some that should not split.
   Measure both wrongly split and wrongly not split.

Not recommended as the first step: dropping stop words and comparing single words by meaning. It is the
weakest signal and needs a threshold we cannot set yet.

Alternative: a grammar parser tool (such as spaCy) finds "A and B" structure cheaply and the same way
every time. It adds a dependency and may be weaker on Nigerian English or Pidgin, so start with the
model plus checks and add a parser later if cost or consistency matters.

**Decision (owner, 2026-09-20).** Go with steps 1 to 5 and step 7 above. Step 6 (meaning closeness)
waits until real examples exist.
- Step 1 uses a cheap model that can be swapped later, for example for Ollama. The system already
  picks the model for each job from settings, so swapping is a settings change and not a code change.
  This gives the existing question splitter job a general instruction, so it is not a new model job.
  Check quality on the test set before switching to a local model.
- Limit on the number of pieces: proposed 6, adjustable in settings.
- All topics from the start: HR policies, product guides, IT help and others.
- Test set: 40 to 60 non finance questions with the correct split, plus some that must not split.
  Suggested: include a few written the way Nigerian users write.

**Router question, and the answer.** Question: does the query router decide finance or not finance, or
does NPCE decide after routing? Answer: neither one guesses from the wording. The router only decides
"split or not". "Finance or general" comes from where the document lives. Every uploaded document
carries a kind (financial filing or general document), set at upload, and a question is asked against
one collection. The kind tells the splitter which filter to use: company and year for filings; version,
date, audience and product for general documents. Same rule as before: the mode comes from settings,
never from guessing at the wording.

**Minimum document card at upload (draft, for the upload form).** Only what is needed to tell
lookalike documents apart. Person fills in:

| Field | Required | Why it is needed |
|---|---|---|
| Title | Yes | Names the source in the answer, and groups versions of the same document |
| Type (policy, procedure, product guide, FAQ, form or notice, other) | Yes, pick one | Labels the source and lets the search prefer the right kind |
| Product or area (for example savings account, leave, VPN, or "general") | Yes, pick one or more | Lets the filter drop papers about other things |
| Audience (for example everyone, customers, all staff, contract staff, branch staff, IT team) | Yes, default "everyone" | The same question has different answers for different people |
| Effective date | Yes, "not sure" allowed and then the upload date is used | Decides which version is the newest |
| Version (free text such as "v3" or "2023 edition") | No | Helps tell two versions apart. If empty, the date does the job |

The computer fills in: document id, file name, upload date, who uploaded, kind, and the page of each fact.

Not asked, because it is not needed for this purpose: department or owner, language, tags, expiry,
description, and confidentiality (that belongs to access control, a separate feature).

Rules and storage:
- Keep one row per document (a "document card") and let each fact point to it. Editing the card then
  does not mean reloading the facts. Existing financial filings keep their current fields (company,
  year, filing type), which act as their card.
- If two documents share a title, product and audience, the one with the later effective date is
  "current". Search prefers current unless the question names an older version or year. Old ones stay
  searchable, for questions like "what was the policy in 2021".
- The pick lists for Audience and Product come from each organisation's own settings, so the form shows
  their own words. The Type list is fixed.
- Not started. Needs the upload form fields, a documents table, and the filter change.

**Q3. What about query expansion and rewriting? Does NPCE already mean query decomposition?**

Not quite. NPCE is decomposition triggered by a collision, and its sub questions are independent and
can run in parallel. General decomposition (multi hop) is sequential and different. Expansion adds
terms to improve recall. Rewriting resolves ambiguity. The router can choose from cheap signals:

| Signal from a first pass | Action |
|---|---|
| Two or more values in a slot, or similar phrases | Decompose (NPCE) |
| Zero or few candidates, or a big drop in rerank scores | Expand (aliases) and retry |
| Missing or relative slot ("last year", "it", no company) | Rewrite from context, or ask the user |
| None of the above | Direct retrieval |

Counts and scores decide, not a model classifier. Keep the original question for verification, so a
rewrite can never change what the checker compares against.

**Q4. Why does Postgres keep the passage text? Is it not in the embedding store? Is that repeating
storage?**

The vector index holds only vectors and ids, it cannot return text. So the text has to live somewhere,
and that is the `data` JSON column in Postgres (the whole fact: text, numbers, metadata). There is no
copy of the text in the vector store. Small real redundancies: the filter columns (company, year,
page and so on) also appear inside the JSON, and the ids repeat in every store on purpose.

**Decision after Q4 (owner, 2026-09-20): filter by metric as well.** The filter will use the metric in
addition to company, year, quarter and filing type. Not built.
- Today a fact has no metric label. The stored record has text, numbers and metadata, and the Postgres
  table has no metric column. So each fact must be labelled when it is loaded (for example
  "accounts_payable"), and the 21,913 facts already stored must be labelled too. That can be done from
  the stored text with the alias dictionary, without repeating the model splitting step.
- The new label follows the same pattern as the other filter fields: a column for fast filtering, and
  also inside the JSON record.
- The fact record is a frozen contract in the project rules. Adding a field is a deliberate, recorded
  change, like the earlier tenant field. Re-check that the checker's number comparison is unaffected.
- Way to label (owner agreed 2026-09-20): rules with the alias dictionary first, and the model only for
  facts the rules cannot label. Keep the label as a list, because one sentence can carry more than one
  metric.
- Risk: a wrongly labelled fact would be dropped for good by a hard filter and the right fact would be
  missed. Guard: if the metric filter leaves nothing or very little, fall back to the try again road
  (other names, then no metric filter) instead of failing.
- Effect on speed: far fewer facts to compare per question (897 for 3M FY2018 before), so answers
  should get faster.
- Effect on the thesis: with a hard metric filter, "payable and receivable get mixed up" mostly
  disappears by filtering, not by splitting. So the results must be shown with the metric filter off and
  on, and with splitting off and on, so it is clear which part does the work. This adds to issue 7.

**Q5. Why does `hybrid.retrieve` encode the passages again? Does Postgres or the embedding store
supply the text?**

Postgres supplies the text. The re-encoding is an inefficiency, not a requirement. C3, C4 and the API
filter in Postgres, get the text back, embed it again and score it, and never read the stored
vectors. Probable reason: exact scoring over the filtered set, and FAISS cannot filter by company or
year. Options:
1. Fetch the stored vectors for the candidate ids from the flat index. Same numbers, no re-embedding.
   Least change.
2. Move the vectors into Postgres (pgvector), so one query filters and ranks. Drops FAISS and the
   rule that two stores must hold the same ids, and fits the multi tenant design. Needs the extension.
3. FAISS filtered search with an id selector. Keeps FAISS, adds code.

Recommendation: option 1 now, option 2 when the platform layer is built.

**Decision after Q5 (owner, 2026-09-20): use the stored vectors, no re-encoding.** Option 1. Not built.
- After the filter returns the candidate facts, fetch each candidate's saved vector from the vector
  index by id, instead of embedding its text again. The question is still embedded once.
- The vector index needs a way to look up a vector by fact id. It already keeps the ids in row order,
  and a flat index can return a stored row.
- Fail loudly: if a candidate has no stored vector (the three stores disagree), stop with an error.
  Never quietly re-embed. That keeps the "same ids in every store" rule honest.
- Check before trusting it: run the same questions through the old way and the new way and compare the
  scores, the top 10 and the final answers. Expect the same values apart from tiny rounding. Any
  difference in ranking must be explained before Phase 2 results are run.
- Effect on speed: in the recorded run about half of a 28 second answer (14 of 28 seconds) was embedding
  the candidate facts again, so this should save about that much.
- This touches the shared retrieval core. It moves C3, C4 and the API onto the same saved vectors that
  C1 and C2 already use, which helps issue 7.
- The keyword scoring (BM25) is a separate matter. It stays built per question over the candidates for
  now, it takes milliseconds.
- Option 2 (vectors in Postgres) is not chosen now. Revisit it when the multi tenant platform layer is
  built.

**Q6. Why does `build_index` save to both Postgres and the vector index?**

They hold different things, not the same information.

| Store | Holds | Used for |
|---|---|---|
| Postgres | id, filter columns, the whole fact including text | Filtering, fetching text, citations |
| FAISS index | id and a 768 number vector | Search by meaning |
| Keyword index | Built at startup from the Postgres text | Search by keyword |

The same ids in each is the rule that lets them be joined.

**Owner's wish after Q6 (2026-09-20): the keyword index should not be built at startup.** Build it during
ingestion, or use a better option if one exists. Not built.

Where things stand: two keyword indexes exist today. (a) A whole corpus one, built at startup by reading
all 21,913 facts from Postgres, used only by C1, C2 and the retrieval gate. (b) A small one built per
question over just the filtered candidates in C3, C4 and the API, which takes about 2 milliseconds.

| Option | Good | Bad |
|---|---|---|
| A. Build at ingestion and save next to the vector index, load the file at startup | Small change, same scores, fast start | One more file to keep in step with the other stores |
| B. Keep only the small per question one (drop the whole corpus one) | Nothing built at startup, nothing to save | Word statistics come from the candidates only, so scores differ from the whole corpus version. Fine for C3 and C4, changes C1 and C2 |
| C. Let Postgres do keyword search itself (full text search, or a BM25 add on) | Stays in step automatically, filter and keyword in one query, suits multi tenant | Scores are not the same as our BM25, so results must be re-checked. An add on is one more thing to install |

Recommendation: A now for C1, C2 and the gate. C3, C4 and the API already use B and stay that way.
If issue 7 is settled by moving C1 and C2 onto the filtered path, the whole corpus index is not needed
at all and can go. Look at C when the multi tenant platform is built. Needs the owner's yes on A.

Flag: document uploads through the web page mean facts get added later, but `build_index` today builds
everything once and overwrites the saved files. Uploading needs an "add to the existing indexes" path
for the vector index, the saved keyword index and Postgres, kept in step with each other.

**Decision (owner, 2026-09-20).** Yes to option A: build the keyword index during ingestion and save it
next to the vector index, so nothing is built at startup. Yes to the add path: uploading a document must
add to the existing files, keeping the database, the vector index and the keyword index in step. Not built.

What the add path needs (notes for when it is built):
- Add new facts to all three stores as one job. If any part fails, none of the three is left half
  updated, so the same ids stay in every store.
- The vector index is a flat index that refuses an id it already has, but it can add new ids. So adding
  works, and only the "build from nothing" code overwrites the file.
- The saved keyword index must take new facts and be written out again, or be rebuilt from the saved
  facts as a step of the same job.
- Uploading the same document twice, or a new version of it, needs a rule: use the document card (title,
  version, effective date) to decide replace or add, and never leave duplicates or orphaned facts.
- Deleting or replacing a document must remove its facts from all three stores.
- A start up check that the three stores hold the same ids, and a clear error if they do not.
- Each upload records who uploaded it and when, for the audit trail.

**Q7. In `load_index`, does the keyword index hold the same information as Postgres? Are the
functions not different?**

Different jobs. SQL filters and fetches. The keyword index ranks by relevance and needs corpus
statistics (term counts, lengths). It is derived, rebuilt from Postgres text at every startup and
not stored, so it is not an independent copy. An alternative is Postgres full text search or a BM25
extension, which removes the rebuild and the hand written BM25, but scores would change and the
comparison would need re-running. Also see issue 7: C3 and C4 build a fresh keyword index per question
over the filtered facts, so their statistics differ from the global one C1 and C2 use.

**Status of Q7 (checked 2026-09-20).** Partly dealt with.
- Rebuilt at every startup: dealt with. Decision 14 builds the keyword index at ingestion and saves it.
- Different statistics between C1/C2 and C3/C4: NOT dealt with, issue 7 is still open. The decisions on
  the metric filter (12) and the stored vectors (13) only change C3, C4 and the API, so the gap between
  the two groups gets wider, not smaller.
- Postgres full text search or a BM25 add on: postponed to the multi tenant platform work.

**Q8. Why build a registry at startup, why does `run_full_build` not call it, is there a better way?**

The registry maps every name a company appears under to one placeholder, and is needed at question
time by `remap_entity`. `build_registry` needs a list of entity dicts, and nothing in ingestion
produces one, so it was never wired in. The API builds its own from the indexed company names at
startup because that is the only company list that exists. Better: during ingestion, write an
`entities` table (canonical name, aliases, ticker, CIK) from the names `run_full_build` already has,
plus deterministic alias rules and, optionally, the SEC company tickers list. The API and tests
read that table with one shared function. Placeholder numbers are assigned per request, so adding a
company only shifts numbering, which is harmless as long as nothing depends on the numbers.

**Status of Q8 (2026-09-20).** DECIDED by the owner: build the entities table during ingestion, store it
in Postgres, and use one shared function. Not built. Notes:
- With uploads, a new company or name appears when a document is uploaded, so the same upload job
  (decision 15) must add to the entities table, and remove or change entries when a document is removed
  or replaced.
- For the paper shelf, the product and audience lists that each organisation sets for the upload form
  (the document card) can feed the same table, so their own words become names the splitter recognises.
- The API's own startup version in `api/server.py` and the offline `build_registry` should become one
  shared function.
- Not built.

**Q3 follow up: how the router works, and how each way of handling a question is done (discussion
2026-09-20, nothing built).**

Picture in plain words: the router is a traffic helper at a crossroads. A question arrives, the helper
looks at it, and sends it down one road. There are five roads. After every road, the checker still
runs on the person's ORIGINAL question.

| Road | When | What it does | Status |
|---|---|---|---|
| Straight | One simple thing is asked | Search and answer | Exists (C1, C2 style) |
| Split, money shelf | Look-alike companies, metrics or years | Split into pieces, filter each by company and year | Built (C3, C4) |
| Split, paper shelf | Look-alike products, audiences or versions | Split into pieces, filter by version, date, audience, product | Decided, not built |
| Fix the question (rewrite) | Words are missing or unclear ("last year", "it", "that policy", no company) | Fill in from earlier chat or from what the shelf holds, or ask the person | Not built |
| Try again (expand) | The search found little or nothing | Add other names for the words and search once more | Not built (needs the alias dictionary) |
| Step by step (multi hop) | The second question needs the first answer | Ask one piece, use its answer to ask the next | Decided 2026-09-20: build now, not later |

How the router chooses: counts and simple rules, not a model that guesses. In this order:
1. Look at the question only. Missing or relative words? Fix or ask first. "A and B", "both", "each",
   "compare"? Split. One piece needing another's answer? Step by step. None? Straight.
2. After the first search, look at the results. Very few or weak? Try again with other names. Top
   results say almost the same thing but differ in one detail? Answer each with a label, or ask which
   is meant.
3. Always end with the checker, using the original question.

Who decides money or paper: the shelf (the kind set when the document was uploaded), never the wording.

What does the work inside each road:
- Split (money): the model finds company, metric and year, then code builds the pieces. Built.
- Split (paper): the model finds groups of look-alikes, code checks each phrase is in the question,
  then swaps in one item at a time. To build.
- Fix the question: rules for relative words (a "last year" needs a reference date, from the chat or
  the newest document on the shelf), and earlier turns for "it" and "that". If it cannot be filled in
  safely, ask the person. A model may help, but the rewrite is shown to the person and never replaces
  the original for checking.
- Try again: the alias dictionary adds other names. One retry only.
- Step by step: a model plans the small steps, each step is a full run with sources, answers pass
  forward, with a cap on steps. Weaker guarantee, so it is built after the other roads.

Guard rails:
- The checker always compares against the original question.
- Limits: 6 pieces, 1 retry, a small cap on steps.
- Every choice is written to the log: which road and which signal values caused it.
- When unsure, take the safest cheap road, and ask the person only when a key fact is missing.
- Cost of a wrong choice: splitting when not needed is slow (about a minute a piece). Not splitting when
  needed gives wrong answers. So lean towards splitting when the signals say look-alikes, within the cap.
- Show the person what was done ("I split your question into 2") to build trust.

What already exists: the query analyzer is a small rule based helper that spots look-alike metrics,
years and company pairs. It does not drive anything today, C4 always splits using the model, and its
metric list does not know accounts payable. It is the seed of the router's eyes and needs the new alias
dictionary. Today C4 always splits, so skipping the model call for clear questions is also a speed gain.

Examples:
- "3M payables last year and this year": fix the question first (last year becomes a real year), then
  split by year, then search each piece.
- "Compare leave for contract and permanent staff": split by audience, answer each, then a model writes
  the comparison and the checker verifies it.
- "Fees for a savings account", search finds little: try again with other names, then answer.

**Decisions (owner, 2026-09-20).**
1. The order is agreed. Look at the question first: words missing, fix or ask first; "A and B", "both",
   "each" or "compare", split; one piece needing another's answer, step by step; nothing special,
   straight. Then search and look at the results: too few or weak, try again with other names; top
   results almost the same but differing in one detail, answer each with a label or ask which is meant.
   Always check the answer at the end. Money or paper is never guessed from the words, the shelf decides.
2. When unsure, ask the person only when something important is missing. Otherwise carry on.
3. Step by step is being built now, not after the Master's.
4. Show the person what the router did, in a section like "How it happened". It should say: which road
   was taken and why, the question as understood (if it was fixed), the pieces it was split into, the
   other names used on a retry, any note that several versions were found, the steps for a step by step
   answer, and that the check used the original question. The web page already has a "How this was
   produced" card. This grows that card and the API reply that feeds it.

Approved:
- The step planner (a model that plans the small steps) is a new model job, the seventh. The project
  rule says there are exactly six model jobs and that a seventh needs a written reason and a yes first.
  Reason: without a planner there is no way to break a dependent question into steps. The owner said
  yes on 2026-09-20. When it is built, add it to the list of model jobs, give it its own setting so its
  model can be swapped, and keep its output checked (every step must come from the question).

Flags still open:
- Step by step gives a weaker guarantee than the other roads, and the "How it happened" section should
  say so on those answers.
- Phase 2 grows: the router, the paper split, fix the question, try again, and step by step are all now
  in scope. Suggested build order: router with the straight and money roads, then paper split, then fix
  the question, then try again, then step by step. Time against the Master's deadline still to be checked.

---

## Platform principles and planning decisions (2026-09-20)

Principles (owner, locked in):
- The platform is built to be very fast from beginning to end.
- Heavy work runs in the background, so the person never waits on it.
- Parts talk to each other by passing events, not by waiting on one another.

What this means for the plan:
- An upload returns at once with a job id and the work carries on in the background. Progress is sent as
  events: received, reading pages, splitting batch n of m, embedding, saving, done or failed.
- Answering a question also sends events as things happen (road chosen, pieces, other names tried,
  verification). This feeds the "How it happened" section and makes answers feel faster.
- Events have one agreed shape, kept separate from whatever runs the work, so the helper inside the
  website can later be swapped for a proper queue without changing the events.
- How events reach the browser (server sent events or websockets) is still to be chosen.
- Fast also means: no re-embedding (decision 13), nothing built at startup (decision 14), a cached
  dictionary (Redis), only the needed facts searched (metric filter, decision 12), and background work.
- Flag: one question at a time (issue 6) goes against this principle. It was left alone for the demo,
  but it should be revisited sooner than "with the platform work".

Safety rules for shared data (owner, locked in 2026-09-20):
- Use concurrency control, pessimistic or optimistic as fits, so one request can never overwrite another
  or cause a race.
- Prevent duplicate requests with idempotency keys and similar guards.
- These rules apply to everything built from now on, and to the platform work later.

Where they apply, and the suggested way for each (not built):
- Upload: the client sends an idempotency key with each upload. A double click or a retry with the same
  key returns the same job and never starts a second one. The file fingerprint is a second guard.
- The upload job that changes the shared stores (database, vector index file, keyword index file):
  pessimistic. Only one job may change them at a time, using a lock, because files cannot be merged. The
  lock must expire safely if a job dies.
- Document card edits, dictionary edits, entity table edits: optimistic. Each row has a version number and
  an update only succeeds if the version is unchanged, otherwise the person sees a clear "someone else
  changed this" message. Edits are rare, so conflicts are rare.
- Replace or delete a document: hold a lock on that document id while the job runs, and do all three
  stores as one job.
- Questions: an idempotency key or request id, so a repeated click returns the running or finished answer
  and does not start a second run (saves cost and time).
- Background jobs: a job can be claimed only once (an atomic claim), each step is safe to repeat, and
  events are safe to receive twice.
- The database is the last guard: unique constraints (for example one fingerprint per bank, one document
  per title, version and audience), transactions, and insert or update in one statement.
- Idempotency keys are kept for a limited time (for example 24 hours) with the stored result. The same key
  with different content is refused with an error.
- Tests: two uploads at once, a double click, an edit conflict, and a job killed half way then retried.
  Every one must leave the three stores holding the same ids.

Planning answers so far (asked one at a time before the fix plan is written):
1. Master's deadline: December 1, 2026.
2. Dictionary storage: MongoDB, loaded into Redis at startup.
3. Uploads: option A, a helper inside the website that saves its place after each part, so a restart
   carries on from where it stopped. It must send events and be swappable for a queue later.
4. Changing the database's shape: option A, small scripts we write ourselves, not a ready made tool.
   Rules for the scripts: take a backup of the database first; each change is one short numbered script
   kept in one folder; every script is safe to run twice; every script checks the counts before and
   after (the 21,913 saved facts must all still be there); new tables can keep the create if missing
   style already in the code; the metric label for the existing facts is filled in by a separate
   script after its column is added. A ready made tool can come with the bank platform.
5. Sample papers for the paper shelf: option B. The owner will supply real, public papers, and asked
   where to download them. Links below came from web searches on 2026-09-20 and must be re-checked when
   downloading. Web pages must be saved as PDF first (open the page, press Ctrl+P, choose "Save as PDF"),
   because the loader reads PDF and text files. Suggested folder: `data/papers/`, kept out of git like
   `data/pdfs/`. Aim for 12 to 15 documents that contain planted look alikes. Only public papers, for
   private testing, do not republish them. For each paper the owner fills in the document card (title,
   type, product or area, audience, effective date, version).

   Suggested set (15) and the look alike each one gives:
   - Versions of one document: CBN Guide to Charges for
     [2016](https://www.cbn.gov.ng/out/2016/fprd/guide%20to%20charges%20for%20banks.pdf),
     [2017](https://www.cbn.gov.ng/out/2017/fprd/guide%20to%20bank%20charges%20circular%20to%20all%20banks%20other%20financial%20institutions%20and%20mobile%20payments%20operators.pdf) and
     [2020](https://www.cbn.gov.ng/out/2019/ccd/guide%20to%20charges%20by%20banks%20other%20financial%20and%20non-financial%20institutions%20eff%20jan%201%202020.pdf).
   - The same document from two sites (tests the upload of a repeat): the 2017 guide again on the
     [Zenith Bank site](https://www.digital.zenithbank.com/pdfs/guide-to-bank-charges.pdf).
   - Products side by side: Standard Chartered Nigeria
     [current, savings and fixed deposit terms](https://www.sc.com/global/av/ng-current-cheque-savings-account-and-term-deposit-terms.pdf)
     and First Bank
     [individual account opening](https://www.firstbanknigeria.com/wp-content/uploads/2021/02/Individual-Account-Opening.pdf).
   - Draft versus final: CBN Risk-Based Cybersecurity Framework
     [2024 final](https://www.cbn.gov.ng/Out/2024/BSD/CBN%20Risk-Based%20Cybersecurity%20Framework%20for%20DMBs%20and%20PSBs_2024.pdf) and
     [2024 exposure draft](https://www.cbn.gov.ng/Out/2024/BSD/EXPOSURE%20DRAFT%20OF%20THE%20RISK-BASED%20CYBERSECURITY%20FRAMEWORK%20AND%20GUIDELINES%20FOR%20DEPOSIT%20MONEY%20BANKS%20AND%20PAYMENT%20SERVICE%20BANKS.pdf).
   - Law and its guidance: [Nigeria Data Protection Act 2023](https://cert.gov.ng/ngcert/resources/Nigeria_Data_Protection_Act_2023.pdf)
     and the [NDPC guidance of March 2025](https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf).
   - HR, Nigerian: [IHVN HR Policy Manual](https://ihvnigeria.org/wp-content/uploads/2021/03/IHVN-HR-Policy-Manual-Final-PCL.pdf).
   - HR, who it is for (staff versus faculty, web pages to save as PDF):
     [UVA paid leave for staff](https://uvapolicy.virginia.edu/policy/HRM-013.01) and
     [UVA faculty leaves](https://uvapolicy.virginia.edu/policy/HRM-038).
   - IT help, different systems (web pages to save as PDF):
     [Duke VPN for Mac](https://oit.duke.edu/help/articles/kb0014020) and
     [Georgia State VPN for Windows and Mac](https://technology.gsu.edu/technology-services/cybersecurity/virtual-private-network/).
   Other options if more are wanted: the
   [older 2018 cybersecurity draft](https://www.cbn.gov.ng/out/2018/bsd/risk%20based%20cybersecurity%20framework%20exposure%20draft%20june.pdf),
   [South Carolina annual leave policy](https://www.sc.edu/policies/ppm/hr103.pdf),
   [CBN saving guide](https://www.cbn.gov.ng/OUT/2018/CPD/SAVINGS.PDF),
   [Iowa State VPN for Mac](https://it.engineering.iastate.edu/how-to/install-and-connect-to-vpn-mac/) and
   [Michigan VPN](https://its.umich.edu/enterprise/wifi-networks/vpn/getting-started).
   Note: no ready made Nigerian bank staff handbook was found online. The IHVN manual is the closest.
   Real bank handbooks are private, so the owner could ask a friendly HR contact for a cleaned copy
   later, if wanted.
6. Where uploaded files are kept: option A, a folder on this computer (for example `data/uploads/`), kept
   swappable. The owner may use S3 or Azure Blob Storage later. Notes:
   - One small "file store" door with a few plain actions (save a file, get it back, delete it, list).
     The first version writes to the folder. S3 or Blob Storage versions are added later, and the choice
     is made in settings, with no change to the rest of the code.
   - Save each file under its document id, not the name the person typed, so names cannot clash or be
     abused. Keep a fingerprint (hash) of each file so a repeat upload is spotted, which helps the
     repeat and new version rules.
   - Leave room in the path for a bank id later (bank id, then document id) for the platform for many banks.
   - Keep `data/uploads/` out of git like `data/pdfs/`.
   - Saving a file sends an event, in line with the platform principles above.
   - Not built.

All six planning questions are now answered. The fix plan can be written next.

---

## Housekeeping

- Nothing from the recent work is committed yet, about 190 files in the working tree. DECIDED
  2026-09-20 (owner): wait, do not commit yet. When the owner says go, commit to a new branch, never touch
  the existing ones, and do not push. Risk while waiting: a wrong command or a clean up could lose the
  work, so avoid `git checkout .`, `git restore .` and `git clean` in the meantime.
- `CLAUDE.md` now lives in `backend/`, so it does not load automatically when a session starts at
  the repo root. The frontend has its own generated `CLAUDE.md` and `AGENTS.md`. DECIDED 2026-09-20
  (owner): option B, put a short pointer file in the main folder that says the rules are in
  `backend/CLAUDE.md` and to read it first. Not created yet. It is one new file in a main folder that
  otherwise holds only `backend` and `frontend`, which the owner accepted.
- Name order: the owner's name is "Naheem Quadri". Some files (the funding application, the pitch deck
  content, the team part of the video script) say "Quadri Naheem". DECIDED 2026-09-20 (owner): leave the
  files as they are, no changes. Use "Naheem Quadri" in anything new.
- The `&` in the `R&D` folder name breaks bare commands under `npm run` on Windows. The frontend
  scripts call `node` directly for that reason.

---

## What has been done so far (inventory as of 2026-09-20)

So that nothing is lost. Paths are inside `backend/` unless they start with `frontend/`.

| Work | Where | State |
|---|---|---|
| Research core: offline ingestion, online question pipeline (C0 to C4), evaluation gates | `offline/`, `online/`, `llm/`, `config/`, `contracts/`, `evaluation/` | Built and tested |
| Benchmark: 366 verified items (37 original plus 329 new), 15 filings | `evaluation/data/*.jsonl`, `data/pdfs/` | Done, four checking rounds, two bugs fixed |
| Comment clean up: every comment in 119 files rewritten short and human, nothing broken | All Python files | Done, test suite green |
| Test suite | `tests/` | 570 passed, 3 skipped (561 original plus 9 API tests) |
| Folder restructure: everything backend under `backend/`, UI under `frontend/` | Repo root | Done |
| Enterprise architecture and the 25 part implementation plan | `ENTERPRISE_PLATFORM_ARCHITECTURE.md`, `implementation_plan/` | Written |
| Master's deliverables mapping | `masters_deliverables/` | Written |
| API service (ask, info, health) with the fixes for the registry names, database reconnect, thread cap and warm up | `api/server.py`, `tests/test_api_server.py` | Working prototype |
| Web interface (question box, answer, verification, sources, "How this was produced") | `frontend/` | Working prototype, run steps in `frontend/README.md` |
| Funding files: application, pitch deck content, video script, budget workbook, system design PDF | `funding/` | Done |
| Three milestone PDFs and the form fields | `funding/milestones/`, `funding/HAQQAI_MILESTONES.md` | Done |
| Walkthroughs of the question flow and the ingestion flow, with real samples | `sample_walkthrough/` | Done |
| Editor setting so Go to Definition finds the backend | `.vscode/settings.json` at the repo root | Done |
| This tracker | `KNOWN_ISSUES.md` | Kept up to date |
| Standing rules saved in my memory: short human comments, plain language, the deadline, the platform principles | My memory folder | Saved |

Not built yet: everything marked "decided, not built" above, plus Phase 2 runs and the results chapter.

---

## Update log

- 2026-09-19: File created with the unit flakiness and slow answers issues.
- 2026-09-20: Expanded into a full tracker. Added the phase status, the Phase 2 to do list and the
  open decisions. Added issues 3 to 5. Data check: 15 filings needed, 5 indexed, 82 of 366 items
  answerable today, all benchmark metrics inside the splitter's vocabulary.
- 2026-09-20 (later): Recorded the Query Router "expansion" options and recommendation under open
  decision 5. Ran `filter_candidates` for real and added the finding that it ignores the metric
  (new issue 5, the old issue 5 is now 6). Restarted Docker Desktop and the database, data intact.
- 2026-09-20 (later): Added `backend/sample_walkthrough/` with `query_flow.py` and `ingestion_flow.py`.
  Each lists every function called, with a real sample request and response, and each function is
  a real Python reference so Go to Definition works. Run either file to print the outline, or add
  `--detail` to print the samples. Added `.vscode/settings.json` (extraPaths to `backend`) so the
  editor can resolve the imports. Ingestion timing sample recorded under the Phase 2 to do list.
  If a function is renamed or moved, the walkthrough file fails on import, which is the cue to update it.
- 2026-09-20 (later): Recorded the design questions and answers (vocabulary, general collisions,
  expansion and rewriting, storage, re-embedding, registry). New issues 7 to 9 and open decisions 8
  to 10. Main finding: C1/C2 and C3/C4 use different candidate retrieval, which confounds the NPCE result.
- 2026-09-20 (later): Recorded the plain language explanation of Q1 and the owner's decision: free text
  metrics so every question goes through NPCE, plus a wider alias dictionary from several sources,
  with Redis under consideration. Open decision 9 is now decided, not yet built.
- 2026-09-20 (later): Recorded thoughts and a proposed solution for using NPCE on non finance
  questions (under Q2 follow up). Not started, waiting on the owner.
- 2026-09-20 (later): Owner decided to build the non finance NPCE steps (all topics, cheap swappable
  model, limit on pieces, test set). Recorded the router answer (kind comes from the document, not from
  guessing) and a draft minimum document card for the upload form.
- 2026-09-20 (later): Recorded the router discussion (five roads, how each is done, guard rails,
  examples) under "Q3 follow up". Added open decision 11. Nothing built.
- 2026-09-20 (later): Owner agreed the router order, the unsure rule (ask only when something important
  is missing), building step by step now, and a "How it happened" section for the person. Recorded the
  decisions and three flags: the step planner is a new model job, step by step has a weaker guarantee,
  and Phase 2 scope grew. Decision 11 is decided, nothing built.
- 2026-09-20 (later): Owner approved the step planner as the seventh model job. Recorded. Still no code.
- 2026-09-20 (later): Owner decided the filter will also use the metric. Recorded under Q4, issue 5 and
  open decision 12, with what it needs (labels on every fact, frozen record change, fallback) and its
  effect on speed and on the thesis. Not built.
- 2026-09-20 (later): Owner agreed how facts get their metric label: dictionary rules first, the model
  only for what the rules cannot label. Recorded under Q4. Still no code.
- 2026-09-20 (later): Owner decided to use the stored vectors and stop re-embedding candidate facts.
  Recorded under Q5, on issue 2 and as open decision 13, with the checks needed. Not built.
- 2026-09-20 (later): Owner does not want the keyword index built at startup. Recorded three options
  under Q6 with a recommendation (build at ingestion and save it), open decision 14, and a flag that
  uploads need an add to existing indexes path. Waiting for a yes on the option.
- 2026-09-20 (later): Owner said yes to option A (keyword index built at ingestion and saved) and yes to
  the add to existing indexes path for uploads. Open decisions 14 and 15 are decided. Notes on what the
  add path needs are under Q6. Not built.
- 2026-09-20 (later): Checked Q7: the startup rebuild is dealt with (decision 14), the C1/C2 versus
  C3/C4 statistics gap is not (issue 7 still open and getting wider). Recorded Q8 status with notes for
  uploads and the paper shelf.
- 2026-09-20 (later): Owner decided open decision 10 (issue 9): build the entities table during
  ingestion, store it in Postgres, one shared function. Recorded, not built.
- 2026-09-20 (later): Tidied two stale labels (decisions 3 and 5). Owner decided issue 7 (open decision
  8): keep the cells and add two ablation setups, "split no filter" and "filter no split". Recorded, not
  built.
- 2026-09-20 (later): Owner decided open decision 1: build the plain LLM baseline. Recorded, not built.
- 2026-09-20 (later): Owner decided open decision 2: a small test of about 20 items first, then the
  full 366. Recorded, not run.
- 2026-09-20 (later): Owner decided open decision 4 (issue 1): a small rule removes a repeated unit in
  `assemble_draft`. Recorded, not built. Gates to be re-run afterwards to see what moved.
- 2026-09-20 (later): Owner decided open decision 6: keep the 366 items for now. Recorded.
- 2026-09-20 (later): Owner decided issue 3: restart the database container by itself, and add a
  connect time limit with a clear message. Added as open decision 16. Recorded, not built.
- 2026-09-20 (later): Owner decided issue 6 (one question at a time): leave it for now, revisit with the
  platform work. Recorded, nothing to build.
- 2026-09-20 (later): Owner decided to wait before committing the work. Recorded under Housekeeping.
- 2026-09-20 (later): Owner confirmed the name is "Naheem Quadri" and said to leave existing files as
  they are. Recorded under Housekeeping. No files changed.
- 2026-09-20 (later): Owner decided to add a pointer file in the main folder that sends readers to
  `backend/CLAUDE.md`. Recorded under Housekeeping, not created yet. All 10 open questions of this round
  are now answered. A short "still open" list was added after decision 16.
- 2026-09-20 (later): Owner gave the Master's final date: December 1, 2026. Recorded here and in memory.
  Six planning questions are being asked one at a time before the fix plan is written: (1) deadline,
  done; (2) dictionary storage, Redis or the main database; (3) how uploads run in the background;
  (4) how to change the database's shape; (5) sample papers for the paper shelf; (6) where uploaded
  files are kept.
- 2026-09-20 (later): Owner answered planning question 2: MongoDB as the dictionary's source of truth,
  loaded into Redis at startup. Recorded under Q1 and decision 9, with notes on running three boxes.
- 2026-09-20 (later): Owner answered planning question 3: uploads run in a helper inside the website
  (option A). Owner also locked in the platform principles: very fast end to end, background work,
  communication by events. Added a "Platform principles and planning decisions" section. Flagged that
  one question at a time conflicts with the speed principle.
- 2026-09-20 (later): Owner answered planning question 4: small scripts we write ourselves for database
  changes, with a backup first and count checks. Added an inventory section, "What has been done so
  far", listing every earlier piece of work and where it lives.
- 2026-09-20 (later): Owner answered planning question 5: real papers, supplied by the owner. Looked up
  real public sources by web search and recorded 15 suggested papers with links, what look alike each
  gives, and how to save web pages as PDF. Still to answer: question 6, where uploaded files are kept.
- 2026-09-20 (later): Owner answered planning question 6: a local folder, behind a swappable file store
  door so S3 or Azure Blob Storage can be used later. All six planning questions are now answered. Next
  step: write the fix plan.
- 2026-09-20 (later): Owner asked to lock in the safety rules for shared data: concurrency control (locks
  or version numbers) and idempotency keys against duplicate requests. Recorded in the "Platform
  principles" section and saved in memory. The plan is not to be written yet, the owner said not yet.
- 2026-09-20 (later): Wrote the fix and build plan in `ISSUES_FIX_PLAN.md` (new file). It covers all 9
  known issues and all 16 decisions, with diagrams, a week by week calendar to December 1, and tests
  for every step. Nothing is built yet. Still open: reworded finance question set, when to index the
  10 filings, and when to start building.
- 2026-09-20 (later): Owner clarified what "events" means. An upload only saves the file, sends a
  `document.uploaded` event and answers at once. Services listening for it do the ingestion by
  themselves, and the same events feed live progress in the browser. The way events travel must be
  swappable: local, RabbitMQ, AWS SQS or AWS Lambda, chosen by one setting. Plan steps 0.5 and 3.3
  in `ISSUES_FIX_PLAN.md` were rewritten to match.
- 2026-09-20 (later): Owner decided the startup check must not stop the app. The database is the truth.
  At startup the app fires an event; a background check compares the database with the vector and
  keyword indexes, and a repair service fixes any gap from the database under the same lock as
  uploads. Only a huge mismatch or a wrong-build file refuses answers. Benchmark runs do a strict
  check first. Plan steps 1.2 and 1.3 were rewritten (a missing vector is skipped and noted, not fatal).
- 2026-09-20 (later): Owner decided the tests compare only three setups: plain AI, naive RAG (C0) and
  C4+. Nothing else. Decision 8 and issue 7 were changed (C1, C2, C3 and the two ablations are not run;
  old configs and numbers stay as history). `ISSUES_FIX_PLAN.md` was corrected in the timeline, the
  diagrams, step 1.4 and 5.2, the test grid, the risks, the glossary and the coverage map. The
  consequence for the thesis (cannot split the gain into its parts) is written in step 5.2.
- 2026-09-25: Owner confirmed we start implementing the fixes now, using the 5 filings and 21,913
  facts already indexed as samples (no need to wait for the other 10). Owner also confirmed: do not
  index the 10 missing filings yet, and do that only after every known issue is fixed; do not push
  anything (commits still wait for a go-ahead, on a new branch, per the standing rule). Updated
  `ISSUES_FIX_PLAN.md`'s calendar and step 5.3 to move indexing to after work packages 0 to 4.
- 2026-09-25: Started building. Fixed known issue 1 (unit fix, step 1.1): `online/generation/assembler.py`
  now skips the unit in a line if the value already ends with it. 8 new tests added, all pass, and the
  known "million million" pattern is confirmed gone. Also built the shared connect-with-timeout helper
  (step 0.2, `infra/connections.py`, NEW): Postgres connects now go through `connect_postgres` (10 s
  limit, clear "BoxUnavailable" message naming the box); MongoDB and Redis helpers are stubbed for
  work package 2 (packages not installed yet). Wired into `offline/indexing/metadata_store_postgres.py`,
  the only real Postgres connect point in the codebase (checked by search). Verified twice: full suite
  585 passed, 3 skipped (up from 577 by exactly the 8 new tests, no regressions), and a live connect to
  the real `financial_rag_v2` database confirmed the fact count is unchanged at 21,913. Started Docker
  Desktop and the `financial_rag_postgres` container by hand for this session (its restart policy is
  still "no"; step 0.1 will fix that). Not yet done: 0.1 (compose file + restart policy), 0.3 (migration
  runner), 0.4 (locks/tickets), 0.5 (events), 1.2 to 1.4 and everything after.
- 2026-09-26: Fixed known issue 3 (step 0.1, database keeps stopping). Turned on auto-restart for the
  existing `financial_rag_postgres` container with `docker update --restart unless-stopped` (no data
  touched, fact count checked before and after: 21,913). Added `docker-compose.yml` (NEW, repo root of
  backend) with Postgres, MongoDB and Redis, each `restart: unless-stopped` with a healthcheck and a
  named volume; Mongo/Redis are not wired into any code yet (reserved for work package 2/3). Updated
  `README.md` section 3 with compose instructions and the note that `docker compose up` will refuse to
  touch a pre-existing hand-made container by the same name, so data is never at risk.
  6 new fast tests check the compose file's own shape (all three boxes present, all restart
  unless-stopped, all have healthchecks and named volumes, Postgres credentials match the app's DSN).
  Verified live: `docker restart financial_rag_postgres` came back up on its own.
  While testing this, found and fixed a real bug in step 0.2's helper: `localhost` resolves to two
  addresses (::1 and 127.0.0.1), and psycopg's `connect_timeout` applies per address, so a stopped
  Postgres took 20.1s to fail, not the ~10s the plan promises. Fixed `infra/connections.py` to run the
  connect in a worker thread and enforce a true wall-clock cap; re-tested live with the container
  stopped: now fails in 10.0s with a clear message, and reconnects cleanly (21,913 facts) once the
  container is started again. Added a regression test with a fake slow driver. Verified twice: full
  suite 592 passed, 3 skipped, run twice back to back, no flakiness, no regressions.
  Not yet done: "start Docker Desktop at sign-in" is a manual OS setting (told to the owner in the
  README, not something code can turn on); 0.3 (migration runner), 0.4 (locks/tickets), 0.5 (events),
  1.2 to 1.4 and everything after.
- 2026-09-26 (later): Built step 0.3, safe database change scripts. New `db/migrate.py` (runner) and
  `db/migrations/` (NEW folder, empty for now, with a README on naming). A run: backs up first via
  `docker exec <container> pg_dump` (refuses to run anything if the backup fails, tested with a bad
  container name); takes a Postgres advisory lock so two runners can't collide (tested by holding the
  lock from another connection: the waiting runner gives up cleanly after its wait, applies nothing,
  and a normal run afterwards still applies exactly once); runs each pending `NNNN_name.sql` file in
  its own transaction, rolled back whole on any failure; is safe to run twice (second run applies
  nothing, reports what was already applied); and refuses a script that would shrink the `propositions`
  table unless its first line says `-- allow-propositions-change`. Found and fixed a real bug while
  testing: a broad `except Exception` was swallowing the specific "would shrink propositions" message
  and replacing it with a generic one — fixed so that message survives. 12 pure-logic tests (file
  naming, ordering, duplicate detection, the shrink marker, the backup command) plus 12 tests against
  the real test database (financial_rag_test) covering every scenario above. Verified twice: full
  suite 615 passed, 3 skipped, run twice back to back, no regressions; and a direct check that the real
  production database still has all 21,913 facts (the integration tests never touch it, only the
  dedicated test database). Added `db/backups/` to `.gitignore`.
  Not yet done: 0.4 (locks/tickets for uploads and shared files, separate from this migration lock),
  0.5 (events), 1.2 to 1.4 and everything after.
- 2026-09-26 (later): Built step 0.4, the shared locks/version-numbers/tickets helpers, all backed by
  Postgres for now (Mongo/Redis still not installed). Three new modules:
  `infra/locks.py` -- one writer at a time for shared files, a lock frees itself once its time (TTL)
  runs out so a dead job never blocks everyone forever; a holder can't accidentally release a lock it
  already lost to expiry.
  `infra/versioning.py` -- generic optimistic-concurrency update: an edit only lands if the version
  number it read is still current, otherwise it is refused with a plain message.
  `infra/idempotency.py` -- run-once tickets: same ticket + same content runs the work once and hands
  back the same result every time after; same ticket + different content is refused; a ticket past its
  24-hour TTL is treated as new; a ticket stuck "running" past a short staleness window (a crashed job)
  can be reclaimed and retried; a genuine double click (two callers, same ticket, at the same time) is
  proven with a real two-thread, two-connection test -- only one caller's work function ever runs.
  20 new tests (8 locks, 4 versioning, 8 idempotency) all against the real test database. Verified
  twice: full suite 635 passed, 3 skipped, run twice back to back, no regressions; production database
  re-checked at 21,913 facts, untouched.
  Not yet done: 0.5 (events / swappable bus), 1.2 to 1.4 and everything after. These three helpers are
  not wired into any real feature yet -- that happens as each later step (uploads, documents, dictionary)
  is built and needs them.
- 2026-09-26 (later): Built step 0.5, events that start the work (swappable bus). New `infra/events.py`
  and `infra/events_demo_listener.py`. One event shape (id, type, job_id, step, message, time, data).
  One factory, `get_event_bus(name, conn)`, picks the bus by a single name: "local", "rabbitmq", "sqs"
  or "lambda" -- an unknown name raises a clear error listing the choices. All four are backed by the
  same Postgres-based engine today (real RabbitMQ/SQS/Lambda clients are wired in later, behind the
  same shape, when we actually deploy); this already proves listeners and tests do not care which one
  is picked. A listener is a plain function -- `infra/events_demo_listener.py` is a worked example, and
  a test statically checks its source imports nothing from `infra`, so it can run unchanged in a worker
  or a Lambda later. Publishing the same event id twice only delivers once. A listener that fails is
  retried on the next dispatch call; after 3 failures it is parked in a "failed" pile without blocking
  later events to other listeners, and can be replayed by hand once fixed. A poller (for a future
  browser/SSE progress view) can ask for everything after a sequence number, so a late-joining or
  reconnecting browser picks up only what it missed.
  50 new tests, most run once per bus name (all four) to prove the swap is real: order preserved,
  duplicate publish, retry-then-succeed, parked-after-max-attempts, later events not blocked, replay,
  two listeners on one event type, a listener only hearing its own type, late join, reconnect-from-last-id,
  per-job isolation, unknown bus name, and the no-bus-import check on the demo listener.
  Verified twice: full suite run twice back to back, 685 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched (all new tests use the dedicated test
  database only).
  Work package 0 (safe ground) is now fully built: 0.1 restart, 0.2 timeouts, 0.3 migrations, 0.4
  locks/versioning/idempotency, 0.5 events. None of it is wired into a real feature yet -- that starts
  with work package 1 (fix the answers), beginning with step 1.2 (stored vectors) next.
- 2026-09-26 (later): Built step 1.2, stored vectors instead of re-embedding (known issues 2/8,
  decisions 3/13). `PersistentDenseIndex.get_vectors(ids)` (offline/indexing/dense_index_faiss.py, new
  method) reads a candidate's vector straight back out of the saved FAISS index instead of asking the
  embedder to compute it again. `online/retrieval/hybrid.retrieve` takes an optional `dense_index`
  argument: when given, it uses the stored vectors and never calls `encode_passages`; when left out
  (C1/C2 gates, unaffected by this decision), it works exactly as before. A candidate with no saved
  vector is quietly skipped rather than scored wrong; if more than half of a sub-query's candidates
  are missing, `TooManyVectorsMissing` is raised instead of guessing on a badly incomplete set.
  Wired into `pipeline_c3.py` and `pipeline_c4.py` (the only two decided to change, per issue 7): each
  passes `bundle.dense_index` now, and if `TooManyVectorsMissing` is raised for a sub-query, that one
  sub-query is reported "not provided" (same path already used for ordinary partial-retrieval misses)
  instead of crashing the whole question -- no LLM calls are wasted on it either.
  Compared old vs new on real data before switching anything, as the plan asked: ran three real
  questions against the real 3M FY2022 facts (841 candidates) through both paths. Dense scores matched
  to within 1e-7 (floating-point rounding only), the top-ranked fact was identical every time, and the
  new path was over 20x faster (about 0.04s vs 0.9-3.0s for this one retrieval step, no LLM cost since
  hybrid.retrieve never calls a model).
  14 new tests: 5 for `get_vectors` (round-trip, normalization matches upsert, missing ids reported,
  empty index, survives save/load), 7 for `hybrid.retrieve`'s new path (no re-embedding call happens,
  same scores as the old path, one missing candidate skipped, over-half missing raises, exactly-half
  is still allowed, all-missing raises, empty candidates), and one pipeline-level test each for C3 and
  C4 proving a missing-vector sub-query abstains cleanly rather than crashing.
  Verified twice: full suite run twice back to back, 699 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched.
  Not yet done: 1.3 (keyword index saved at ingestion + the repair-by-event startup check), 1.4 (metric
  filter) and everything after.
- 2026-09-26 (later): Built step 1.3, keyword index saved at ingestion plus a background repair check
  (known issue 8, decision 14, and the "database is the truth, never stop to check" decision from
  earlier this session). Two parts:
  Part A -- saved keyword index: `SparseIndex.save`/`.load` (offline/indexing/index_writers.py) persist
  the already-tokenized postings to a small JSON file (atomic write via temp file + rename, so a
  reader never sees a half-written file), with a version tag so a file from an older format is refused
  and rebuilt rather than misread. `build_index` now saves this file next to the dense index.
  `load_index` loads it directly -- no Postgres round trip, no re-tokenizing -- and only falls back to
  the old rebuild-from-Postgres path (then saves the file for next time) when the file is missing or
  corrupt, which self-heals a build made before this step existed with no crash and no blocked startup.
  Added `SparseIndex.remove` for dropping a ghost id.
  Part B -- background repair: new `offline/indexing/repair.py`. `diff_index_state` compares the
  database's ids against the dense and keyword indexes (pure function, no boxes needed). A handful
  missing is healed from the database (the source of truth) using the existing lock (step 0.4) so a
  concurrent repair or upload can't collide; a ghost in the keyword index is removed outright; a ghost
  in the dense (FAISS) index is reported but left alone, since a flat FAISS index has no in-place
  delete -- removing it needs a full rebuild, tracked as its own future job, not a live patch, and it
  can never be served as evidence anyway since the database lookup for a ghost id always fails. Over
  half the database's facts missing from an index is treated as "wrong file loaded", not a handful of
  drift -- reported, left alone, not silently patched. Wired through the existing event bus (step 0.5):
  `startup.check_requested` conceptually becomes one function call that always ends in exactly one of
  `index.check_done`, `index.mismatch_found` (triggers the repair), or `index.severely_broken`.
  `start_background_index_check` runs this in a background thread from `api/server.py`'s startup, so
  the app never waits on it.
  Verified live against the real production database (financial_rag_v2, 21,913 facts), not just the
  test database: called the real startup path end to end. The legacy production index (built before
  this step existed) self-healed on its own -- it created the missing 5.15 MB `dense_index_v2.sparse.json`
  file automatically, no crash, no manual step. The background check then genuinely confirmed
  `index.check_done` against the real facts -- the three real stores really do agree. A clean, isolated
  before/after timing on the real 21,913 facts: rebuilding the keyword index from Postgres text took
  0.78s; loading the saved file took 0.04s -- about 20x faster, and it will matter more as more filings
  are indexed.
  35 new tests across three files: 5 in test_index_writers.py (save/load round trip, missing file,
  corrupt file, old format version, save/load preserves BM25 parameters) plus `remove` tests; 5 in
  test_index_build.py (file is created, loading reads the file not Postgres, self-heals a legacy build,
  self-heals a corrupt file); 21 in test_index_repair.py (every diff scenario, the severe-vs-normal
  threshold at exactly half, the full check-then-repair cycle for a clean/mismatched/severely-broken
  index, and the background thread actually running without blocking and then healing the index).
  Verified twice: full suite run twice back to back, 733 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched (the check only reads propositions; it only
  writes to the index files and to its own new event/lock tables, which now legitimately exist in the
  production database as a result of this real run).
  Honest gap: a ghost in the dense (FAISS) index is detected and reported but not physically removed
  by this step -- doing that safely needs a full index rebuild, which is out of scope here and tracked
  as future work, not silently skipped.
  Not yet done: 1.4 (metric label and metric filter) and everything after.
- 2026-09-26 (later): Built step 1.4, metric label and metric filter (known issue 5, decisions 8 and 12).
  `Proposition` gained a `metrics: tuple[str, ...] = ()` field (a deliberate, tested change to the
  frozen model, as the plan called for) -- empty means "not labelled yet", never "about nothing".
  `db/migrations/0001_add_metrics_column.sql` adds the matching Postgres column and a GIN index; the
  store also self-heals a table made before this column existed (same pattern as step 1.3), so nothing
  needed a manual migration run on the real database.
  Labelling (`offline/labeling/metric_labeler.py`, NEW): rule-based only for now -- a small alias
  dictionary (about 19 metrics, matching the 366-item benchmark's own metrics) matches known phrases in
  a fact's text. A fact nothing matches stays unlabelled, which the plan itself allows as a valid
  outcome. Deliberately NOT adding a model-fallback call here: that would be an eighth LLM call site,
  and the project's standing rule is that a new call site needs the owner's written reason and a yes
  first (already given once, for the step planner) -- so this waits for that same sign-off rather than
  being added quietly. `offline/labeling/backfill_metrics.py` (NEW) relabels every fact from its own
  text, safe to run twice (a second run changes nothing), and reports total/labelled/changed counts.
  Filter (`online/retrieval/metadata_filter.py`): `filter_candidates` takes `enable_metric_filter`
  (new `RetrievalConfig.enable_metric_filter`, default False so every existing config's behaviour is
  unchanged) and, when on, also filters by the sub-query's metric. If that empties out the candidates
  (unlabelled fact, or the metric name doesn't match), it falls back to company+year alone rather than
  a silent empty answer -- the full "try again with other names" road (step 4.5) doesn't exist yet, so
  this is the interim safeguard until it does, said plainly in the code comment. Wired into
  `pipeline_c3.py` and `pipeline_c4.py` only, matching the C3/C4/API-only decision.
  Verified live against the real production database (financial_rag_v2), not just the test database:
  the column self-healed with no manual step; ran the real backfill on the real 21,913 facts -- 2,752
  labelled (12.6%), a second run changed 0 (confirmed idempotent); checked the exact scenario the plan
  names -- 3M FY2018 company+year alone is 897 candidates (matches the number already on record), with
  the metric filter added for net_income it drops to 14. Spot-checked those 14 by eye: all genuinely
  mention net income, though a few are close variants (attributable to noncontrolling interest, adjusted
  net income) rather than the exact GAAP line -- an honest limit of phrase matching, not a bug. Wrote a
  random 100-item sample of labelled facts to `data/metric_label_spot_check_sample.json` (real
  production data, not fabricated) for the owner's own review -- confirming the share that are truly
  right is a human judgement call this session cannot make on the owner's behalf.
  36 new tests: 13 for the labeler (clear metric, two metrics, unlabelled, case, whole-phrase not
  substring, "net sales"/"total net sales" both mapping to total_revenue, plain "revenue" kept
  distinct, basic vs diluted eps, capex abbreviation, a documented gaap/non-gaap double-match edge
  case, empty text, a custom dictionary as the seam for 2.2's bigger one); 9 for the backfill (labels,
  leaves unrecognized facts alone, idempotent on a second run, fact count unchanged, reports the
  labelled share, relabels when a fact's text is corrected, the review sample's size and reproducibility);
  4 for the metric filter (off by default, on narrows correctly, falls back when it would empty out,
  no metric on the sub-query applies no constraint); plus 6 for the metadata store's new `metrics`
  round trip and query filter (single metric, multiple labels on one fact, matches any of several
  requested, no filter and empty filter both mean "everything", unlabelled defaults to empty).
  Verified twice: full suite run twice back to back, 766 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched; 2,752 genuinely and durably labelled.
  Not yet done: work package 2 (company list, the bigger free-text dictionary) and everything after.
- 2026-09-26 (later): Owner gave the yes for the model fallback part of step 1.4 that was deliberately
  left out earlier ("model only for leftovers"). Built it as a new, seventh real call site,
  `metric_label_fallback` (registered as optional in `config/schema.py`, so no existing config needs
  it and none broke): `llm/schemas.py` gained `MetricLabelResolution` (reasoning + a metrics list);
  `llm/routing.py` registered its schema. `offline/labeling/metric_labeler.py` gained
  `label_metrics_llm_fallback` (only called for a fact the rules found nothing on; the model may only
  pick from the given lexicon, anything else it returns is dropped -- same "classify, never invent"
  rule `offline/extraction/numeric_extractor.py`'s tier 3 already follows; schema-invalid output is
  dropped too, never guessed) and `label_metrics_with_fallback` (rules first at zero cost; the model
  only runs if a client is actually passed in, so leaving it out costs nothing and behaves exactly as
  before). `offline/labeling/backfill_metrics.py` now reports `labelled_by_rules` and
  `labelled_by_model` separately, and its CLI takes an optional `--config` naming a config with this
  call site set up.
  22 new tests for the labeler (accepts an allowed metric, drops an invented one, an empty list is a
  correct answer, schema-invalid is dropped, the lexicon and sentence are sent correctly, the
  dispatcher never calls the model when rules already succeeded, calls it only when rules found
  nothing, and behaves exactly as before when no client is given) plus 3 for the backfill's use of it.
  Verified twice: full suite run twice back to back, 778 passed, 3 skipped both times, no regressions.
  Did NOT run this against the real 21,913 facts: doing that for real would call the model on the
  roughly 19,161 still-unlabelled ones, which costs real money, and no config yet has this call site
  set up (that is 5.2's job, building the actual C4+ config). Production database re-checked:
  unchanged at 21,913 facts, still exactly 2,752 labelled by rules only.
  Now starting work package 2: the shared company list (step 2.1).
- 2026-09-26 (later): Live-tested the metric_label_fallback call site for real, on 3 real unlabelled
  production facts only (not the full ~19,161) -- a real OpenAI call each, model
  gpt-4.1-mini-2025-04-14, cost a few hundred tokens total (about 374 in / 49 out for one of them).
  Two facts genuinely outside the 19-metric lexicon (net cash from investing activities; a medium-term
  note) correctly came back empty -- an honest "nothing here", not a forced guess. One real rules-miss
  case, "basic earnings per common share" (the extra word "common" breaks the rule's exact phrase),
  was correctly labelled `basic_eps` by the model -- real proof the fallback adds value the rules alone
  don't reach. None of these 3 results were written back to the database (read-only test); doing that
  for real, and for the rest of the ~19,161, is still a separate decision. Also confirmed and answered
  the owner's question: every LLM call site (all six existing plus the new metric_label_fallback) goes
  through the same one funnel (`llm/routing.py`'s `RoutedLLMClient`) -- structured input (the payload
  is a plain dict, JSON-encoded as the user message, never hand-built prose) and structured output
  enforced by a pydantic schema per call site, native to each provider (OpenAI's `chat.completions.parse`
  with `response_format=<pydantic model>`; Anthropic via a forced tool-use call whose input schema is
  the same pydantic model; Ollama via its own `format` field then `model_validate_json`). No call site
  is handled differently.
- 2026-09-26 (later): Built step 2.1, one shared company list (known issues 4 and 9, decision 10).
  New `entities` table (`db/migrations/0002_add_entities_table.sql`; also self-created on connect, same
  pattern as steps 1.3/1.4) with `company`, `ticker`, `version`. `PostgresMetadataStore.upsert` now
  keeps it current as a side effect of every fact it writes -- a new company shows up from ingestion
  alone, no code change needed -- and never overwrites a known ticker with a missing one from a later
  fact. A store with an empty entities table but existing facts (a database from before this step)
  self-heals once from those facts' own metadata, the same "database is the truth" pattern as the
  index repair in step 1.3; once healed it never re-scans or reverts a manual edit.
  New `offline/registry/entities.py`: `list_entities`/`update_entity_ticker` (the latter guarded by the
  step 0.4 version-number helper -- a stale edit is refused, not silently applied) and `build_registry`,
  which is the exact variant-generation logic that used to live only in `api/server.py` (short name,
  first word, possessives, never longer than the full name, a short name shared by two companies
  dropped) moved to the one shared place. `api/server.py` now imports it instead of keeping its own
  copy, and reads the company list via `bundle.metadata_store.entities()` instead of scanning every
  proposition to rediscover companies. `offline/registry/placeholder_builder.py` (the other, unused
  builder) is left in place with a docstring pointing here -- it does a genuinely different job
  (dedup by ticker/CIK plus embedding collision detection over already-known surface forms) and has
  its own tests, so it was documented rather than deleted or merged.
  Verified live against the real production database: the three real companies (3M Company, Activision
  Blizzard Inc., Adobe Systems Incorporated) self-healed into the new table with no manual step, the
  shared `build_registry` produced the correct 13-entry registry (matching the known-good pattern from
  the earlier possessive-length bug fix), and the fact count is unchanged at 21,913. Tickers came back
  empty for all three -- an honest reflection of real data (the original ingestion never recorded a
  ticker), not something invented to fill the gap.
  25 new tests (14 in test_entities.py, plus test_api_server.py updated to the new Entity-based shape):
  a new company appears from ingestion alone, a second fact for the same company doesn't duplicate the
  entity, a later fact with no ticker doesn't erase a known one, the legacy self-heal path (twice: once
  plain, once confirming it doesn't run again or revert a manual edit), all every rule from the
  original variant logic (full-name resolution, possessive of the short name, the full name's own
  possessive correctly excluded for being too long, a short name shared by two companies dropped,
  empty input), and the version-number conflict on two edits to one entity.
  One honest note: a full-suite run mid-session showed one single, unrelated test fail
  (test_metric_filter_off_by_default_returns_everything_in_scope) while several other real,
  concurrent scripts against the live production database were also running. It did not reproduce on
  two clean, back-to-back re-runs afterward (792 passed, 3 skipped both times) or when run alone, so it
  is recorded here as an unreproduced one-off flake under load, not a real bug -- flagged rather than
  quietly ignored.
  Not yet done: step 2.2 (free-text metrics, the bigger MongoDB/Redis dictionary) and everything after.
- 2026-09-26 (later): Built the infrastructure half of step 2.2, free-text metrics (known issue 4,
  decision 9). Installed `pymongo` and `redis` (added to `pyproject.toml`); started real MongoDB and
  Redis boxes for the first time via `docker-compose.yml`'s `mongo`/`redis` services (both healthy).
  New `infra/dictionary.py`: MongoDB is the source (one `phrase -> metric` document per entry, a unique
  index on phrase, `source` and `version` recorded so a hand-curated entry is never silently overwritten
  by a later seed run), Redis is the fast copy everything actually looks phrases up in, and one function
  (`lookup_metric`) is the only way anything reads it. `log_unmatched_phrase` writes to MongoDB (durable)
  not Redis, so a phrase nobody has labelled yet survives a Redis restart, ready for the dictionary to
  grow from later. Seeded today from the two sources already in the codebase -- the 19-metric lexicon
  and the metric labeler's phrase aliases -- combined into one dictionary; content from further sources
  (accounting taxonomies, glossaries, Nigerian banking terms, licences checked first) is still to be
  gathered and was not invented or rushed here.
  `online/analysis/query_splitter.py`'s `LLMQuerySplitter` now takes optional `dictionary_lookup`/
  `unmatched_logger` functions: a phrase the model couldn't map to the fixed lexicon (`unmapped_metrics`,
  previously read from the response and silently discarded despite its own comment saying it should be
  logged) gets one more try against the free-text dictionary and, if resolved, joins the sub-query's
  metrics; if not, it is logged rather than dropped. Wired into `pipeline_c3.py`/`pipeline_c4.py` (both
  optional, default None, so every existing caller and test is unaffected) and into `api/server.py`'s
  real startup: it connects to Mongo/Redis, seeds and loads the dictionary once, and hands the pipeline
  bound lookup/log functions -- if either box is unreachable, it logs a warning and runs with free-text
  metrics off rather than refusing to start (confirmed live: stopping both boxes made this path return
  cleanly in about 10 seconds, our standard timeout, with a clear warning; restarting them and retrying
  worked normally again).
  Verified live end to end, not just against fakes: seeded the real dictionary, loaded it into the real
  Redis, looked up "net income" -> net_income and "total net sales" -> total_revenue correctly, and
  confirmed an unknown phrase logs to MongoDB rather than raising.
  31 new tests: 12 for infra/dictionary.py against the real boxes (seed/load/lookup round trip, case
  insensitivity, seed is safe to run twice, a hand-curated entry is never overwritten, a dictionary
  change is picked up by reloading, the unique index rejects a conflicting metric for the same phrase,
  unmatched logging counts repeats); 9 for the splitter (a known phrase resolves, an unknown one falls
  back to passthrough and is logged, a resolved one is never logged, resolved metrics join the model's
  own ones without duplicating, and no dictionary configured behaves exactly as before this step); one
  full pipeline test each for C3 and C4 proving a free-text metric ("net sales") is answered correctly
  end to end through the real pipeline wiring, not just the splitter in isolation.
  Verified twice: full suite run twice back to back, 814 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched (this step never touches Postgres).
  Not yet done: gathering the fuller dictionary content itself (work still open, licences to check
  first), and the rest of work package 3 (documents in and out) is next.
- 2026-09-26 (later): Built steps 3.1 and 3.2, the document card and the file store door.
  Step 3.1 -- `offline/documents/document_card.py` (NEW) + `db/migrations/0003_add_documents_table.sql`.
  A person gives title, type, product/area, audience, effective date (or "not sure"), and an optional
  version label; the computer fills in id, upload date, kind (guessed from the file extension), and
  file name; page count is filled in later, once the file is actually processed. A missing title, a
  missing file name, or a type not on the pick list is refused with a plain message and nothing is
  half-saved. The pick list is a default, not fixed -- a caller can pass its own ("pick lists can differ
  per organisation"). Two documents sharing a title and type are treated as versions of each other;
  `current_documents` returns the one with the newest effective date per group. Edits go through the
  step 0.4 version-number guard, same as the entities table. Found and fixed a real gap while testing:
  the read functions (get/list/current) didn't create the table first, so an empty database crashed on
  a read with no writes yet -- fixed to self-heal on every access, same as create.
  Step 3.2 -- `offline/documents/file_store.py` (NEW). One door, `save`/`read`/`exists`, backed today by
  a plain folder (`data/uploads/`, kept out of git) and swappable for S3/Azure Blob later behind the
  same shape (`LocalFileStore` and a fake `InMemoryFileStore`, same tests run against both). Every save
  records a sha256 fingerprint; saving the exact same bytes again is recognized as a repeat rather than
  rewritten. Saves are atomic (write to a uniquely named temp file, then rename into place) so a reader
  never sees a half-written file.
  Found two real, genuine Windows-specific races while writing the concurrency test (two threads saving
  the same document id at once): (1) a reader's plain file open can transiently fail with a Windows
  sharing violation while another thread's rename is in flight -- something that never happens on
  POSIX, where rename is unconditionally atomic; (2) the rename itself can transiently fail the same
  way when two writers race for the same destination. Fixed both with a short, bounded retry (up to
  50 attempts, 10ms apart) before giving up for real. Across many dozens of repeated local runs the
  actual guarantee (content is always one of the two valid versions, never corrupted or partial) never
  once failed; a rare, low-frequency, environment-flavoured warning (most likely antivirus or the
  Windows search indexer briefly touching a freshly written temp file) can still print occasionally
  without failing the test, and is documented plainly in the test rather than hidden.
  36 new tests: 17 for the document card (defaults, "not sure" date, missing title/file name refused
  with nothing half-saved, an unknown type refused, a custom pick list honoured, get/list round trips,
  page count filled in later, two versions of a policy resolving to the newest as current, documents of
  different types never grouped as versions, and the version-conflict guard); 19 for the file store,
  most run against both the real folder and the fake in-memory store (save/read round trip, a repeat
  save recognized, a genuine new version not flagged as a repeat, two different documents never
  colliding, the real concurrency race, and local-store-specific checks for atomic writes, a clean
  failure with nothing half-saved, and creating its own root folder).
  Verified twice: full suite run twice back to back, 850 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched (neither step touches propositions).
  Not yet done: step 3.3 (the upload job tying the card, the file store, the lock, the ticket and the
  event bus together) and 3.4 (the upload screen) are next.
- 2026-09-26 (later): Built step 3.3, the upload job (decision 15). New `offline/documents/upload_job.py`
  ties together the ticket (step 0.4), the file store (3.2), the document card (3.1), the lock (0.4)
  and the event bus (0.5) into one real "upload a document" flow: check for a genuine repeat by content
  fingerprint (skip it, event says so) -> save the card and file -> `document.uploaded` -> take a lock
  scoped to this document's title+type (so two different documents never block each other, but two
  uploads of the *same* one queue safely) -> if replacing an earlier version, remove its facts and mark
  it superseded -> run the pluggable content step (`ingest_fn`) with up to 3 retries, a `document.retry`
  event on each failure and `document.failed` if all attempts are exhausted -> check the three stores
  agree (`check_stores_fn`) -> record the fact ids and `document.indexed`.
  Turning a file's bytes into actual searchable facts is deliberately left pluggable
  (`ingest_fn`/`remove_fn`/`check_stores_fn`): what a "fact" means for a policy or a form isn't defined
  anywhere in the codebase yet (that is non-finance NPCE, work package 4, still open), so this session
  built and tested the real orchestration around that seam rather than invent an extraction pipeline
  that doesn't exist.
  Found and fixed a real, significant bug while building this: `document_card.py`'s functions expected
  dict-shaped rows, but `infra.locks`/`infra.idempotency`/`infra.events` all expect plain tuple rows --
  and the upload job hands the very same one connection to both. That would have broken in real use,
  not just in a test. Fixed by having `document_card.py` open its own dict-row cursor per query,
  independent of whatever row factory the passed-in connection defaults to, so one connection now
  correctly serves every piece.
  Also found and finished fixing the Windows file-locking race from step 3.2: one more raw
  `read_bytes()` call (inside `save()`'s own "is this already saved" check) wasn't covered by the
  retry-on-transient-sharing-violation fix yet. Fixed by routing all three read call sites through the
  same retry helper; the concurrent-save test that used to occasionally print a benign warning ran
  clean 30 times in a row afterward.
  For a killed-and-retried job to resume the same document instead of creating a duplicate,
  `create_document` gained an optional explicit `doc_id` (the upload job derives it from the ticket)
  and is now idempotent on it (`ON CONFLICT (id) DO NOTHING`, always re-reads the current row) --
  existing callers that omit it are unaffected. `documents` also gained `fingerprint`, `indexed` and
  `fact_ids` columns (self-healed on an existing table, same pattern as steps 1.4/2.1), plus
  `mark_indexed`, `mark_superseded`, `find_completed_by_fingerprint`, `find_previous_completed_version`
  and `delete_document_row`. `FileStore` gained `delete`, on both the real and fake implementations.
  Honest gap: this function still runs the ingest step inline rather than truly in the background --
  the docstring says so plainly. A real deployment has a worker consume `document.uploaded` and call
  the same ingest step itself, so the API layer can answer at once; that "answers in about a second"
  guarantee belongs to that future API/worker wiring, not to this orchestration function, and is not
  claimed here.
  27 new tests (9 in test_document_card.py for the new idempotent-id/fingerprint/replace-lookup
  functions; 2 for file store delete; 11 in test_upload_job.py covering nearly every scenario in the
  plan: a small upload indexed and searchable, a genuine repeat skipped, a new version replacing the
  old facts and marking it superseded, delete removing a document from every store, a double click
  indexing once, a permanently failing ingest retrying then failing clearly with the right events, a
  transient failure retried and recovered, a killed-then-retried job resuming without a duplicate card,
  a failed three-store check refusing to mark indexed, and two real concurrency tests -- different
  documents uploading at once without interfering, and two uploads of the *same* document queuing
  safely behind the lock, both run repeatedly (10x) to confirm they are not flaky).
  Verified twice: full suite run twice back to back, 874 passed, 3 skipped both times, no regressions,
  no warnings. Production database re-checked: 21,913 facts, untouched.
  Not yet done: step 3.4 (the upload screen) is next, followed by work package 4 (the router).
- 2026-09-26 (later): Fixed the honest limitation flagged at the end of step 3.3: uploads now genuinely
  answer at once instead of doing the slow work inline. `offline/documents/upload_job.py` split into
  two halves: `submit_upload` (dedupe, save the card and the file, publish `document.uploaded`, return
  -- never touches the pluggable content step at all) and `run_ingest_step` (the slow half: take the
  lock, turn the file into facts, check the stores, record the result; safe to call more than once for
  the same document, since an already-indexed one is left alone). `submit_upload` fires `run_ingest_step`
  on a background thread with its own connection (`start_background_ingest`) when given a database
  address -- the exact same one-shot "background thread, its own connection" shape step 1.3 already
  uses for the startup index check. Proved this for real: a slow fake ingest step, `submit_upload`
  measured returning in well under a second, then polled until the background thread finished and the
  document showed as indexed.
  While rewiring this, found a second real bug: the lock helper (`infra.locks.acquire_lock`) fails
  immediately if another job holds the lock -- correct for its other callers, but wrong here, since
  "two uploads at once" is supposed to mean the second one waits its turn, not gets refused outright.
  Added a bounded wait-and-retry wrapper (`run_ingest_step` now polls for up to 30 seconds before
  giving up for real) rather than changing the shared lock primitive's own fail-fast behaviour, which
  other callers correctly rely on.
  Honest gap, now narrower: the background thread is still one-shot per upload, spawned directly by
  `submit_upload`, not a persistent worker that subscribes to `document.uploaded` once and keeps
  running for every future upload in its own process. That fuller, always-on worker is the real
  production shape and is still to come; what changed today is that the caller-facing promise ("you get
  an instant reply") is now actually true and tested, not just asserted in a comment.
  Rewrote all 14 upload-job tests around the two-phase shape (a small helper runs both phases back to
  back for the tests that want a deterministic result to assert on) and added one new test proving the
  background timing behaviour for real. All 14 (now 14, was 11) re-verified, including the two real
  concurrency tests run 10 times each for stability.
  Verified twice: full suite run twice back to back, 877 passed, 3 skipped both times, no regressions.
  (One run in between showed two unrelated failures from a transient network blip reaching Hugging Face
  for the real embedding/reranker model tests -- confirmed by re-running them alone straight after,
  both passed; not caused by anything changed today.) Production database re-checked: 21,913 facts,
  untouched.
- 2026-09-26 (later): Owner gave a real design for what a "fact" means for a non-finance document
  (a policy, form, or guide) -- the placeholder from step 3.3 is now filled in with a real, tested
  implementation, built to the owner's own specification: rule-block granularity (a whole clause,
  never a single sentence, since a rule is incomplete without its conditions and exceptions),
  heading-hierarchy citation, and a direct-answer-plus-verbatim-excerpt-plus-pinpoint-citation format
  that never claims more certainty than the source supports.
  `contracts/models.py` gained `PolicyFact` (fact_id, document_id, verbatim text, heading path,
  section number, antecedent/consequent/exceptions, page, source). Antecedent (who/when a rule applies)
  is deliberately left unbuilt -- reliably telling "who this applies to" apart from "what it requires"
  needs more than a keyword rule and is real future work, following the same "rules first, model only
  for what rules can't reach, needs its own yes" pattern already used for the metric labeler; it is not
  guessed at.
  `offline/documents/policy_chunker.py` (NEW): splits a document's text into rule blocks by numbered
  clauses ("4.2.1 ..."), named sections ("Section 4.2 - ..."), or plain headings, keeping the full
  heading hierarchy above each block (so "4 Accounts -> 4.2 Eligibility -> 4.2.1 Minors" survives as
  the block's own citation path, and returning to a shallower heading correctly drops the deeper ones).
  Reuses the existing `offline/parsers/layout_parser.py` PDF/text extraction rather than parsing files
  a second way. Known, stated simplification: a rule block never spans a page break yet.
  `offline/documents/rule_extractor.py` (NEW): splits a rule block into its consequent (the main rule)
  and exceptions (sentences starting with "however", "except", "unless", "excluding", "does not apply
  to", ...), by sentence, never a bare keyword search -- found and fixed a real bug in this while
  building it: a sentence like "This does not apply to..." wasn't being recognized because of the
  leading pronoun before the connector word; fixed by splitting into sentences first and matching each
  one's own start, rather than trying to find connectors mid-text with a fixed-width regex lookbehind.
  `offline/documents/policy_fact_store.py` (NEW): a Postgres store for policy facts, kept separate from
  the financial-numeric propositions table since the two fact shapes are genuinely different.
  `offline/documents/policy_answer.py` (NEW): the owner's exact requested format -- a direct answer
  (the fact's consequent), then the verbatim excerpt, then a pinpoint citation (document, one-indexed
  page, section number, and the full heading path) -- plus an honest out-of-scope message that states
  what a document does cover instead of ever inferring what it doesn't.
  `offline/documents/policy_ingestor.py` (NEW): the concrete `ingest_fn`/`remove_fn`/`check_stores_fn`
  that plug this whole thing into step 3.3's upload job -- proven for real, end to end, through the
  actual `submit_upload`/`run_ingest_step` functions, not just tested in isolation: a real (synthetic)
  policy document's bytes go in, get chunked, get their consequent/exceptions split out, get stored,
  and come back out through `render_policy_answer` in exactly the agreed two-part format with a correct
  pinpoint citation. Replacing a policy document also correctly removes every one of its old facts by
  id (checked they are genuinely gone, not just superseded) and writes the new ones; deleting a document
  removes its facts too.
  84 new tests across five files (12 chunker, 9 rule extractor, 8 fact store, 11 answer formatting, 6
  real end-to-end through the upload job) -- all real, none hand-waved; two real design bugs (the
  "This does not apply to" miss, and an earlier confused/nonsensical assertion caught and rewritten
  rather than left in) were found and fixed while writing these tests, not left for later.
  Honest limits, stated plainly rather than hidden: this has not been run against a real PDF policy
  document yet (no real papers are sourced for this yet, per the owner's own earlier decision -- only
  synthetic text so far); a rule block never spans a page break; antecedent extraction and true
  AI-written synthesis of the direct answer (versus reusing the extracted consequent verbatim) are both
  deferred, pending their own yes, the same as everywhere else new AI use has come up this session;
  and none of this is wired into the query side yet (a person asking a real question and this code
  answering it) -- that is the router, work package 4, still to come.
  Verified twice: full suite run twice back to back, 923 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched (none of this touches propositions).
- 2026-09-26 (later): Owner gave the yes for the antecedent-extraction fallback flagged as
  deliberately unbuilt in the policy work. Built as an eighth real call site, `antecedent_extraction_fallback`
  (registered optional in `config/schema.py`, so nothing existing needed it and nothing broke):
  `llm/schemas.py` gained `AntecedentExtractionOutput` (reasoning + an optional antecedent phrase --
  null is a correct "applies to everyone, no condition" answer, same as every other fallback's honest
  "nothing found" case); `llm/routing.py` registered its schema.
  `offline/documents/rule_extractor.py` gained `extract_antecedent_llm_fallback`: the model may only
  quote or closely paraphrase a condition the rule block already states, never add one it doesn't --
  enforced by code, not just asked for in the prompt: the returned phrase is only kept if at least half
  its real words actually appear in the source block, otherwise it is dropped and the antecedent stays
  None. `rule_block_to_fact` and `ingest_policy_document` both take an optional client; leaving it out
  costs nothing and behaves exactly as before this fallback existed.
  Two real bugs found while testing this (both in my own test code, not the feature): a grounding-check
  test used a phrase that trivially matched on stopwords like "a", inflating the overlap score past the
  threshold when it should have failed -- rewritten with phrases that share (or genuinely don't share)
  real words; and an end-to-end test's fake client asserted on text from only one of two real rule
  blocks in the sample document, failing on the other -- fixed to handle both.
  Live-tested for real, cheaply, the same way the metric-labelling fallback was: two real calls,
  gpt-4.1-mini-2025-04-14. "Applicants under the age of 18 years are permitted..." correctly returned
  "Applicants under the age of 18 years". "All customers receive a debit card..." returned "All
  customers" rather than null -- a defensible reading (it does name an antecedent, an unconditional
  one), grounded in the real words of the text either way, not a hallucination.
  26 new tests (18 in test_rule_extractor.py: accepts a grounded antecedent, rejects an invented one,
  a null antecedent is a correct answer, schema-invalid is dropped, the exact rule block text is sent,
  the pipeline behaves exactly as before with no client, a grounded phrase is accepted and an
  ungrounded one rejected) plus 2 in test_policy_ingestor.py proving it flows through the real upload
  job end to end and that leaving the client out changes nothing.
  Also found and fixed a real, separate test-quality bug while running the full suite: an earlier test
  for step 3.3's background-ingest fix used a fixed wall-clock threshold ("returns in under 1 second"),
  which failed once for real under normal system load (it took 1.42s) -- a fixed-time assertion is
  inherently fragile. Rewrote it to prove the same thing without any timing assumption at all: the slow
  ingest step is now held deliberately blocked by an event the test controls, so `submit_upload`
  returning at all, with the document still not indexed, is itself the proof it never waited -- run 15
  times afterward with no failures, including one run that took 2.5x longer than usual and still passed.
  Verified twice: full suite run twice back to back, 934 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched.
  Now starting step 3.4: the upload screen.
- 2026-09-26 (later): Built step 3.4, the upload screen -- and wired the whole upload story into the
  real, running API and a real page for the first time, not just tested in isolation.
  Backend (`api/server.py`): two new endpoints. `POST /api/documents` accepts a real file upload
  (multipart form: file, title, type, product/area, audience, effective date or left out for "not
  sure", version label, and a ticket the browser makes up once per attempt) and returns at once with a
  job id -- it never waits for the slow ingest step, using exactly the `submit_upload`/background-thread
  machinery built in step 3.3. `GET /api/documents/{id}/events` is the poller the screen calls every
  second or so to watch progress, built straight on `events_since` (a websocket/SSE connection was
  considered and set aside for this session; the event shape underneath is the same either way, so
  switching the transport later changes nothing about what is returned). `/api/info` now also reports
  the allowed document types, so the screen's own dropdown always matches the backend's real pick list
  instead of a second, hand-copied list that could drift out of sync.
  Found and fixed a real bug while wiring this: the new endpoints handed the ingest step's background
  thread the module-level `POSTGRES_DSN` constant directly, instead of the address the request's own
  documents connection was actually opened against. In production these are the same value, so nothing
  would have broken there -- but a test (or a future second environment) pointed at a different address
  would have silently leaked work onto the real database instead of its own. Fixed by giving `Runtime`
  its own `postgres_dsn` field and always reading it from there.
  Frontend (`frontend/`): a second tab, "Upload a document", next to the existing "Ask a question" one.
  The form checks required fields itself (title, type, a chosen file) and shows a message beside the
  exact field, before ever calling the server; a real double click is stopped outright on the browser
  side, and the same idempotency ticket protects against it again on the server side if a request is
  somehow sent twice anyway. Progress is polled and shown live in plain words, ending in a plain "done",
  "already there", or "failed". The existing color system (light/dark handled by CSS variables tied to
  the OS setting, already used everywhere else in the app) and the same responsive classes as the rest
  of the app are reused as-is, so phone-width layout and dark/light readability come for free rather
  than needing new work.
  Verified for real, twice over -- once through automated tests, once by hand against the actual
  running programs, not simulated:
  Automated: 21 new tests (12 for the two endpoints against a real test database and a real event bus;
  9 more once two genuine test-authoring mistakes were found and fixed while writing them -- FastAPI's
  own required-field check treats a truly empty form value as "missing" before our own code ever sees
  it, which one early test wrongly expected to reach our own error message; and an earlier version of a
  progress test only looked at the last batch of polled events instead of the whole history, missing an
  event that really did happen).
  By hand: started the real backend and the real frontend, uploaded an actual text file with a real
  policy in it through the browser-facing address (not directly at the backend), watched it come back
  "accepted" immediately, polled the real progress endpoint and saw it finish with two real facts
  chunked out, then uploaded the exact same file again under a different ticket and watched the real
  duplicate-detection correctly say "already there" and point back at the first upload -- proving the
  file's bytes survived the trip through the website's own server untouched. Fixed the event messages
  along the way once this showed them arriving blank ("" instead of a plain sentence) -- a real, small
  gap the automated tests alone had not caught, since they only ever checked the event *type*, never
  what a person would actually read.
  Frontend `npm run typecheck`, `npm run lint`, and `npm run build` all pass clean with the new pages.
  Verified twice: full backend suite run twice back to back, 943 passed, 3 skipped both times, no
  regressions. (One run in between showed two unrelated failures from the same kind of transient network
  blip as before, reaching Hugging Face for the real reranker model tests -- confirmed unrelated by
  re-running them alone straight after, all 5 passed.) Production database re-checked: 21,913 facts,
  untouched. Both manually started servers were stopped afterward; confirmed no process was left
  listening on their ports.
  This closes work package 3 (documents in and out). Work package 4 (the router) is next.
- 2026-09-26 (later): Started work package 4, the router. Built step 4.1 to the exact scope the owner's
  own suggested build order set out (KNOWN_ISSUES.md, "Q3 follow up": "router with the straight and
  money roads, then paper split, then fix the question, then try again, then step by step") -- the
  straight and money-split roads, and the 6-piece cap, only. Paper-split (4.3), fix-the-question (4.4),
  try-again (4.5) and step-by-step (4.6) are separate, later steps and are honestly not touched here;
  several of 4.1's own listed test rows in the plan belong to those later steps and are deferred with
  them, not silently skipped.
  New `online/routing/router.py`: `choose_road` classifies a question by counts and simple rules, never
  a model that guesses (decision 11) -- a whole-word check for "and", "both", "each", "compare",
  "versus"/"vs" triggers a split; no signal found is itself the "straight" rule, not a fallback guess.
  `cap_pieces` enforces decision 11's 6-piece limit on whatever the existing splitter produces, and
  says plainly when it had to cut some off, rather than silently dropping them. `describe_routing`
  builds the plain-words record step 4.7 will surface as "How it happened".
  Wired into `pipeline_c3.py` and `pipeline_c4.py`: both now classify the question and cap the split
  sub-queries for real (not just log it) -- the underlying retrieval and generation code paths
  (`filter_candidates`, `hybrid.retrieve`, `rewrite_to_ffts`, `generate_answer`) are untouched line for
  line, so this is additive, not a rewrite of anything already proven. `C3AnswerResult`/`C4AnswerResult`
  gained `routing_road`, `routing_reason`, `routing_capped` fields with safe defaults, so nothing
  existing broke.
  46 new tests: 20 for the router module itself (every trigger word, a false-positive check that "and"
  or "each" inside an unrelated word never falsely trips a split, the cap at exactly 6/7/0 pieces and
  with a custom limit, and the plain-words log for each case) plus 13 each in test_pipeline_c3.py and
  test_pipeline_c4.py proving the classification and the cap hold up through the real fan-out, not just
  in the router module alone -- including a real 7-metric question that only ever produces 6
  sub-queries.
  Two real test-authoring mistakes found and fixed while writing these: one test wrongly assumed "and"
  would be the reported trigger word in "Compare 3M and Adobe", when "Compare" is matched first (it
  appears earlier in the sentence) -- a valid, correct signal, not a bug; and a 7-piece cap test
  originally checked the number of real LLM-client calls made, not realizing that with an empty
  candidate store `rewrite_to_ffts`/`generate_answer` both skip the LLM call entirely on their own for
  *every* piece, capped or not -- so the real, meaningful proof capping happened is the sub-query count
  itself, fixed to check that instead.
  Honest note on the "old 82 answerable items -- not worse than before" test: not re-run live against
  the real 366-item benchmark this time, since doing so would spend real money on a change that is
  provably additive -- the retrieval/generation code paths were not touched, only new logging fields
  and a cap that is a no-op below 6 pieces (well above what any of the 366 items' known collision types
  need). A live re-run is still owed once the fuller router (4.4-4.6) changes real behaviour, not just
  before then.
  Verified twice: full suite run twice back to back, 969 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched.
  Not yet done: 4.2 (money split is already the existing splitter, nothing new to build there beyond
  what 4.1 already wired in) and 4.3 (paper split, non-finance NPCE) are next per the suggested order.
- 2026-09-26 (later): Built the rule-based half of step 4.3, paper split (non-finance NPCE). Did NOT
  build the model half yet: the plan itself calls for "a cheap, swappable model" (a new call site) to
  find look-alike groups in freer phrasing, and the project's standing rule is that a new call site
  needs the owner's own written reason and yes first, every time -- asked for separately, not assumed
  from "continue".
  New `online/routing/paper_splitter.py`: `detect_plain_comparison` recognizes the plain, explicit
  shape the plan's own example uses ("loan limit for savings vs current accounts" -> "savings accounts"
  / "current accounts"), no model at all, real and free. `find_grounded_phrases` is the safety check
  any model's output would have to pass before being trusted -- built and tested now, the same
  "classify/extract, never invent" rule every other fallback in this project follows, so the model
  piece can plug straight into it once approved. `split_by_look_alikes` swaps in one look-alike at a
  time and enforces the same shared 6-piece cap from step 4.1's router (imported from there, not
  redefined, so there is only ever one place that number lives).
  Found and fixed a real bug while testing the regex: it initially allowed up to four words on each
  side of "vs"/"versus"/"or", which greedily swallowed unrelated leading words ("loan limit for
  savings" instead of just "savings") -- because `re.search` finds the leftmost position where the
  *whole* pattern matches, not the shortest match. Fixed by constraining each side to a single word (or
  a hyphenated compound), which is what every real case actually needs and removes the ambiguity
  entirely.
  15 new tests: the plan's own example, "versus" spelled out, "or" as a comparison, a question that
  must not split staying one piece, the cap at six with a custom limit too, the phrases used are
  recorded for the log, the grounding check keeping a real phrase and rejecting an invented one
  case-insensitively and on whole words only, and a Nigerian-phrasing example working correctly since
  the rule only depends on the "X vs Y <shared word>" shape, not on any particular dialect.
  Verified twice: full suite run twice back to back, 984 passed, 3 skipped both times, no regressions.
  Production database re-checked: 21,913 facts, untouched.
  Stopped here to ask the owner for the yes on the model call site before going further with 4.3.
- 2026-09-26 (later): Owner gave the yes for the model half of step 4.3 ("yes, do it"). Built it as a
  ninth real call site, `paper_split_fallback` (registered optional in `config/schema.py`, so nothing
  existing needed it and nothing broke).
  `llm/schemas.py` gained `LookAlikeGroupOutput` (reasoning + a list of phrases; an empty list is a
  correct "nothing to split here" answer, same shape as every other fallback's honest "found nothing"
  case). `llm/routing.py` registered its schema.
  `online/routing/paper_splitter.py` gained two functions. `find_look_alike_groups_llm_fallback` sends
  the question to the model and only keeps whatever it proposes that survives `find_grounded_phrases` --
  a phrase the model invents that is not actually in the question's own words is thrown away, never
  trusted, the same rule every other fallback in this project follows; any bad or unreadable response is
  treated as "found nothing" rather than a crash. `split_paper_question` is the one function the rest of
  the router should call: it tries the free rule-based check first always, only asks the model if that
  finds nothing and a client was actually handed in, and returns nothing to split (not an error) if fewer
  than two grounded phrases turn up either way. Leaving `llm_client` out costs nothing and reproduces the
  rule-only behaviour from the previous step exactly.
  16 new tests in test_paper_splitter.py: the model's phrases are accepted when grounded, an invented one
  is rejected, an empty list is a correct answer, a schema-invalid response is dropped not guessed, the
  exact question text is what gets sent, and for the composed entry point -- the free rule wins alone
  with no client, nothing-to-split is a clean `None`, the model is never even called when the rules
  already succeeded, the model fallback fires and is used when the rules find nothing, and a single
  leftover grounded phrase (not two) is still correctly treated as nothing to split.
  Verified twice: full suite run twice back to back, 995 passed, 3 skipped both times, no regressions.
  Live-tested for real, cheaply, the same way as the other two fallbacks: three real calls,
  gpt-4.1-mini-2025-04-14. "Please what is the requirement for a domiciliary account as against a
  regular current account?" (a locally common phrasing the plain "vs" rule can't catch) correctly came
  back as two grounded phrases, "a domiciliary account" and "a regular current account". "What is the
  minimum balance for a savings account?" (only one thing asked about) correctly came back empty. One
  honest, worth-recording result: "...open an account for my child compared to opening one for myself?"
  -- the model answered "opening an account for my child" and "opening one for myself", but the question
  actually says "open" (not "opening") for the first one, so the grounding check correctly rejected that
  one phrase as not an exact match and only "opening one for myself" survived -- one piece, not two, so
  nothing got split. This is the strict-grounding safety check doing exactly its job: it would rather
  under-split (miss a real comparison because of a small wording mismatch) than trust a phrase that isn't
  verbatim in the question. A looser, close-paraphrase grounding check (like the antecedent fallback's
  "at least half the real words match") was considered but not built, since two single-word phrases
  ("savings"/"current") are short enough that a looser match risks matching the wrong nearby word instead
  -- an honest, deliberate limitation, not an oversight.
  Production database re-checked: 21,913 facts, untouched (this step doesn't touch propositions or
  policy facts, only the query-time router).
  Work package 4's paper-split step (4.3) is now fully done, both halves. Next per the owner's agreed
  order: 4.4, fix-the-question.
- 2026-09-26 (later): Built step 4.4, fix the question (decision 11). No new model call site needed --
  both checks the plan asks for (fix a typo, ask instead of guessing when something important is
  missing) are plain rules against the company list the query pipeline already carries, the same
  placeholder registry from step 2.1, so nothing new had to be wired in to know the real company names.
  New `online/routing/question_fixer.py`. `fix_typos` corrects a capitalized word that closely -- but
  not exactly -- matches a known company name (e.g. "Aple's" -> "Apple's"); the sentence-initial word is
  never touched (ordinary capitalization there means nothing), and very short words are skipped
  entirely, both to keep false corrections rare. `fix_question` is the one function callers use: it
  tries the typo fix first, and only asks "Which company is this question about?" if nothing was fixed,
  the question uses a pronoun ("it"/"they"/...) with nothing to point at, and no known company is named
  anywhere in it -- the plan's own "What was it in 2019?" example. A clear question is returned exactly
  as it came in.
  Wired into both `pipeline_c3.py` and `pipeline_c4.py`, right at the top of `answer_question`, before
  routing or splitting ever sees the question: a typo fix means the corrected text (not the typo) is
  what gets split and searched; a real missing-reference case short-circuits the whole question
  immediately, the same way an abstention already does, and genuinely touches none of the six call
  sites -- no split, no retrieval, no generation, proven in the tests below by an empty fake client that
  would fail loudly if anything tried to call it. `C3AnswerResult`/`C4AnswerResult` gained
  `question_fix_outcome` and `question_fixes` fields with safe defaults, so nothing existing broke.
  20 new tests: 16 for the module itself (a close typo fixed, a correct spelling left alone, the
  sentence-initial word never touched, an unrelated word never fuzzy-matched, lowercase words never
  considered, two typos in one question both fixed, a short word skipped even if close, a possessive
  reconstructed correctly, an empty company list still asks on a bare pronoun, a pronoun with the
  company already named in the same question is not treated as ambiguous, fixing always wins over
  asking) plus 2 each in test_pipeline_c3.py/test_pipeline_c4.py proving the typo fix reaches the real
  splitter and the missing-reference case reaches none of the real call sites at all.
  Verified twice: full suite run twice back to back, 1015 passed, 3 skipped both times, no regressions.
  Production database re-checked directly (`financial_rag_v2`, not the test database): 21,913
  propositions, exactly unchanged -- this step only affects how a question is prepared before routing,
  never touches stored data.
  Honest limitation, same trade-off any spell-checker makes: fuzzy typo-matching can occasionally "fix" a
  real but unfamiliar company name into a similarly-spelled known one, since it only requires being
  close enough (see the module's cutoff), not an exact match. Also honest and deliberately out of scope:
  the plan's "final check with the ORIGINAL question" (test matrix row under 4.1) is not this step's
  job and is not built yet -- nothing in the current pipeline re-checks a verified answer against the
  question exactly as the user first typed it; that is still owed, likely alongside 4.5/4.6 where a
  retry or a multi-step plan first makes the distinction matter.
  Next per the owner's agreed order: 4.5, try again with other names.
- 2026-09-26 (later): Built step 4.5, try again with other names (decision 5 and 11). No new model call
  site needed -- the alternate names it tries are the same placeholder registry (step 2.1) already used
  everywhere else, never a guess.
  Refactored `online/retrieval/metadata_filter.py` first, behaviour-preserving (all 9 of its existing
  real-Postgres tests still pass unchanged): pulled the "resolve which company string to filter on" and
  "run the actual query" logic out of `filter_candidates` into two small reusable pieces,
  `resolve_company` and `query_for_company`, so the retry module could reuse the exact same query logic
  with a different company string, instead of a second, drifting copy of it. `filter_candidates` itself
  now just calls both in sequence -- same behaviour, same signature, nothing that already used it needed
  to change.
  New `online/routing/retry_with_other_names.py`. `other_surface_forms` looks up the other names sharing
  the same placeholder in the registry (e.g. "3M" and "3M Company" are siblings). `filter_with_retry` is
  the one function callers use in place of `filter_candidates` directly: runs the normal lookup once; if
  it comes back with nothing at all, retries exactly once with the next known sibling name; a second
  empty result is an honest "not found", never a third try (decision 11's own one-retry cap). This also
  replaces the interim note step 1.4 left in `metadata_filter.py`'s own docstring ("the full try again
  with other names road doesn't exist yet").
  Wired into both `pipeline_c3.py` and `pipeline_c4.py`, replacing the plain `filter_candidates` call
  inside the per-sub-query loop. `C3AnswerResult`/`C4AnswerResult` gained `retry_attempted` and
  `retry_names_tried` fields with safe defaults, so nothing existing broke.
  25 new tests: 9 for the retry module itself against the real Postgres container (a non-empty first try
  never retries, an empty one retries with the sibling name and finds it -- the plan's own "weak result,
  other name works" case, a second empty result is an honest not-found with no third try, no known
  sibling means no retry attempted, and the NPCE-off passthrough case never retries either) plus 2 each
  in test_pipeline_c3.py/test_pipeline_c4.py proving it end to end through the real fan-out: a company
  stored under one name is still found when the registry only resolved a different sibling name, and a
  genuinely missing company still fails that one sub-query honestly rather than crashing or looping.
  These pipeline-level tests needed a new fake metadata store that actually filters by company (the
  existing shared fake in both test files ignores company entirely and returns everything, which was
  fine for wiring tests but couldn't prove a retry does anything real) -- added alongside the old one,
  which every other existing test still uses unchanged.
  Verified twice: full suite run twice back to back, 1028 passed, 3 skipped both times, no regressions.
  Production database re-checked directly (`financial_rag_v2`): 21,913 propositions, exactly unchanged.
  Honest, deliberate scope choice: "weak" here means empty (zero candidates) -- the same bar the metric
  filter's own existing fallback already uses. A looser bar ("fewer than N candidates") was considered
  and set aside since the plan gives no specific number, and treating a handful of correctly-filtered
  results as "too few" risks retrying cases that were never actually broken, diluting the "How it
  happened" log with retries that didn't need to happen.
  Next per the owner's agreed order: 4.6, step by step (the seventh model job) -- this one does need a
  new call site (a step planner), so the usual yes will be asked for separately before it's built.
- 2026-09-26 (later): Owner gave the yes for step 4.6 ("yes do that. continue"). Built the router side,
  the planner call site, and the real chained execution -- the whole step-by-step road, not just the
  planning half.
  The project's own written rule of exactly six call sites is changed, in writing, to seven, exactly as
  the plan calls for: `config/schema.py`'s `CALL_SITE_NAMES` gained `step_planner` as a REQUIRED call
  site (not optional like every other addition this session) -- every existing config, real and test,
  now needs one configured. Updated all 5 real experiment YAMLs (`config/experiments/*.yaml`) and every
  test fixture that hand-built a six-name call-site dict (found by grepping for `cove_verification`
  across the repo: test_pipeline_c0/c1/c2/c3/c4, test_pilot, test_full_build, test_routing -- 8 files in
  total). `test_config_schema.py`'s own fixture builds itself from `CALL_SITE_NAMES` already, so it
  needed no direct edit, just a comment update.
  `online/routing/router.py`: `Road` gained `"step_by_step"`, chosen by a new curated regex for
  superlative/ordinal phrasing that points at an entity not yet named ("the company with the highest
  revenue", "whichever segment had the largest loss") -- the same honest "a curated list, not
  exhaustive" trade-off the existing split-signal list already makes. Road order follows decision 11's
  own diagram exactly: split is checked first, step-dependency second, straight last -- a question with
  both signals still splits.
  New `llm/schemas.py` gained `StepPlanOutput` (reasoning + an ordered list of {step_id, question}
  items); registered in `llm/routing.py`.
  New `online/routing/step_planner.py`: `validate_step_plan` is the code-side check every proposed plan
  must pass before a single step runs -- step ids must be a plain 1..N sequence with no gaps or
  duplicates, no step may reference its own or a later step's answer (a loop, rejected outright, not
  patched), and the whole plan is capped at `MAX_STEPS` (chosen as 4 -- the plan itself only says "a
  small cap" without a number; kept smaller than the 6-piece split cap since each step is a full
  retrieval-and-generation pass, not a cheap sub-query slot). `plan_steps` calls the model and runs its
  answer through that check; any bad or unreadable response is "no usable plan", never a crash, the same
  safe-default rule every other fallback in this project follows. `substitute_step_answers` replaces
  "{step_N}" in a later step's question with that earlier step's answer text.
  Wired into both `pipeline_c3.py` and `pipeline_c4.py`: this is the one road that actually changes
  control flow (straight and split_money still just get logged -- the query splitter does the real
  splitting either way, unchanged from step 4.1). When the router picks step_by_step and a
  `step_planner_client` was given, each planned step runs as its own full, real call to
  `answer_question` (recursively -- the exact same function, reusing every other step already built:
  question-fixing, retry-with-other-names, CoVe, everything), with the previous steps' answers
  substituted in first. `step_planner_client` is always passed as `None` on these nested calls, by
  construction -- one level of step-by-step only, never a plan of plans, so there is no recursion depth
  to bound or guard separately. A step that comes back unanswered stops the whole question there with a
  plain message naming which step failed; the final answer, when every step succeeds, is the last step's
  own real answer, with a `step_plan` log (question asked and outcome, one row per step) added to the
  result for "How it happened" (step 4.7) to show later. `step_planner_client` is optional everywhere;
  omitting it means a step-by-step-classified question is answered the normal way instead -- the
  documented safe default every other optional call site in this project already uses.
  Wired for real into `api/server.py` too (not left as dead code): `step_planner` is a required call
  site so the live API's runtime always has a real client for it, no `if "..." in call_sites` optional
  check needed the way the antecedent fallback needed one.
  Did NOT wire it into `evaluation/run_c3_gate.py`/`run_c4_gate.py` -- an honest, deliberate scope
  choice, not an oversight: `step_planner_client` being optional means those gates keep behaving exactly
  as before (a step-by-step-classified item in the 366-item benchmark would just get answered straight,
  same as today), and wiring a real evaluation run of the new road is separate work for whenever the
  owner wants gate numbers that actually exercise it.
  46 new tests: 26 in test_step_planner.py (a two-step plan accepted in order, an empty plan rejected, a
  step referencing itself or a later step rejected as a loop, an earlier-step reference accepted, gaps/
  duplicates/out-of-order ids rejected, too many steps capped not rejected, exactly the cap not marked
  capped, substitution with one/many/missing references, and the model-calling entry point's own
  accept/reject/empty/schema-invalid cases) plus 6 in test_router.py (the step-by-step road triggers on
  both curated phrasings, split wins when both signals are present, a superlative with an already-named
  company stays straight, the reason names the triggering phrase, the plain-words log) plus 14 across
  test_pipeline_c3.py/test_pipeline_c4.py (a real two-step question -- through the full C4 fan-out
  including CoVe on both steps, not a shortcut -- a failing step stopping with a plain message and never
  reaching the next step, a loop plan rejected and falling back to the normal road, no client falling
  back unchanged, and too many steps capped while still answering with the kept ones).
  Verified twice, clean, no other process running alongside (a first attempt showed 4 transient
  real-model-download test failures in test_bge_embedder.py/test_reranker.py caused by a live-model
  script I ran at the same time colliding with the same shared model cache/HTTP client -- confirmed not
  a real regression by re-running those exact tests alone right after, all passed, then re-ran the whole
  suite twice more cleanly with nothing else running: 1062 passed, 3 skipped both times).
  Live-tested for real, cheaply: real gpt-4.1-mini-2025-04-14 calls against the actual step_planner
  call site. First finding, a real one: the model's first two live plans did NOT reliably use the exact
  "{step_1}" placeholder text asked for in the prompt -- it used a plain-English paraphrase like "the
  company identified in step 1" instead, which `validate_step_plan` would accept as a plan with nothing
  to substitute, silently losing the intended chaining (a real prompt-compliance gap, not a code bug).
  Fixed by rewriting SYSTEM_PROMPT to explicitly forbid paraphrasing it and adding one concrete worked
  example. Re-tested live twice more afterward, both times correctly using "{step_1}" -- fixed for real,
  not just in theory. This is now an honest, still-open risk to keep in mind rather than a solved
  problem: a model can drift off a textual convention like this one at any time since nothing in the
  schema itself can force it (there's no structured field for "which step this depends on" in
  `StepPlanStepItem` -- an honest design gap worth a future revisit if this recurs in practice).
  Also honest and deliberate: a "{step_N}" reference substitutes an earlier step's ENTIRE answer
  sentence, not an extracted name or value -- there's no field anywhere in this project that separates
  "the one fact this answer is about" from the sentence itself, and guessing one out of free text would
  be exactly the kind of invented certainty this project avoids elsewhere. The planner is told to phrase
  later steps so a whole sentence still reads naturally in place instead.
  Production database re-checked directly (`financial_rag_v2`): 21,913 propositions, exactly unchanged
  -- this step only affects query-time routing and execution, never stored data.
  Work package 4 (the router and the roads) is now fully done: 4.1 through 4.6. Only 4.7 ("How it
  happened", API and screen) remains in that package -- surfacing the routing_road/routing_reason/
  routing_capped/question_fix_outcome/retry_attempted/step_plan fields already built and returned by
  every step above, through the API response and the frontend, in plain words.
- 2026-09-27: Built step 4.7, "How it happened" (API and screen). Pure assembly -- no new decisions, only
  turning the fields 4.1-4.6 already built and returned into one plain-words section.
  A real gap found while reading the plan closely for this step, worth flagging plainly rather than
  quietly working around: decision 11's own diagram has a road this step's own "How" description
  mentions ("a several-versions note") that was never actually built anywhere across 4.1-4.6 -- "top
  results almost the same, differ in one detail -> ANSWER EACH, labelled (or ask)", with its own row in
  step 4.1's test table ("Two near-identical results | Both answered and labelled"). It has no step
  number of its own, so it seems to have been dropped between the plan being written and the steps being
  scoped, rather than deliberately deferred. Built 4.7 honestly around this gap rather than guessing at a
  fix: `several_versions_note` always reports `None` -- a real, visible "not built yet", never a
  fabricated feature. This is still open and needs an owner decision: build it as an unplanned 4.1
  addition, or accept the gap and move on.
  Two new fields added to `C3AnswerResult`/`C4AnswerResult`, populated at every existing return point in
  both pipelines: `question_understood` (the question actually used after step 4.4's typo fix, before
  any split or step plan) and `pieces` (the real-company-name, reverse-mapped text of each sub-query or
  step actually run -- empty when the question was answered straight).
  New `online/routing/how_it_happened.py`: `describe_how_it_happened` builds one `HowItHappened` record
  from a finished result -- road and reason, both questions, the pieces, the retry names, the steps, and
  `checked_against_original_question`. That last field is itself an honest, load-bearing distinction, not
  a formality: it's `True` on the straight/split roads (the abstention/verification check is built
  against the real top-level question there) and `False` when steps actually ran, since each step's own
  check is against that step's own re-phrased question, not the true original composite one -- decision
  11's "check at the end with the ORIGINAL question" promise is not actually kept end-to-end on the
  step-by-step road yet, and this field says so honestly instead of claiming otherwise. The one-sentence
  `summary` describes what actually happened by the real piece/step counts, not just the router's road
  label, since a rejected step plan (step 4.6's own safe-default fallback) still carries
  `routing_road == "step_by_step"` even though the ordinary path answered it.
  Wired into `api/server.py`: `AskResponse` gained a `how_it_happened` field (`HowItHappenedOut`,
  `StepOut`), built by a small `_build_how_it_happened` helper in `build_response` -- always populated,
  answered or abstained alike.
  Wired into the frontend: `frontend/src/lib/types.ts` gained `HowItHappened`/`Step` and the new field on
  `AskResponse`; a new `HowItHappenedCard` in `AnswerView.tsx` shows the plain-words summary always, plus
  the question as understood (only when a fix actually changed it), the pieces, the steps with each
  step's own answer, and the retry names, using the exact same styling/responsive classes as every other
  card on the page so phone-width and dark/light behaviour come for free. `npm run typecheck` and
  `npm run lint` both clean.
  40 new tests: 12 in test_how_it_happened.py (straight/split/capped-split summaries, retry names shown,
  abstain still shows the section, the needs-clarification and fixed-typo summaries, steps shown with the
  honest original-question flag turned off, a rejected step plan falling back to describing the real
  piece count, the several-versions note always None, both questions reported) plus 3 in
  test_api_server.py proving the field reaches the real API response, including when abstained and when
  steps ran, plus the existing pipeline test files' assertions all still pass unchanged with the two new
  fields populated alongside everything already tested.
  Verified twice, clean: full suite run three times total this step (two showed transient real-model
  download failures in test_bge_embedder.py/test_reranker.py -- confirmed not real regressions by
  re-running those exact tests alone right after both times, all passed; this environment's network to
  huggingface.co appears intermittently flaky, an environmental condition, not a code issue) --
  1077 passed, 3 skipped, twice in a row clean with nothing else running alongside.
  Production database re-checked directly (`financial_rag_v2`): 21,913 propositions, exactly unchanged.
  Work package 4 (the router and the roads, decision 11) is now fully done, 4.1 through 4.7, with one
  honest open item: the several-versions road itself, flagged above, waiting on the owner's decision.
- 2026-09-27 (later): Owner decided: build the several-versions road now ("build it now. record as
  well"). No new model call site needed -- a genuine collision here is a property of the retrieved
  evidence itself (two different numbers for the same period), checked deterministically, the same
  "code decides, model never guesses" rule the numeric-verbatim guard already follows.
  New `online/curation/near_identical.py`. `detect_near_identical_versions` looks at the top propositions
  actually retrieved for one sub-query: if two of them share the same period (fiscal year + quarter) but
  differ in BOTH basis (reported vs restated vs trailing-twelve-months) AND value, that is a real
  collision -- two facts that merely share a period, or that happen to report the same number twice, are
  correctly left alone. Deliberately scoped to `basis` only: decision 4's "unit fix" already normalizes
  scale and currency elsewhere, so a scale/currency mismatch here would be a data bug to fix upstream,
  not a legitimate second version of a fact the way a genuine restatement is.
  `answer_each_version` runs the existing FFF-rewrite + generate pass once per version, each seeing ONLY
  its own version's evidence -- the model never sees both facts at once, so it can never blend them or
  silently prefer one. Every resulting answer's metric is labelled with which version it is (e.g.
  "net_income (originally reported)" / "net_income (restated)"), and each version gets its own
  sub_query_id so `pair_facts` (online/curation/pairer.py) never lets one version's facts overwrite the
  other's in `ctx.fff_sets`.
  Wired into both `pipeline_c3.py` and `pipeline_c4.py`'s per-sub-query loop, right where the FFF-rewrite
  used to run unconditionally -- a collision now short-circuits into `answer_each_version` instead, and
  every existing sub-query still behaves exactly as before when there's nothing to flag.
  `C3AnswerResult`/`C4AnswerResult` gained `near_identical_detected`/`near_identical_detail` fields with
  safe defaults, so nothing existing broke. `online/routing/how_it_happened.py`'s `several_versions_note`
  (added a few hours earlier as an honest "not built yet" placeholder) now reports the real thing when it
  fires, in both the note and the one-sentence summary -- the API and screen already had the wiring for
  this from step 4.7, so no further API/frontend changes were needed beyond updating two stale code
  comments that had said "always null today".
  20 new tests: 10 in test_near_identical.py (a reported+restated pair is a real collision, same
  basis+same value is not, different basis but same value is not, a single proposition is never a
  collision, different periods are never compared, non-numeric propositions ignored, quarterly vs annual
  kept separate, groups carry the right propositions, both answers correctly labelled, and each version
  gets a distinct sub_query_id) plus 4 in test_how_it_happened.py (the note is None when nothing fired,
  the note and summary both report a real collision honestly) plus 4 each across
  test_pipeline_c3.py/test_pipeline_c4.py (a real reported/restated pair answered and labelled through
  the real fan-out -- with full CoVe verification of both versions in the C4 case -- and a sanity check
  that an ordinary single-version sub-query is left completely alone).
  Verified twice, clean: full suite run twice back to back with nothing else running alongside, 1091
  passed, 3 skipped both times. Frontend `npm run typecheck` clean.
  Production database re-checked directly (`financial_rag_v2`): 21,913 propositions, exactly unchanged.
  The gap flagged after step 4.7 is now closed. Work package 4 (the router and the roads, decision 11) is
  complete: 4.1 through 4.7, plus this once-missing piece of 4.1's own test table.
- 2026-10-10: Started work package 5 ("proving it"). Owner gave the yes for step 5.1's new call site
  ("yes, build"): this step genuinely needs a new place where the system talks to the AI, since every
  existing call site requires retrieved evidence to run at all, and this baseline deliberately has none.
  Built step 5.1, the plain AI baseline (decision 1) -- the model answers a question using only its own
  training knowledge, no retrieval, no facts, nothing grounded. This is the floor the real system (C0,
  C4+) gets compared against later, never an answer path a real user would see.
  New ninth real call site, `plain_ai_baseline`, registered optional in `config/schema.py` (not required
  like the seven C0-C4 call sites -- it only ever appears in its own dedicated config).
  `llm/schemas.py` gained `PlainAIOutput` (reasoning + answerable + answers + answer_text, the same shape
  as the generator's own output for scoring compatibility) and `llm/routing.py` registered its schema.
  New `online/pipeline_plain_ai.py`: deliberately has NO `bundle`/`embedder`/`reranker`/`registry`
  parameter at all, unlike every other pipeline in this project -- the plan's own "zero retrieval calls"
  test (step 5.1's test table) is satisfied by the function's own shape, not a mock that merely happens
  to go unused. One call, no retry (there's no evidence here to re-check an answer against, so the
  bounded-retry pattern every grounded call site uses wouldn't change anything). Any bad or unreadable
  response is a safe abstention, never a crash, same as every other fallback in this project. The
  model's own answer is trusted directly, with no numeric-verbatim guard -- the opposite of every other
  call site here, and the entire point: there is no evidence to check it against.
  New `evaluation/run_plain_ai_gate.py`, mirroring run_c0_gate.py's harness shape exactly (per-question
  record, checkpointing, spend-cap cost tracking, `summarize`) so results slot into the same comparison
  table as C0 and C4+. Deliberately kept its own local copy of the checkpoint/cost-tracking helpers
  rather than importing run_c0_gate.py's private ones -- every other gate file (run_c1/c2/c3/c4_gate.py)
  already follows that same "own copy, not a cross-module private import" convention, confirmed by
  reading all four before writing this one.
  New `config/baselines/plain_ai_baseline.yaml` -- deliberately NOT in `config/experiments/`, since it
  isn't one of the five C0-C4 cells and test_config_schema.py's "exactly five experiment yamls" check
  must stay untouched by it. All seven required call sites still present (six as unused
  `<pinned-version>` placeholders, matching c0_naive.yaml's own pattern for slots a given setup never
  reaches) plus the real `plain_ai_baseline` entry.
  29 new tests: 10 in test_pipeline_plain_ai.py (an answerable question returns the model's answer, the
  model saying it doesn't know is a correct abstention, answerable=true with no answers is still treated
  as an abstention, a schema-invalid response and a raising client are both safe abstentions not
  crashes, the model name and question are carried through and sent correctly, the function's own
  signature structurally has no retrieval parameters at all, and one test using the project's shared
  LLM-call-counting fixture -- not an ad hoc patch -- proving exactly one real call happens) plus 9 in
  test_run_plain_ai_gate.py (exact-match scoring, cost tracking, the spend cap, checkpoint round-trips,
  a checkpoint-resumed item never reaching build_llm_client at all, and summarize's accuracy/cost math)
  plus 1 in test_config_schema.py proving the new baseline config loads and validates for real.
  Verified: ran every new test file directly, all passed (10/10, 9/9, 1/1, plus the existing
  test_config_schema.py suite unaffected at 19/19). Also ran the FULL suite once for a sanity check on
  everything else: Docker Desktop was found to be off on this machine (an expected, documented
  possibility -- step 0.1's own "restarts by itself" design covers the box itself, not a laptop that was
  never told to start Docker Desktop at sign-in, already noted as a manual OS setting back in work
  package 0). This produced exactly the real-infrastructure-unavailable error
  (`infra.connections.BoxUnavailable: Postgres is not reachable right now`) on every Postgres-backed
  test, 250 of them, none in any file this step touched -- confirmed by searching the full run's output
  for every file this step added or changed and finding zero matches. 861 passed, 3 skipped, 250 errors,
  all Postgres-unreachable. Did not re-run the full suite a second time since Docker being off produces
  the identical result every time regardless of this step's own code; the genuinely new code (10 + 9 + 1
  = 20 tests, all needing no real infrastructure at all) was itself verified clean.
  Did NOT re-check the production database this time -- it isn't reachable right now either, for the
  same Docker reason, not because anything touched it. Nothing in this step could have: this is a new,
  separate pipeline with no database access of any kind, by its own design.
  Did NOT run a live, real-money model check against the real plain_ai_baseline call site this time, the
  way every other new call site got one -- that is a deliberate hold, not an oversight: a real check is
  far more useful once Docker is back up and the real 366-item gold data is reachable too, so it can be a
  genuine few-item preview of step 5.4 rather than an isolated, less meaningful ping.
  Next: get Docker Desktop running again, then continue with step 5.2 (the C0 and C4+ run configs for
  the three-setup comparison) -- or, if preferred, a first live check of this step's call site once
  Docker is back.
- 2026-10-17: Docker Desktop back up (confirmed with `docker ps` -- Postgres/Mongo/Redis all healthy).
  Re-checked the production database directly first: still exactly 21,913 facts, untouched over the
  gap. Owner asked to pick up the one open loose end inside step 2.2 (the free-text dictionary's thin
  content) before continuing work package 5.
  Expanded `offline/labeling/metric_labeler.py`'s `DEFAULT_METRIC_ALIASES` from 28 to 38 phrases across
  the existing 19 metrics -- standard financial English (GAAP/IFRS and common British/Nigerian usage:
  "turnover", "topline", "profit after tax", "trade payables/receivables", "trade creditors/debtors",
  "capital spending", "stock-in-trade", "operating profit", "pat margin"), not copied from any one
  glossary, and shown to the owner before being seeded live rather than silently expanded. This is the
  one shared dictionary both the rule-based labeler (`label_metrics`) and the free-text MongoDB/Redis
  lookup (step 2.2's infra) read from, so both improve from one change.
  Found and fixed three real collisions while writing the tests for the first draft, the same class of
  issue the module's own existing "non-GAAP also matches GAAP" test already documents: "operating profit
  margin" and "gross profit margin" each literally contain another metric's own phrase ("operating
  profit", "gross profit") and would have double-labelled; "sales revenue" literally contains "revenue"
  and would have too. All three dropped from the final list rather than patched around, with the reason
  recorded directly in the dictionary's own comment so a future addition doesn't repeat the same mistake.
  Also deliberately left out, for the same reason: bare "stock" for inventories (collides with equity
  stock), bare "receivables"/"payables"/"debtors"/"creditors" (too generic), and "EBIT" for
  operating_income (not always the same figure -- a real accounting difference, not just a naming one).
  13 new tests in test_metric_labeler.py (each new phrase labels correctly; three tests specifically
  proving the dropped collision candidates do NOT mislabel -- "sales revenue" labels as revenue only,
  "net profit margin" labels as net_profit_margin only not also net_income, "gross profit" labels as
  gross_profit only not also gross_margin; and a bare "stock" sentence about equity correctly stays
  unlabelled). All 35 tests in the file pass, including the 22 already there.
  Also ran test_dictionary.py (infra/dictionary.py's own 12 tests, against the real Mongo/Redis boxes):
  all pass unchanged -- the seed/lookup machinery itself needed no changes, only the content it seeds.
  Live-verified for real: re-seeded the actual MongoDB dictionary and reloaded the actual Redis copy --
  41 phrases now loaded (was 28) -- then looked up 11 of the new phrases directly ("turnover",
  "topline", "total sales", "profit after tax", "bottom line", "operating profit", "trade payables",
  "trade receivables", "capital spending", "stock-in-trade", "pat margin") and every one resolved to the
  correct metric through the real pipeline, not just the fakes.
  Verified twice: full suite run twice back to back with Docker up throughout, 1124 passed, 3 skipped
  both times, no regressions.
  Honest note, not yet decided: the 21,913 facts already in production were labelled under the OLD,
  28-phrase dictionary at ingestion time. Expanding the dictionary does not retroactively relabel them
  -- `offline/labeling/backfill_metrics.py` would need to be re-run against production for any of these
  21,913 facts to pick up a newly-recognized phrase (e.g. a fact whose text says "turnover" instead of
  "revenue"). Checked: still exactly 2,752 facts labelled (unchanged), confirming nothing was silently
  touched. Whether to re-run the backfill against production, and when, is the owner's call -- not done
  without asking, since it writes to the real 21,913-fact table.
  Step 2.2's dictionary-content loose end is now closed (content is real and reviewed, not exhaustive --
  more sources can always be added later, but nothing invented or rushed here either).
- 2026-10-17 (later): Owner said yes to re-running the backfill against production ("yes, do that now").
  Ran `offline/labeling/backfill_metrics.py` for real against `financial_rag_v2`, rules only
  (`llm_client=None`, the default) -- no model call, no cost, exactly the dictionary-driven relabelling
  the owner approved, not a separate spend decision.
  Checked the module's own 12 tests against the real test Postgres box first, all passing, before
  touching production.
  Real result: 21,913 facts in, 21,913 facts out (the fact count itself never changes, by the module's
  own design -- only the `metrics` field on existing rows can change). Labelled share rose from 2,752 to
  2,986 facts (237 rows changed this run) -- 234 more facts now correctly recognize a metric purely from
  the new dictionary phrases, at zero model cost. Re-ran it a second time immediately after: 0 facts
  changed, confirming the module's own "safe to run twice" promise for real, not just in the tests.
  Verified twice: full suite run twice back to back after the real backfill, 1124 passed, 3 skipped both
  times, no regressions. Production re-checked directly a final time: 21,913 facts, 2,986 now labelled.
  Step 2.2 is now fully closed, content gathered, reviewed, seeded, and backfilled into the real
  database.
- 2026-10-17 (later): Owner asked about a faster way to grow the dictionary than hand-drafting each
  phrase -- word2vec/GloVe vs a cheap LLM. Decided not to build either now; recorded here as a later
  optimization, not forgotten.
  The comparison, for whenever this is picked up: word2vec/GloVe give words that appear in SIMILAR
  SENTENCES, not words that mean the SAME THING -- "revenue" and "cost" sit close together in that kind
  of embedding space precisely because they show up in the same sentences constantly, which would
  suggest exactly the wrong pairing. They also won't reliably know finance-specific shorthand ("PAT") or
  regional terms ("turnover") unless those happened to appear often in whatever the embeddings were
  trained on. A cheap LLM, asked directly for common/regional synonyms of each of the 19 known metrics,
  would very likely do better with far less manual correction -- but it is still a new place something
  would talk to the AI model, even as a one-off offline list-building script, not a live call site, so
  it needs the owner's own specific yes first, the same standing rule as everywhere else. Not done now;
  owner said no, not yet, just record it.
- 2026-10-17 (later): Step 5.5 (non-finance test set) started. Owner asked for real sources to download
  -- searched the web rather than guess at URLs (the standing "never generate/guess URLs" rule), found
  real, verifiable links: four CBN (Central Bank of Nigeria) regulatory PDFs and two real bank
  account-opening terms documents. Owner downloaded four of these into
  `backend/data/plainBankPdfs/`: `CBN Consumer Protection Regulations.pdf` (39 pages), `Consumer
  Protection Framework (Final).pdf` (33 pages), `Draft Guidelines on Responsible Business Conduct.pdf`
  (11 pages), `BUSINESS BANKING REVISED Ts and Cs JUNE 17 2021.pdf` (5 pages, a real Standard Bank
  Nigeria business banking terms document).
  Checked all four directly with `pypdf` before trusting them (a PDF with no real text layer -- a
  scanned image with no OCR -- would silently produce nothing for the policy chunker to work with):
  every one has real, cleanly extractable text, and the three CBN documents are structured exactly the
  way the policy chunker expects -- numbered Parts, clauses, and sub-clauses ("PART TWO - FAIR TREATMENT
  OF CONSUMERS", "3.2.4 Promptly oblige consumers...").
  Not yet done: actually ingesting these four real documents through the real upload pipeline so they
  become searchable PolicyFacts (none of this is in the database yet -- these are just files on disk
  right now), and gathering the 40-60 real questions, their gold answers, and a few real Nigerian-phrasing
  examples from the owner, none of which exist yet either.
- 2026-10-17 (later): Owner gave the yes to ingest the four real documents with the antecedent-extraction
  fallback on ("with that AI step on (small real cost, better results), now"). Ran them through the real
  production upload path (submit_upload + run_ingest_step, the same machinery a real browser upload
  uses, not a shortcut) against `financial_rag_v2`.
  Dry-run first: the chunker produced 445 real rule blocks across the 4 documents (218 + 136 + 62 + 29).
  Real cost estimated beforehand from that real count (~$0.10-0.15 at gpt-4.1-mini rates for ~445 short
  calls) before spending anything.
  Real result: all 4 documents marked indexed; 447 policy facts total (445 new + the 2 from an earlier
  test fixture); 151 facts (about a third) got a real, model-found antecedent, the rest honestly stayed
  None rather than guessed; financial propositions unchanged at 21,913.
  Spot-checked the actual content afterward, not just the counts -- and found a real, significant
  problem: across all four documents, a meaningful share of the 445 facts were junk -- stray page
  numbers stored as their own "fact" (`'2'`, `'3'`, `'4'`), sentence fragments with no real heading
  context, and on the Standard Bank document specifically, text with two columns interleaved into one
  garbled line (e.g. "balance requirements for my/our account(s) for or the charges relating to
  17.Commission and charges shall be levied in a"). Reported this honestly rather than treat the
  ingestion as "done" just because it ran without crashing -- a test set built on this data would have
  been testing garbage text, not real policy rules.
  Root-caused with the owner: `offline/documents/policy_chunker.py`'s heading detector matches on
  NUMBERING patterns in the text itself (e.g. "4.2.1 Title"), which is exactly as easily matched by a
  lone page number as by a real heading; and each page was chunked in total isolation, so a rule
  spanning a page break got cut into two incomplete pieces. The garbled text itself traced one level
  deeper, into the shared `offline/parsers/layout_parser.py`: `page.extract_text()` reads a page by
  vertical position only, with no awareness that two side-by-side columns exist, so it interleaves them.
  Owner proposed the real fix directly: stop using numbers as the heading signal at all -- use visual
  cues instead (bold, font size) -- and let a rule block continue onto the next page instead of always
  resetting. Owner also asked whether a technique called "late chunking" (an embeddings technique that
  keeps a whole document's context inside each chunk's vector by embedding the full document before
  slicing) would help. Explained why it would not: late chunking operates entirely downstream, on
  embeddings for search ranking -- it cannot fix text that was extracted in the wrong order, and it does
  not decide where a chunk's boundary is in the first place, which are the two things actually broken
  here. Agreed: set late chunking aside, fix the two real root causes directly.
  Before writing any code, confirmed with the owner that `pdfplumber` (already in use) does NOT provide
  multi-column reading order on its own -- checked directly: its own `layout=True` text mode still reads
  top-to-bottom in original order, just with whitespace padding, so the same two columns still
  interleave. It does give per-word position and font/size data, which is what the real fix is built on.
  Built the first half: `offline/parsers/layout_parser.py` (the module shared with the financial
  pipeline -- handled carefully given 21,913 real facts already depend on it being correct).
  New `Line` dataclass (text + vertical position + font size + bold flag) and a new `ParsedPage.lines`
  field, additive only -- the existing `.text` field financial extraction depends on is completely
  unchanged for the common, single-column case, which still takes the exact same single
  `page.extract_text()` call as before this step.
  New column-split detection, calibrated against the real documents rather than guessed, through several
  real wrong turns worth recording honestly: a first attempt (biggest gap between individual words' raw
  start positions) failed on the real document -- its real gutter was only the 4th-widest gap, not the
  widest, because a word at the end of one column's line can start almost anywhere, polluting the
  comparison. A second attempt (a histogram of word-start positions merged across the whole page) also
  failed -- single-column pages have large "empty" stretches too, purely from how individual lines
  happen to wrap, with no column involved at all; tested directly against a real single-column page and
  it produced gaps up to 85pt wide with nothing meaningful behind them. The working version instead
  checks, per 5pt band across the page, what share of actual LINES (not raw word positions) have any
  word crossing that band -- a real gutter is empty for nearly every line, not just empty in the
  aggregate word count. Two more real false positives turned up once that worked: a genuine ruled table
  (Annexure D) produced a gap just as real as a true column break, fixed by skipping column-detection
  entirely on any page pdfplumber's own table extractor already resolved; and a table of contents
  (title, then a single trailing page number) produced a centred gap exactly like a real second column,
  fixed by requiring each side to have several lines of genuine multi-word prose, not just a trailing
  token. Final check also requires the gutter to sit roughly in the middle of the page (30%-70%), since a
  real two-column layout has two similarly-sized columns -- an indent after a short list number (another
  real false positive found) sits nowhere near the middle and is excluded by this alone.
  20 tests in test_layout_parser.py (all 13 existing ones still pass unchanged, plus 7 new: a genuine
  two-column layout detected, an ordinary single-column page never split, a page with a real table never
  split, a table of contents never split, an off-centre gap never treated as a column, too few words
  never split, and reading order coming out left-column-then-right-column correctly).
  Verified against the real documents directly, not just synthetic tests: the previously-garbled
  Standard Bank page now reads in correct order end to end; a full sweep of every page across all 4 real
  documents found splits only on the 4 pages that are genuinely two-column, and correctly found none
  anywhere else, including the exact table and table-of-contents pages that broke the first two attempts.
  Not yet done: `offline/documents/policy_chunker.py` itself still needs the matching rewrite (visual
  heading detection instead of numbering, and letting a rule block span a page break) -- `.lines` exists
  now but nothing reads it yet. The 445 facts already in production from the flawed run have not been
  touched yet either; they will need removing and re-ingesting once the chunker side is done, which means
  a second real round of the antecedent-extraction call, a cost the owner should be told about again
  before it happens even though it was already approved once for the first (flawed) run.
- 2026-10-17 (later): Finished the second half: `offline/documents/policy_chunker.py` itself, rewritten
  to match the owner's own agreed plan -- heading detection by how a line LOOKS (bold, or a font size
  meaningfully bigger than the document's own body text), never by matching a number in the line's own
  text, and a rule block now carries straight across a page break instead of being cut off at every
  page boundary.
  Public shape kept stable on purpose: `chunk_document_into_rule_blocks(parsed: ParsedDocument)` is the
  only function `offline/documents/policy_ingestor.py` ever called, and still takes the same input and
  returns the same `RuleBlockDraft` shape `offline/documents/rule_extractor.py` already depends on --
  nothing downstream needed to change. The old per-page, number-driven internals were fully replaced,
  not patched around.
  A number in a heading's own text is still read -- just no longer as the admission test, only to help
  decide DEPTH (how nested a heading is) once something has already qualified as a heading by how it
  looks. No number present still means depth 1, the same simplification unnumbered headings already had
  before this step (never told apart by depth) -- not a new limitation, carried over honestly.
  Two more real, concrete bugs found and fixed by actually reading the real output, not just trusting
  the block count:
  - A table of contents (which repeats the real headings almost verbatim, each followed by a dot-leader
    and a page number -- "1. OBJECTIVES.......... 4") was being chunked as if it were real rule content.
    Fixed by dropping any line containing a dot-leader (3+ dots, or the real ellipsis character a broken
    one sometimes decodes to) from a block's body outright, rather than trusting it as real text.
  - A single large decorative glyph (a bullet-like "o" rendered at 2.5x the document's own body-text
    size) satisfied the size-based heading check and was mistaken for a heading titled "o". Fixed with a
    minimum heading length (3 characters) -- a real heading is never just one decorative character,
    however big it happens to be rendered.
  Also carried over, deliberately unchanged, the original junk-short-body filter from the first half of
  this step (a flushed block with no real letters, under 15 characters, is dropped, not stored as a
  fact) -- still catches stray page-number footers that leak into a block's body rather than becoming
  their own heading.
  Honest, deliberately un-fixed limitation found and left as-is: a few real numbered sub-clauses (found
  in the CBN Regulations' own "1.1 From unfair and exploitative practices... 1.2 From unethical...")
  are bold enumerated list items whose "heading" text is actually the start of a sentence that continues
  as the block's body -- visually indistinguishable from a true heading without actually reading and
  understanding the sentence, which no rule-based check can do. The content itself is not lost or
  garbled, just split at a slightly early boundary (the heading captures the sentence's opening clause,
  the body captures its tail). Not fixed now: doing so properly would need a model to judge, which is
  new scope beyond what was agreed, not a quick rule fix -- flagged honestly rather than either silently
  shipped as "perfect" or used as an excuse to add a new call site without asking first.
  20 tests in test_policy_chunker.py, entirely rewritten (the old 13 tested plain-text/numbering
  behaviour that no longer exists) to build synthetic `Line` objects directly: bold and bigger-font
  headings recognized, a bold-but-too-long line treated as body not a heading, a lone page number never
  a heading however it is styled, a single decorative glyph never a heading however big, a table-of-
  contents dot-leader entry dropped, a stray short numeric fragment dropped, the existing hierarchy/
  sibling/pop-on-shallower-heading behaviour all still correct, a rule block now genuinely spanning a
  page break (and still citing the page its heading started on, not where it ends), a new heading on the
  next page still closing the previous block correctly, full document-level chunking through the real
  `ParsedDocument` shape, and the `.txt` fallback (no font information exists there at all) still using
  the original text-pattern approach unchanged.
  Verified against the real documents directly after each fix, not just the synthetic tests: block
  counts dropped from the flawed run's 445 to 192 clean blocks (81->76, 136->86, 62->14, 29->16 after the
  glyph fix) with zero junk, zero TOC artifacts, and zero bad single-character section titles, confirmed
  by scanning every block's actual text and section path, not just counting them.
  Verified twice: full suite run twice back to back after both halves of this step (layout_parser.py and
  policy_chunker.py together), 1139 passed, 3 skipped both times, no regressions anywhere, including the
  financial path that depends on the same shared parser module.
  Owner then said yes to the real, second clean-up-and-reingest ("go ahead"). Removed all 445 facts and
  all 4 document rows from the flawed first run, then re-ingested the same 4 real PDFs through the same
  real production upload path, antecedent-extraction on again -- a second real but smaller spend
  (~192 short calls this time, not ~445, since the fixed chunker produces fewer, cleaner, more complete
  blocks).
  Real result, verified directly against the database: 194 policy facts total (192 new + the 2 from the
  unrelated, pre-existing sample_policy.txt test fixture, left alone); 105 facts (54%, a higher real
  share than the flawed run's 34%, since each block is now more complete and gives the model more real
  context to find an antecedent from) got a real model-found antecedent; zero junk facts; zero dot-
  leader/TOC-looking facts; financial propositions unchanged at 21,913.
  Verified twice more: full suite run twice back to back after the real re-ingestion itself, 1139
  passed, 3 skipped both times.
  Step 5.5's document side is now genuinely done: 4 real documents, cleanly chunked, really ingested,
  antecedents really extracted where the model could find one honestly. Still outstanding for step 5.5
  at that point: the 40-60 real test questions, their gold answers, and a few real Nigerian-phrasing
  examples.
- 2026-10-17 (later): Owner changed direction on who writes the test questions: "create the file,
  use standard phrasing, verification_status should be verified, go over it 3 times separately." So
  this one file (standard phrasing only, no Nigerian examples yet) was written by me, not supplied by
  the owner -- a deliberate, explicit exception to this project's usual "never invent eval data" rule,
  made because every question/answer/excerpt is grounded in real text already sitting in the real
  production database, not invented content.
  Built `evaluation/data/nonfinance_policy_test_set.jsonl`, 46 questions (46, inside the owner's
  40-60 range), fields: test_id, question, phrasing_style, expected_behavior, document_title,
  gold_answer_text, gold_excerpt, gold_citation, verification_status, notes. phrasing_style is
  "standard" throughout (Nigerian phrasing deferred, per the owner's own instruction for this file).
  verification_status is "verified" throughout, per instruction. expected_behavior mix: 28 "straight"
  (single-fact lookups), 10 "must_not_split" (multi-clause questions a router might wrongly split, but
  are each answered by one single real fact), 8 "must_split" (questions that genuinely need two facts
  from two different documents, e.g. comparing the Guidelines' and the Regulations' debt-collection or
  sanctions wording). Spread across all 4 real documents (7 to 18 questions each, matching each
  document's real share of the 192 facts). For "must_split" items, document_title/gold_excerpt/
  gold_citation are 2-item parallel arrays instead of single strings, since the question needs both
  documents.
  Before writing any question, read the real text of (effectively) all 192 real facts straight from
  `facts_condensed.json`, not from memory of an earlier summary -- the earlier conversation's summary
  only described topics, not exact wording, and exact wording is what gold_excerpt needs.
  Ran 3 separate, independent review passes before reporting back, as instructed:
  1. Programmatic verbatim check -- wrote a throwaway script, queried the real `policy_facts.data->>
     'text'` column for every fact_id named in each item's notes, and asserted every gold_excerpt is
     an exact Python substring of the real stored text. Found and fixed one real error this way: item
     nonfin_045 (CBN Consumer Protection Regulations, Objectives) had cited the wrong one of two
     neighbouring facts (1.1 "the consumers;" instead of 1.2 "confidence in the use of financial
     products and services;") -- a plain copy mistake while drafting, not a chunker bug. Re-ran after
     the fix: all 46 passed.
  2. Logical/coherence re-read -- for the trickiest cross-document and multi-clause items, re-fetched
     the full (non-truncated) real stored text directly from the database and checked every claim in
     gold_answer_text against it word for word, not just against the 300-char condensed dump used
     while drafting (which had silently truncated some of the real sentences, e.g. "toll-free" only
     appears after the condensed dump's cut-off point for one Framework complaints-channel fact).
  3. Structural/coverage check -- confirmed all 46 lines are valid JSON, no duplicate test_id or
     question text, all 10 required fields present on every item, verification_status and
     phrasing_style correct on all 46, the must_split items are structured as matched 2-item arrays,
     and the expected_behavior/document coverage matches the plan above.
  Not done yet: the Nigerian-phrasing examples (explicitly deferred -- the owner asked for standard
  phrasing in this file only), and the owner's own promised check of the file ("when I have the time,
  I will also check it").

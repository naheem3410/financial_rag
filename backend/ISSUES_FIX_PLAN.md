# Plan to fix and build everything we decided

Written: 2026-09-20. Master's final date: **December 1, 2026** (72 days away).
Source of every decision: `KNOWN_ISSUES.md` (same folder). This file says **how** and **when**.
Nothing in this plan is built yet. It is a plan only.

How to read it: every step has the same parts.

- **Covers**: which issue or decision it fixes.
- **Why**: the reason, in plain words.
- **How**: the small steps, in order.
- **Tests**: every situation we will check, and what "pass" means.
- **Done when**: the simple test that says the step is finished.

Words like "lock", "version number" and "idempotency key" are explained in the glossary at the end.

---

## 1. The goal in plain words

1. Make the question-answering engine **faster, cleaner and easier to trust**.
2. Let it handle **any question**, not only money questions.
3. Let people **upload papers** and have them searchable, safely.
4. **Prove** it with a fair test for your Master's, and write it up before December 1.

---

## 2. The big picture: two lanes

Your Master's must be finished first. So the work runs in two lanes.

```
 TODAY (Sep 20) ------------------------------------------------> DEC 1 (due)

 LANE A  (Master's. MUST finish)
 [1 Safe      [2 Fix the   [3 Dictionary  [4 Plain AI +  [5 Pilot   [6 Full    [7 New    [8 Write
  ground]  ->  answers]  -> + company  -> naive RAG  -> of 20  -> test    -> router  -> the
                             list]         setups]                 runs]      run]      results]

 LANE B  (platform. A bonus. May finish AFTER Dec 1)
        [Document card  [Upload job      [Paper      [Fix the question   [Step by
         + file store]-> + upload screen]-> split]  -> + try again]      -> step]
```

**The rule between the lanes:** Lane A always wins. If time runs short, Lane B waits.
Lane B is ordered by value, so we can stop at any point and still have something useful.

---

## 3. What must come before what

```
                    +--------------------------+
                    | 0  Safe ground           |  boxes restart, no hanging,
                    |    (foundations)         |  database change scripts,
                    +------------+-------------+  locks, tickets, events
                                 |
        +------------------------+-------------------------+
        |                                                  |
        v                                                  v
 +--------------+                                  +---------------+
 | 1 Fix the    |                                  | 5.3 Index the |  (starts in week 1,
 |   answers    |                                  |   10 filings  |   runs in the background)
 | unit, saved  |                                  +-------+-------+
 | vectors,     |                                          |
 | saved word   |                                          |
 | list, metric |                                          |
 | filter       |                                          |
 +------+-------+                                          |
        |                                                  |
        v                                                  v
 +--------------+     +--------------------+      +-------------------+
 | 2 Company    | --> | 5.1 Plain AI setup | ---> | 5.4 Pilot of 20,  |
 |  list +      |     |  (naive RAG = C0)  |      |  then the full    |
 |  dictionary  |     +--------------------+      |  366 runs         |
 +------+-------+                                 +---------+---------+
        |                                                   |
        v                                                   v
 +--------------+                                  +-------------------+
 | 4.1 Router   | -------------------------------> | C4+ run, then     |
 | (money roads)|                                  | write the results |
 +--------------+                                  +-------------------+

 LANE B hangs off "Safe ground" and the "Dictionary":
 3 Documents in and out --> 4.3 Paper split --> 4.4 Fix question --> 4.5 Try again --> 4.6 Step by step
```

---

## 4. Calendar, week by week

Weeks start on Monday. "CP" means checkpoint: it must be true before we move on.

**CHANGED 2026-09-25 (owner):** all of the fixes below (work packages 0 to 4) are built and tested
first, using the 5 filings and 21,913 facts we already have. Indexing the 10 missing filings (step
5.3) is done only **after** every known issue is fixed, because it is the one step that spends real
OpenAI money, and there is no need to spend it before the fixes are proven. This pushes the pilot and
full runs later, which is fine because the date check below still leaves buffer time.

| Week | Dates | Lane A (Master's) | Lane B (platform) | Checkpoint |
|---|---|---|---|---|
| 1 | Sep 25 to 27 | 0.1 boxes restart, 0.2 time limits, 0.3 database scripts, 1.1 unit fix | none | CP1: boxes restart by themselves, nothing hangs, unit fix in |
| 2 | Sep 28 to Oct 4 | 1.2 saved vectors, 1.3 saved word list, 0.4 locks and tickets, 0.5 events | none | CP2: answers much faster, three stores match (on the 5 filings we have) |
| 3 | Oct 5 to 11 | 1.4 metric label and filter, 2.1 company list | 3.1 document card | CP3: metric filter works with its fallback |
| 4 | Oct 12 to 18 | 2.2 dictionary, 5.1 plain AI, 5.2 naive RAG setup and C4+ setup, 4.1 router, 4.2, 4.7 (server side) | 3.2 file store | CP4: every setup and the router run correctly on the 5 filings we have |
| 5 | Oct 19 to 25 | **All known issues done. Start 5.3: index the 10 missing filings** (background, real cost, we ask you first) | 3.3 upload job (start) | CP5: all fixes proven; indexing under way |
| 6 | Oct 26 to Nov 1 | Finish 5.3, then 5.4 test runner and the **pilot of 20** | 3.3 finish, 3.4 upload screen | CP6: 366 of 366 items have their papers; pilot reviewed, go or no go |
| 7 | Nov 2 to 8 | **Full runs** (in the background) | 4.3 paper split, 5.5 test set | CP7: runs moving, first results table |
| 8 | Nov 9 to 15 | Full runs finish, results tables and figures | 4.4, 4.5 (light) | CP8: all numbers final |
| 9 | Nov 16 to 22 | **Write the results chapter**, 6.3 refresh the walkthroughs | 4.6 only if time | CP9: draft chapter done |
| 10 | Nov 23 to 29 | Supervisor review and fixes. **Code freeze Nov 25.** | none | CP10: chapter final |
| | Nov 30 to Dec 1 | Final checks, hand in | none | Done |

Housekeeping items (6.1 pointer file, 6.2 commit) happen when you say so.

**Honest note:** this calendar assumes about 15 seconds per question after the fixes, and about
3 test setups (plain AI, naive RAG, C4+). Doing all fixes before indexing costs a little calendar
time up front but avoids re-testing anything against 15 filings twice. The pilot in week 6 tells us
if the 15-second estimate is true. If it is not, we shorten Lane B, never Lane A.

---

## 5. Rules for every step (the "done" gate)

A step is **not done** until all of these are true:

1. **All old tests still pass.** Today that is 570 passed and 3 skipped. No step may break them.
2. **New tests for the step pass.** Every situation in its Tests table has a test.
3. **Quick tests need no boxes.** Slow tests that need the real database, MongoDB or Redis are marked
   `integration` and run on their own.
4. **Comments** in new code are at most 2 lines, written like a person, never pointing at a spec, a
   rules file or a chat.
5. **The plain-words rule:** anything the person sees uses simple words.
6. **Speed rules:** heavy work runs in the background and reports by events. Nothing is rebuilt at startup.
7. **Shared-data rules:** anything that writes shared data uses a lock or a version number, and anything
   that can be repeated carries a ticket (idempotency key).
8. **The tracker is updated** (`KNOWN_ISSUES.md`): what changed, and what the test showed.
9. **No commit and no push** until you say so.
10. **Real model calls in tests** are avoided. Tests use fake model clients, so they are free and fast.
    Only a few named checks use the real model, and they say so.

---

## 6. Work package 0: Safe ground

Everything else stands on this. It is small, and it must come first.

### 0.1 The database box restarts by itself
**Covers:** Known issue 3, Decision 16.
**Why:** The database box (Postgres) stops when the computer restarts, and then every question fails.
**How:**
1. Add one compose file (`docker-compose.yml`, NEW) that starts Postgres, MongoDB and Redis, each with "restart unless stopped".
2. Keep the same Postgres name, password and data folder, so the 21,913 facts stay where they are.
3. Turn on "start Docker Desktop when I sign in" (a setting on your computer, we tell you where).
4. Write one short "how to start" note in the backend readme.

| Test | Pass means |
|---|---|
| Restart the box on purpose | It comes back alone within 30 seconds |
| Count facts before and after the move | Still 21,913 |
| Start all three boxes from the compose file | All three say "healthy" |
| Start with the data folder missing | A clear message, and nothing is created by mistake |

**Done when:** you restart the computer and the health page says "ok" without you doing anything.

### 0.2 Nothing hangs: a time limit on every connection
**Covers:** Known issue 3, Decision 16.
**Why:** If the database is down, today the app can wait a very long time and give no message.
**How:**
1. One shared "connect" function for Postgres, MongoDB and Redis, with a limit of about 10 seconds.
2. If it fails, say in plain words which box is down and how to start it.
3. The API, the loader, the tests and the scripts all use this one function.

| Test | Pass means |
|---|---|
| Postgres is off, ask a question | Clear message in under 12 seconds, no hang |
| MongoDB is off | Same, and it names MongoDB |
| Redis is off | Same, and it names Redis |
| Box comes back after a failure | The next question works without restarting the app |
| Grep the code for direct connects | None left outside the shared function |

**Done when:** you stop each box in turn and always get a clear message fast.

### 0.3 Database changes go through small numbered scripts
**Covers:** Planning answer "database changes via numbered scripts".
**Why:** New columns (metric label, documents table, entities table) must not break the 21,913 facts.
**How:**
1. Folder `db/migrations/` (NEW) with files `001_...`, `002_...` in order. A small runner remembers which ones ran.
2. The runner makes a **backup first**, and every script is **safe to run twice**.
3. After each script it runs count checks (for example, still 21,913 facts).
4. The metric label fill (1.4) is its own separate script.

```
  run scripts
      |
      v
  backup made?  --no--> stop
      | yes
      v
  already ran?  --yes--> skip it
      | no
      v
  run inside one transaction
      |
      v
  counts still right? --no--> undo, tell you
      | yes
      v
  write "ran" in the log
```

| Test | Pass means |
|---|---|
| Run twice | Second run changes nothing |
| Script fails half way | Everything is undone, counts unchanged |
| Backup missing or empty | Runner refuses to start |
| Two runners at the same time | Second one waits or stops, no double change |
| Facts count after every script | 21,913 |

**Done when:** an empty test database and the real one both reach the same final shape.

### 0.4 Locks, version numbers and tickets (shared helpers)
**Covers:** Concurrency and idempotency principles.
**Why:** So two uploads, a double click or a retry can never damage the shared files.
**How (one small shared module, NEW):**
1. **Lock** for the shared index files: one writer at a time. It **expires safely** if the job dies.
2. **Version number** on rare row edits (document card, dictionary, entities): an update only works if the number you saw is still current.
3. **Ticket (idempotency key)** for uploads and questions: kept about 24 hours with the saved result. Same ticket + same content returns the saved result. Same ticket + different content is refused with a clear message.
4. Database rules back all of it (unique constraints, transactions).

| Test | Pass means |
|---|---|
| Two writers ask for the lock together | One goes, one waits or is told to try later |
| The lock holder is killed | Lock frees itself after its time, next job goes |
| Edit with an old version number | Refused, message says "someone changed this" |
| Same ticket twice | Work runs once, second call gets the same result |
| Same ticket, different content | Refused with a clear message |
| Ticket older than 24 hours | Treated as new |
| Double click on Upload | One upload only |

**Done when:** all the rows above pass with real Postgres/Redis, plus fast fake versions for quick tests.

### 0.5 Events that start the work (swappable bus)
**Covers:** Platform principles (fast, background, events).
**Why:** When a person uploads a paper, the website only saves it, says "got it", and sends out an event. Services listening for that event start the work (reading, making facts, indexing) by themselves. Nobody waits. The same event also feeds the live progress in the browser.
**The picture:**
```
 upload --> website saves file --> sends event "document.uploaded" --> [ BUS ]
                 |                                                       |
          answers at once                     +--------------------------+---------------+
          ("got it", job id)                  v                          v               v
                                     ingestion service           labelling service   progress
                                     (reads, makes facts)        (metric labels)     (browser)
                                             |                          |
                                   sends "facts.ready"  ---------->  sends "document.indexed"
```
**The bus is swappable.** One "send" door and one "listen" door. Behind them we can plug in:
- **Local** (in the website process, first choice, no extra software)
- **RabbitMQ**
- **AWS SQS** (and SNS if one event must reach many listeners)
- **AWS Lambda** (an event calls a small function directly)
The choice is one setting. No other code changes.
**How:**
1. One agreed event shape: `id`, `type` (for example `document.uploaded`), `job_id`, `step`, `message`, `time`, `data`, and the `ticket` (idempotency key).
2. A small list of event names and who listens to each (kept in one file).
3. Every listener is safe to run twice: the `id` is checked, so a repeated event does nothing new. A failed listener retries a few times, then the event goes to a "failed" pile you can look at and replay.
4. Each listener is a plain function with no knowledge of the bus, so it can run in the website, in a worker, or in a Lambda.
5. Progress for the browser: listeners also send progress events. The browser hears them by server-sent events (simple default; we only switch if a test shows a problem).
6. Questions use the same idea for their "How it happened" trail.

| Test | Pass means |
|---|---|
| Upload sends one `document.uploaded` event | The website answers at once, the ingestion listener starts by itself |
| Send 3 events | Arrive in order per job |
| Same event twice | Handled once |
| Listener crashes half way | Event is retried, no duplicate facts |
| Listener keeps failing | Goes to the "failed" pile, job says "failed" in plain words |
| Replay from the "failed" pile | Works and is safe |
| Browser joins late | Gets the events it missed |
| Browser drops and comes back | Continues from the last id |
| Swap Local for a fake RabbitMQ, a fake SQS and a fake Lambda | The same tests pass unchanged |
| A listener imports no bus code | Checked by a test that scans its imports |
| Setting names an unknown bus | Clear message at startup |

**Done when:** a fake upload event starts a fake ingestion listener with no waiting, five progress events show live in the browser, and the same tests pass on all four bus types (real ones marked `integration`).

---

## 7. Work package 1: Fix the answers (finance side)

### 1.1 Unit fix ("2,266 million million")
**Covers:** Known issue 1, Decision 4.
**Why:** The answer draft sometimes repeats the unit, the checker sees a clash, and the system refuses to answer (about 3 of 6 runs in our check).
**How:**
1. Small rule inside `assemble_draft`: if the value already ends with the unit, do not add it again.
2. Keep the old gate numbers, re-run the gates, keep old and new numbers side by side.

| Test | Pass means |
|---|---|
| "2,266 million" + unit "million" | Shows "2,266 million" |
| "2,266" + unit "million" | Shows "2,266 million" |
| "$2.3 billion" + unit "billion" | One "billion" only |
| Percent, no unit, empty unit, unit in another case ("Million") | No change or one unit, never two |
| Ten repeats of the same question | Same result every time |
| Full test suite | 570 passed, 3 skipped, plus new ones |
| Gates re-run | Numbers written next to the old ones |

**Done when:** the six-run check that failed 3 times now gives no unit clash.

### 1.2 Use the stored vectors (no re-embedding)
**Covers:** Known issues 2 and 8, Decisions 3 and 13.
**Why:** Re-embedding the candidate facts costs about 14 of the 28 seconds per question. The vectors are already saved.
**How:**
1. After the filter picks candidate ids, fetch their vectors from the flat index **by id**.
2. If a candidate has no vector, **skip that fact**, note it in "How it happened", and tell the repair service (see 1.3). Only if too many are missing (over half) say plainly that the index needs repair, and stop answering.
3. First run old and new side by side on a sample and compare scores and answers.
4. Only then switch the default.

```
 question --> filter (Postgres) --> candidate ids
                                        |
                        old way: embed text again (slow)
                        new way: read saved vectors by id  (fast)
                                        |
                                   same ranking steps
```

| Test | Pass means |
|---|---|
| Old vs new scores on 50 sample questions | Same up to a tiny rounding difference |
| Same final answers on the 82 answerable items | Same, or every difference explained |
| One candidate missing from the vector index | Skipped and noted, the rest are answered, repair is asked for |
| More than half the candidates missing | Clear message, no answer |
| Empty candidate list | Clean "nothing found", no crash |
| Time per question | Falls by about 10 seconds or more |
| Vector order after fetch | Matches candidate order |

**Done when:** a question takes about 15 seconds and the answers match the old way.

### 1.3 Keyword index saved at ingestion, plus a startup check that repairs by event
**Covers:** Known issue 8, Decision 14, and the events principle.
**Why:** Startup should not rebuild the word index from database text. Saved files can drift, so a background check finds gaps and fixes them, and never blocks startup.
**The rule:** the **database is the truth** (it holds every fact's text). The vector index and keyword index are copies that can be rebuilt from it. A fact missing from one copy is only a little harder to find, because the other copy still finds it, so it is never a reason to stop.
**How:**
1. The build step saves the keyword index next to the vector index.
2. Startup loads both saved files. Nothing is built at startup, and the app **always starts**.
3. Startup sends one event, `startup.check_requested`. It does not wait for the answer.
4. A check service listens, compares the ids of the database with the two indexes (counts first, full lists only if the counts differ) and sends `index.check_done`. If ids differ it also sends `index.mismatch_found` with the exact ids.
5. A repair service listens to `index.mismatch_found`, takes the same lock that uploads use, and fixes it from the database:
   - Fact in the database but not in the vector index: read its text, make its vector, add it.
   - Fact in the database but not in the keyword index: add its words.
   - Id in an index but not in the database (a "ghost"): remove it from the index.
   - Then it saves the file safely (write a new file, swap it in), and sends `index.repaired`.
6. While repairing, search skips the missing facts, and "How it happened" says so. The health page says "working, repairing" and then "ok".
7. If most facts are missing or the file is from another build (version tag differs), the check says so plainly and answers are refused until fixed. This is the only stop.
8. Before a benchmark run, a **strict** check runs first and the runner refuses to start if the copies differ, so the numbers are clean.

```
 app starts --> loads saved files --> serves questions at once
      |
      +--> event: startup.check_requested
                    |
                    v
             check service: compare ids
                    |
        same? --yes--> index.check_done  (health: ok)
                    | no
                    v
             event: index.mismatch_found (ids)
                    |
                    v
             repair service: take lock --> fix from database --> save safely
                    |
                    v
             event: index.repaired (health: ok)
```

| Test | Pass means |
|---|---|
| Build, save, load | Same ranking as building it live |
| Startup time | No index building (measured), and the app does not wait for the check |
| All three match | `index.check_done`, health ok, no repair |
| One id missing from the keyword index | App starts, repair adds it, search finds it after |
| One id missing from the vector index | App starts, repair makes its vector, search finds it after |
| Id in an index but not in the database | Repair removes the ghost, it is never shown as evidence |
| Question asked while repair runs | Answered, skipped fact is noted in "How it happened" |
| Repair killed half way, then retried | Safe to run again, no duplicates, index file not corrupt |
| Two apps start together and both find the gap | One repairs, the other waits on the lock, fixed once |
| Same mismatch event twice | Handled once |
| Repair keeps failing | Goes to the "failed" pile, health says "needs attention" with the ids |
| Most facts missing | Answers refused with a clear message |
| Keyword file missing | Clear message that says how to rebuild it |
| Old file from an older build | Refused by a version tag |
| Benchmark run with copies that differ | Runner refuses to start |
| Benchmark run with matching copies | Runs |

**Done when:** you delete one fact from the keyword index, start the app, and it answers at once and then heals itself.

### 1.4 Metric label and metric filter
**Covers:** Known issue 5, Decisions 8 and 12.
**Why:** The filter today ignores the metric, so 3M FY2018 leaves 897 candidates. Adding the metric shrinks the pile and the noise.
**How:**
1. Add a `metrics` list column, and a copy inside the JSON, using a migration (0.3). Change the frozen `Proposition` on purpose, with a test.
2. Label every fact, new and the existing 21,913: rules and the alias dictionary first, the model only for leftovers.
3. Filter becomes company + year + metric. If the filter leaves too little, use the "try again" road (4.5).
4. Add settings so each part can be on or off (so the filter is one setting, on or off).

```
 fact text ---> rules + dictionary ---> label found? --yes--> save label
                                             | no
                                             v
                                        ask the model (only leftovers) ---> save label
```

| Test | Pass means |
|---|---|
| Fact with a clear metric ("net sales") | Labelled by rules, no model call |
| Fact with two metrics | Both in the list |
| Fact nobody can label | Model labels it, or stays "unlabelled" |
| Filter for 3M FY2018 net sales | Far fewer than 897 |
| Filter leaves zero or too few | Falls back to the try-again road, never a silent empty answer |
| Backfill run twice | Same result the second time |
| Backfill on the 21,913 facts | Count unchanged, share labelled reported |
| Spot check of 100 labels | A person confirms the share that are right |
| Gate run with metric filter off | Same as before this step |

**Done when:** candidates drop a lot, gold evidence still survives the filter on the benchmark, and the spot check is good.

---

## 8. Work package 2: Company list and dictionary

### 2.1 One company list (entities table)
**Covers:** Known issues 4 and 9, Decision 10.
**Why:** The company list is built in two places today, and `placeholder_builder.build_registry` is never used.
**How:**
1. Build the entities table during ingestion, stored in Postgres, through a migration.
2. One shared function reads it. The API, the loader and tests all call it.
3. Keep the rules already learned: variants never longer than the full name, and short names shared by two companies are dropped.
4. Remove the unused second builder, or point it at the shared one.

| Test | Pass means |
|---|---|
| All 5 indexed companies resolve | Yes, to the full name |
| "3M Company's" (possessive) | Resolves right, no wrong filter |
| A short name shared by two companies | Dropped |
| Add a new company by ingestion | Shows up without code changes |
| Two edits at once on one entity | Version number stops the second |
| API and loader give the same list | Same |

**Done when:** there is one function and one table, and the old bug test still passes.

### 2.2 Free-text metrics and the bigger dictionary
**Covers:** Known issue 4, Decision 9.
**Why:** The splitter only knows 19 metrics. Real questions use many other words.
**How:**
1. Keep the dictionary in **MongoDB**. Load it into **Redis** when the app starts. All look-ups go through one function.
2. Fill it from many sources: standard accounting name lists (check licences first), labels from ingested filings, glossaries, Nigerian banking and rule words, benchmark fields, and phrases we could not match (logged).
3. Change the splitter so **every question passes through NPCE**, with free-text metrics, and the 19-item list becomes the first entries.
4. Add `pymongo` and `redis` to the project.

```
 question ---> splitter ---> "metric phrase"
                                  |
                         look-up function
                       /                    \
                 Redis (fast)  <-- loaded at startup from --  MongoDB (source)
                       |
                 known? --no--> log the phrase, use one lookup
```

| Test | Pass means |
|---|---|
| All 19 old metrics | Same results as before |
| The 10 metrics in the 366 items | All found |
| A new phrase added in MongoDB | Found after the next load |
| Redis empty at startup | Filled from MongoDB, or clear error |
| Unknown phrase | Logged, falls back to one lookup, no crash |
| Two people edit the same entry | Version number stops the second |
| Licence check | Every source has its licence noted |
| Splitter results on the 366 | No worse than today |

**Done when:** the 366-item results are not worse, and new words work with no code change.

---

## 9. Work package 3: Documents in and out (Lane B)

### 3.1 The document card and documents table
**Covers:** Planning answers on the document card.
**How:**
1. A `documents` table through a migration.
2. **You fill in:** Title, Type (policy, procedure, product guide, FAQ, form or notice, other), Product or area, Audience (default everyone), Effective date ("not sure" means upload date), Version (optional).
3. **The computer fills in:** id, file name, upload date, uploader, kind, page.
4. Newest effective date means "current". Pick lists can differ per organisation.

| Test | Pass means |
|---|---|
| Card with only required fields | Saved, defaults filled |
| "Not sure" for date | Upload date used |
| Missing title | Refused with a plain message |
| Edit with an old version number | Refused |
| Two versions of a policy | Newest is "current" |
| A type not on the pick list | Refused |

### 3.2 The file store door
**How:**
1. One "file store" door with two actions: save and read. Today it is the local folder `data/uploads/`. It can be S3 or Azure Blob later.
2. Files are saved by **document id**, with a fingerprint (hash). A bank id in the path is added later. The folder is kept out of git.
3. Saving sends an event.

| Test | Pass means |
|---|---|
| Save then read | Same bytes |
| Same file twice | Same fingerprint, second is flagged as a repeat |
| Two saves at once with the same id | One wins, no half-written file |
| Disk full or no permission | Clear error, nothing half saved |
| Swap in a fake store | Same tests pass |

### 3.3 Upload starts ingestion by event: add to the existing indexes
**Covers:** Decision 15, principles.
**How:** The upload call only saves the file and card, sends `document.uploaded`, and returns a job id at once. The ingestion service listens for that event and does the work (see 0.5). It saves progress so it can start again where it stopped.

```
 person uploads file + card
        |
        v
 ticket seen before? --yes--> return the saved result
        | no
        v
 save file (file store) --> send event "document.uploaded" --> answer "got it" (job id)
                                        |
                          (now in the background, by the listener)
                                        v
 take the lock (one writer at a time)
                                        |
                                        v
 read pages --> make facts --> label metrics   (progress events)
                                        |
                                        v
 write: database + vector index + keyword index  (one job, all or nothing)
                                        |
                                        v
 check the three stores hold the same ids
                                        |
                                        v
 free the lock --> send event "document.indexed"
```

Rules for repeats, new versions and deletes: a repeated file is skipped, a new version replaces the old facts, a delete removes the file's ids from all three stores. Every step records **who and when**.

| Test | Pass means |
|---|---|
| Upload a small file | Searchable, three stores match |
| Same file again | Skipped, told "already there" |
| New version of a file | Old facts gone, new facts in |
| Delete a file | Gone from all three stores |
| Two uploads at once | One waits for the lock, both end correct |
| Double click | One job (ticket) |
| Job killed half way, then retried | Continues, no duplicates, stores match |
| Model call fails | Retries, then a clear "failed" event |
| Event received twice | Handled once |
| Upload answers before any work is done | Reply comes back in about a second |
| Check after an upload | Same ids in all three stores (a mismatch is repaired, not fatal) |
| Existing 21,913 facts | Untouched |

**Done when:** a real paper you download becomes searchable, and killing the job mid-way leaves nothing broken.

### 3.4 The upload screen
**How:** A screen in the website: pick a file, fill the card, watch live progress from events, see "done" or "failed" in plain words.

| Test | Pass means |
|---|---|
| Fill the card and upload | Progress shows, then "done" |
| Leave a required field empty | Message beside the field |
| Server is off | Clear message |
| Double click | One upload |
| Phone width | Fits, no sideways scroll |
| Dark and light mode | Both readable |

---

## 10. Work package 4: The router and the roads

### 4.1 The router (money roads first)
**Covers:** Decision 11.
**Why:** One front door that picks the right road for each question.

```
 question
    |
    v
 LOOK AT THE QUESTION FIRST
    |-- words missing? ------------------> fix it, or ask the person
    |-- "A and B / both / each / compare"-> SPLIT into pieces
    |-- one piece needs another's answer-> STEP BY STEP
    |-- else ----------------------------> STRAIGHT
    v
 SEARCH, THEN LOOK AT WHAT CAME BACK
    |-- too few or weak results ---------> TRY AGAIN with other names
    |-- top results almost the same,
    |   differ in one detail ------------> ANSWER EACH, labelled (or ask)
    v
 CHECK AT THE END with the ORIGINAL question
    |
    v
 answer + "How it happened"
```

Money or paper is decided by the **shelf** (the document kind chosen at upload), never guessed from the words. When unsure, ask only if something important is missing. Caps: 6 pieces, 1 retry, a small cap on steps.

| Test | Pass means |
|---|---|
| Plain one-fact question | Straight road |
| "Revenue and net income of 3M in 2018" | Split, 2 pieces |
| "Compare A and B" | Split |
| Piece two needs piece one | Step by step |
| Question with a missing word | Fixed or asked |
| Seven things in one question | Capped at 6, and says so |
| Weak search | One retry only, then an honest "not found" |
| Two near-identical results | Both answered and labelled |
| Final check | Uses the original question |
| Router unsure, nothing important missing | Does not ask |
| Router unsure, something important missing | Asks |
| Every road | Writes what it did into the log |
| Old 82 answerable items | Not worse than before |

### 4.2 Money split (existing NPCE, now behind the router)
**How:** The router calls the existing splitter for money questions. Nothing else changes.

| Test | Pass means |
|---|---|
| The 366-item collision types | Same behaviour as today |
| Unmapped metric | Falls back to one lookup, logged |

### 4.3 Paper split (non-finance NPCE)
**Covers:** Non-finance NPCE decisions.
**How:**
1. A cheap, swappable model finds look-alike groups. Code checks that each phrase really appears in the question.
2. Swap in one item at a time. Cap 6.
3. Attach the document card details at upload. Check for evidence collisions. Combine and check.
4. This is a model job with its own setting (a new call site, see 4.6 for the rule).

| Test | Pass means |
|---|---|
| "Loan limit for savings vs current accounts" | Splits into two |
| A question that must not split | Stays one |
| The model invents a phrase not in the question | Code rejects it |
| Nigerian-style phrasing | Handled |
| Model unavailable | One lookup, no crash |
| Swap model by setting | No code change |

### 4.4 Fix the question
**Covers:** Decision 11 (words missing, fix or ask).
| Test | Pass means |
|---|---|
| "What was it in 2019?" with no company | Asks |
| A typo in a company name | Fixed |
| A clear question | Untouched |

### 4.5 Try again with other names
**Covers:** Decision 5, Decision 11.
| Test | Pass means |
|---|---|
| Weak result, other name works | Second try finds it and says so |
| Second try also weak | Honest "not found", no third try |
| Filter left too little (1.4) | Uses this road |

### 4.6 Step by step (the seventh model job)
**Covers:** Decision 11, the "step planner" yes.
**Why:** Written reason: some questions need the answer of one piece to ask the next, and no other job can plan that.
**How:** A planner job with its own setting and swappable model. Its output is checked by code (steps in range, no loops, cap on steps). The project rule of six call sites is changed, in writing, to seven.

| Test | Pass means |
|---|---|
| A two-step question | Steps run in order |
| Planner returns a loop | Rejected |
| Too many steps | Capped, says so |
| A step fails | Stops with a plain message |
| Final check | Uses the original question |
| Call-site test | Shows seven, each with its own setting |

### 4.7 "How it happened" (API and screen)
**How:** The API adds a section: the road taken and why, the question as understood, the pieces, the other names used on a retry, a several-versions note, the steps, and that the check used the original question. The screen shows it in plain words.

| Test | Pass means |
|---|---|
| Straight road | Says "answered straight" |
| Split | Shows the pieces |
| Retry | Shows the other names |
| Abstain | Still shows the section |
| Screen at phone width | Fits |

---

## 11. Work package 5: Proving it (Lane A)

### 5.1 Plain AI baseline
**Covers:** Decision 1.
**How:** A setup that asks the model directly with no retrieval, using the same harness, scored against the gold answers only.
| Test | Pass means |
|---|---|
| Zero retrieval calls | Checked by the shared meta-tested fixture (not ad hoc patching) |
| Same 366 items and scoring | Yes |
| Fake model | Runs with no cost |
| Result file | Same shape as other setups |

### 5.2 The naive RAG setup and the C4+ setup
**Covers:** Known issue 7 (closed by decision, see below), Decision 8 (replaced by the three-setup decision).
**Why:** The Master's compares exactly three setups: plain AI, naive RAG (C0) and C4+. C1, C2, C3 and the two ablations are not run.
**How:**
1. Keep the existing C0 config as it is and run it on the same harness, the same 366 items and the same scoring.
2. Add one config for C4+: the full system with the metric filter and the router on. Add the metric filter as a deliberate schema setting (the schema forbids unknown keys). The rule that only C0 may use naive, fixed-size and no-reranker stays.
3. The old C1, C2, C3 and C4 config files and their old gate numbers stay in the repo as history. They are not run again and not in the results tables.
4. Results are reported by collision type for the three setups.

| Test | Pass means |
|---|---|
| C0 config | Loads and runs unchanged |
| C4+ config | Loads, metric filter and router are on |
| A config that breaks the shared-core rule | Still refused |
| The runner is given any other setup name | Refused with a plain message listing the three allowed |
| Same 366 items and same scoring for all three | Yes |
| C0 uses no filter and no split | Checked |
| C4+ metric filter switched off in a test | Behaves like the old C4 |

**What this means for the results (told to the owner, not a new decision):** the plan can show how much better C4+ is than naive RAG and than plain AI. It can no longer say how much of the gain came from splitting, from the filter, or from the check, because those parts are not run apart. Also C0 finds facts across the whole corpus while C4+ filters first, so the two do not share a candidate step. The results chapter must say this plainly.

### 5.3 Index the 10 missing filings
**Covers:** Decision 2 and 6 (needs all 15 filings).
The 10: Adobe 2022, AES 2022, American Water Works 2020, Best Buy 2019, Boeing 2022, Costco 2021, General Mills 2020 and 2022, Lockheed Martin 2020, PepsiCo 2021.
**When:** CHANGED 2026-09-25 (owner). This step starts only after every known issue (work packages 0 to 4) is built and tested against the 5 filings and 21,913 facts we already have. There is no reason to spend on the other 10 before the fixes are proven.
**How:** Run in the background with saved progress; reuse the saved files for the 5 done. Costs real model calls, so **we ask you before it starts** and agree the number of workers.
| Test | Pass means |
|---|---|
| Stop half way, restart | Continues, no repeats |
| Three stores match after | Yes |
| All 15 filings present | Yes |
| The 366 items | 366 have their papers |
| Old 21,913 facts | Unchanged |

### 5.4 Test runner, pilot of 20, then the full 366
**Covers:** Decisions 2 and 6.
**How:** A runner runs every setup on the same list. First a fixed pilot of about 20 items spread over the 3 collision types, reused for all setups. You review it, then the full 366.
| Test | Pass means |
|---|---|
| Same pilot list for every setup | Yes |
| Runner stopped and restarted | Continues from saved progress |
| Same question twice | Same result (temperature 0) |
| Scores by collision type and by setup | Table produced |
| Old gate numbers | Reproduced or explained |

### 5.5 Non-finance test set
**How:** 40 to 60 questions from the real papers you download, including ones that must not split and some in Nigerian phrasing. The reworded finance set (30 to 40) is **still your call**.
| Test | Pass means |
|---|---|
| Must-not-split questions | Not split |
| Split questions | Split right |
| Answers vs your gold | Scored |

---

## 12. Work package 6: Housekeeping

| Step | What | When |
|---|---|---|
| 6.1 | Pointer file in the main folder that points to `backend/CLAUDE.md` (decided, not created) | When you say |
| 6.2 | Commit on a **new** branch, no push, no touching other branches | When you say |
| 6.3 | Refresh `sample_walkthrough/` from a new real run | Week 9 |
| 6.4 | Revisit Issue 6 (one question at a time) against the speed rule; Decision 7 (calculation module) stays deferred | After the full runs |
| 6.5 | Update `KNOWN_ISSUES.md` after each step | Always |

---

## 13. Test grid for the Master's results

The three setups run on the same 366 items, and the results are split by collision type.

| Setup | Retrieval | Split | Filter | Check (CoVe) | Where from |
|---|---|---|---|---|---|
| Plain AI | none | no | no | no | 5.1 |
| Naive RAG (C0) | whole corpus, plain chunks | no | no | no | exists |
| C4+ | filtered, reranked | yes | company + year + metric | yes | 1.4 + 4.1 + 5.2 |

Old C1, C2, C3 and C4 numbers are kept as history only.

---

## 14. What could go wrong (and what we do)

| Risk | What we do |
|---|---|
| The gain of C4+ cannot be split into its parts | Say so in the results chapter. Add the ablations later only if a reviewer asks. |
| Real model runs are slower or dearer than planned | The pilot of 20 tells us in week 5. We cut Lane B first. |
| Stored-vector answers differ from old answers | Compare side by side first. Do not switch until explained. |
| Metric labels are wrong for some facts | Spot check, fallback road, and a run with the filter off for comparison. |
| Indexing 10 filings takes long or costs a lot | Start in week 1, saved progress, we ask you before it begins. |
| Dictionary licences block a source | Skip that source and note it. |
| Time runs out before Dec 1 | Code freeze Nov 25. Lane B stops. Lane A finishes. |
| A change breaks old tests | Not done until 570 pass. |

---

## 15. Glossary (plain words)

- **Lock:** a "do not disturb" sign on a shared file. One writer at a time.
- **Version number:** a number on a row. If it changed since you looked, your edit is refused.
- **Ticket (idempotency key):** a code sent with a request. Send it twice, it runs once.
- **Event:** a short message that says "this happened". Parts of the system talk this way.
- **Migration:** a small numbered script that changes the database safely.
- **Three stores:** the database, the vector index and the keyword index. They must always hold the same ids. A gap in an index is repaired from the database in the background, never a reason to stop.
- **Shelf:** the kind of document (money or paper), chosen at upload.

---

## 16. Coverage map (every decision has a home)

| From the tracker | Where it is handled |
|---|---|
| Known issue 1 (unit flake) | 1.1 |
| Known issue 2 (slow answers) | 1.2 |
| Known issue 3 (database stops) | 0.1, 0.2 |
| Known issue 4 (not general) | 2.1, 2.2 |
| Known issue 5 (filter ignores metric) | 1.4 |
| Known issue 6 (one at a time) | 6.4 |
| Known issue 7 (C1/C2 vs C3/C4) | 5.2 (closed: C1 to C3 are no longer run) |
| Known issue 8 (indexes unused) | 1.2, 1.3 |
| Known issue 9 (registry twice) | 2.1 |
| Decision 1 (plain AI) | 5.1 |
| Decision 2 (pilot then full) | 5.3, 5.4 |
| Decision 3 (stored vectors) | 1.2 |
| Decision 4 (unit fix) | 1.1 |
| Decision 5 (expansion) | 4.5, 5.5 |
| Decision 6 (stay with 366) | 5.4 |
| Decision 7 (calculation module deferred) | 6.4 |
| Decision 8 (two ablations) | 5.2 (replaced: only plain AI, naive RAG, C4+) |
| Decision 9 (free-text metrics, dictionary) | 2.2 |
| Decision 10 (entities table) | 2.1 |
| Decision 11 (router) | 4.1 to 4.7 |
| Decision 12 (metric filter) | 1.4 |
| Decision 13 (reuse vectors) | 1.2 |
| Decision 14 (keyword index at ingestion) | 1.3 |
| Decision 15 (add to existing indexes) | 3.3 |
| Decision 16 (restart and time limit) | 0.1, 0.2 |
| Non-finance NPCE | 4.3, 5.5 |
| Document card | 3.1 |
| File store | 3.2 |
| Commit, name, pointer file | 6.1, 6.2 |
| Database change scripts | 0.3 |
| Dictionary in MongoDB then Redis | 2.2 |
| Uploads in a helper, resumable | 3.3 |
| Sample papers you download | 5.5 |
| Principles: fast, background, events | 0.5, rule 6 |
| Concurrency and idempotency | 0.4, rule 7 |
| Master's date December 1 | Sections 2 and 4 |

Still open and **not** decided by this plan: the reworded finance question set (30 to 40),
whether everything fits before December 1 (the pilot answers this), when to index the 10 filings
and how many workers, and the exact order to start building. Please tell us when you are ready.

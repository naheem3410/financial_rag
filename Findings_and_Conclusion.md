# Findings and Analysis

## What we were testing

AI systems that answer questions by reading real documents are called **RAG** systems, short for **Retrieval-Augmented Generation**. Instead of answering purely from memory, a RAG system first looks up the relevant pages in a real document, then writes an answer based on what it found. This is meant to make the AI more trustworthy, because its answer can be checked against a real source.

But RAG systems can still make mistakes. Sometimes an AI states a wrong number with total confidence, as if it were certain — a failure often called a **hallucination**. This is especially likely in financial documents, because financial filings are full of numbers that look similar but mean different things: "gross margin" versus "operating margin," this year's figure versus last year's, one company's number versus another's. It is easy for an AI to grab the wrong one and still sound sure of itself.

We tested two techniques meant to reduce this problem. The first is **NPCE**, short for Noun-Phrase Collision Elimination. In plain terms, NPCE takes a confusing question that asks for several similar numbers at once and splits it into simple, one-number-at-a-time questions, so the AI cannot mix them up. The second is **CoVe**, short for Chain-of-Verification. CoVe is a double-checking step: after the AI drafts an answer, a separate check re-examines each claim in that answer against the original source material before the answer is allowed out the door. We tested both techniques, alone and together, to see how much each one actually helps.

## Where our test questions came from

To know whether our system was telling the truth, we needed questions where we already knew the correct answer and exactly which page it came from. For that, we started from a public, existing test set called **FinanceBench** (Islam et al., arXiv:2311.11944). FinanceBench is a collection of real questions about real company financial filings — things like annual reports — where each question already comes with a verified correct answer and a pointer to the exact source page. That "correct answer plus source page" pairing is what let us check, precisely, whether our system's answers were faithful to the real document rather than made up.

There was a problem with using FinanceBench exactly as it comes, though. Almost all of its questions ask about a single number, for a single company, for a single time period — for example, "What was 3M's net income in 2018?" But our entire study is about what happens when the AI has to tell apart several similar-looking numbers at once. If a question only ever asks for one number, there is nothing for it to confuse in the first place. So the ready-made FinanceBench questions, on their own, could not test our idea at all.

Our fix used a property that is already true of financial statements: they are naturally comparative. A single page of a financial statement typically shows several numbers side by side — different years in neighboring columns, or several related line items stacked on top of each other. So we built new, deliberately confusing questions directly out of FinanceBench's own answer pages, asking for two similar numbers from the same page at once, instead of just one. Every one of these new questions still traces back to a real, verified FinanceBench answer — we never invented a fact. We call this derived set **FinanceBench-Collision**.

From FinanceBench-Collision, the results in this paper are based on a specific, final slice: **37 questions that test confusion between similar metrics on the same page** (for example, accounts payable versus inventories on the same balance sheet), drawn from 5 real company filings, mostly from 3M. This is not a new benchmark we invented from scratch — it is a targeted derivation of FinanceBench, and its answers are FinanceBench's own verified answers, reused and recombined.

One honest detail worth stating plainly: some of the added answer values had to be read directly off the same source page and checked by hand, since FinanceBench itself only verified one number per question, not two. We also found that different companies report their numbers in different units — some in thousands of dollars, some in millions — and we had to carefully match each answer to its correct unit so that scoring stayed fair and accurate.

## How to read our results

Before looking at any numbers, it helps to understand the four measurements we used to judge each version of the system. Each test question was actually run three separate times, and we used whichever answer came up most often, so that one unlucky or lucky roll of the dice would not skew a result.

A system tested on a set of questions can do three things with each question: answer it correctly, answer it incorrectly, or refuse to answer — which we call **abstaining**. Abstaining means the system says something like "not found in the source," instead of guessing. Abstaining is safer than guessing wrong, because a reader can tell an abstained question apart from an answered one and go check some other way — but a confidently wrong answer looks exactly like a correct one until someone catches it.

With that in mind, here are the four measures, each with a plain-English description:

- **Recall** — out of every question we asked, how many did the system get right? Think of it like a search-and-rescue team: recall is how many people they actually found, out of everyone who needed finding.
- **Precision** — out of the questions the system actually chose to answer (not counting the ones it abstained on), how many of those answers were right? Think of a weather forecaster: precision is how often they were right on the days they committed to a firm prediction, ignoring the days they said "not sure."
- **Coverage** — how often was the system willing to answer at all, instead of abstaining? A system with low coverage plays it safe very often; a system with high coverage is willing to commit to an answer most of the time.
- **Confidently-wrong rate** — out of every question we asked, how often did the system give a wrong answer while sounding sure of itself? This is the one measure we most want to see close to zero, because a confidently wrong answer is the dangerous case: it looks exactly like a correct one to a reader who cannot double-check it.

## The five setups we compared

We tested five different versions ("setups") of the system, switching NPCE and CoVe on or off in every combination:

- **C0** is a basic, off-the-shelf style approach: no NPCE, no CoVe, and a simpler way of searching the document (it reads the document in larger, page-sized chunks rather than pulling out individual facts). This stands in for what a standard, un-tuned system would do.
- **C1** is our own system with both NPCE and CoVe switched **off**. This is the fair starting point we measure everything else against — not a finished product, just the honest baseline our other setups are compared to.
- **C2** is C1 with only CoVe switched on (the double-checking step, NPCE still off).
- **C3** is C1 with only NPCE switched on (the question-splitting step, CoVe still off).
- **C4** is C1 with both NPCE and CoVe switched on together — the full combined system.

The results below are the final, corrected numbers: they use a version of the double-checking (CoVe) step that behaves consistently every time it is run (more on why that matters in Finding 4), a hand-verified answer key, and a scoring method that has been checked for bugs.

| | C0 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|
| Recall (got it right, out of all 37) | 62.2% | 37.8% | 37.8% | 81.1% | 75.7% |
| Precision (right, out of the ones it answered) | 71.9% | 58.3% | 82.4% | 83.3% | 96.6% |
| Coverage (willing to answer, vs. abstain) | 86.5% | 64.9% | 45.9% | 97.3% | 78.4% |
| Confidently-wrong rate (the dangerous one) | 24.3% | 27.0% | 8.1% | 16.2% | 2.7% |

Remember: C1 is the control, not a finished product. Every other column is measured as a change relative to C1.

## Finding 1 — NPCE is the big win

The clearest result in this study is what happens when we look specifically at the 37 questions built to test similar-metric confusion. On the hardest 18 of those questions — the ones specifically designed so that two similar numbers compete for the same spot in the system's search results — the baseline (C1) got **zero** of them right; every one came back wrong or abstained. Turning NPCE on (C3) alone brought that up to **16 out of 18 correct**, including every single one of the four toughest cases, where a wrong-year lookalike number had been drowning out the correct one.

Why did this happen? When a question asks for two similar numbers at once, and the AI searches for both at the same time, the two numbers end up competing for a short list of search results — and one of them can get crowded out by a similar-looking but wrong number. NPCE fixes this by splitting the question first: instead of one search trying to find two things at once, each number gets its own separate, focused search. With nothing to compete against, each number is far more likely to be found and used correctly. This is not a small improvement; it directly attacks the exact problem this whole study is about.

## Finding 2 — CoVe adds safety

Turning on CoVe (double-checking) by itself, without NPCE, changed the system in a different way. Compared to the baseline (C1), CoVe on its own (C2) raised precision from 58.3% to 82.4%, and — most importantly — cut the confidently-wrong rate from 27.0% down to 8.1%. In other words, when the double-checking step let an answer through, that answer was far more likely to be genuinely correct, and far less likely to be a confident mistake.

This came with a trade-off: coverage dropped from 64.9% to 45.9%. CoVe is a cautious reviewer. When it cannot fully confirm a claim against the source, it would rather say "not found" than let a shaky answer through. That means the system answers fewer questions overall — but the ones it does answer are safer to trust. Whether that trade-off is worth it depends on the situation: in a setting where a wrong answer is costly, giving up some coverage for much better safety is usually the right call.

## Finding 3 — using both together is best overall

Turning on both NPCE and CoVe together (C4) produced the best overall balance of any setup we tested. C4 reached 96.6% precision and a 2.7% confidently-wrong rate — the safest result of any setup by a wide margin — while still answering 78.4% of the questions and getting 75.7% of all 37 right overall. That is a large improvement over CoVe alone (C2), which only managed 37.8% recall and 45.9% coverage.

We describe NPCE and CoVe here as **complementary**: they help in different ways, and one does not undo the other's benefit. NPCE's splitting means CoVe has real, well-matched evidence to check each claim against instead of a jumbled, two-numbers-at-once answer. CoVe then catches the rare cases where NPCE's answer was still not quite right, and turns those into a safe "not found" instead of a wrong answer. Used together, the strengths of one cover the weak spot of the other.

## Finding 4 — a surprising result, and how we handled it honestly

Along the way, we ran into a result that looked troubling at first: an earlier version of C4 (NPCE plus CoVe together) actually scored **worse** than NPCE alone (C3) on the 18 hardest questions — only 10 out of 18 correct, compared to C3's 16 out of 18. At first glance, this looked like the two techniques were working against each other instead of helping each other.

We did not accept that conclusion at face value — we investigated why. It turned out the cause was not the techniques themselves, but a hidden setting. Many AI models have a control called **temperature**, which governs how much randomness goes into their wording. Set to its most predictable setting, an AI should give the exact same answer to the exact same question every time. We discovered that the AI model doing CoVe's double-checking was, without our intending it, being forced into a more random mode. This meant it sometimes phrased the same fact slightly differently from one check to the next — for example, writing a date as "December 31, 2018" one time and "the quarter ended December 31, 2018" the next, for the very same fact. Our checking logic then sometimes treated those two phrasings of the same true fact as if they disagreed, and wrongly rejected a correct answer.

Once we fixed the double-checking step to use a version of the AI that behaves consistently, we re-ran the same test. The result reversed: C4 rose from 10 out of 18 correct to **15 out of 18 correct, with zero wrong answers**, and all four of the toughest lookalike-number cases were now answered correctly. The full 37-question numbers moved the same way — recall rose from 59.5% to 75.7%, and precision rose from 91.7% to 96.6%. The real story was not that NPCE and CoVe fight each other. It was that one hidden, inconsistent setting was quietly sabotaging the double-checking step. We treat this as a lesson in careful measurement: before concluding that two techniques conflict, it is worth checking whether something simpler and more mechanical — like a setting most people would never think to look at — is the actual cause.

## Finding 5 — an honest oddity: the basic approach beat our switched-off baseline

One more result deserves an honest mention, because it looks strange at first: the basic, off-the-shelf-style approach (C0) beat our own baseline with both techniques switched off (C1) — 62.2% recall and 71.9% precision for C0, versus 37.8% recall and 58.3% precision for C1.

This is not a contradiction; it is a side effect of how C1 is built. Our full system (C1 through C4) works by breaking each document down into small, individual facts — for example, treating "accounts payable" and "inventories" as two separate pieces of text, even if they sit on the same page. This fine-grained breakdown is what lets NPCE search for each number separately and precisely (Finding 1). But when NPCE is switched off, as it is in C1, that same fine-grained breakdown works against the system: two related numbers from the same page now have to independently compete for a spot in a shared, short list of search results, and one can get crowded out by a similar-looking but wrong number from a different year. The basic approach (C0) never breaks the page apart in the first place — it keeps a larger chunk of the page together as one piece — so both numbers usually survive together in the same retrieved chunk, with nothing crowding them out.

In short, C1's fine-grained splitting is only an advantage once NPCE is there to search each piece properly. Without NPCE, that same splitting becomes a liability. This is exactly the crowding problem Finding 1 describes, seen from the opposite direction. It also confirms that C1 is doing its job correctly as a baseline: it is deliberately not the best system on its own. It exists so that C2, C3, and C4 can be measured fairly against a known, honest starting point.

## Limitations

This study has a narrow, clearly bounded scope, and it is worth stating that plainly rather than hiding it. We tested one specific kind of confusion — similar metrics sitting on the same page, such as two different line items in a financial statement. We did not get a working test of confusion between different time periods (our attempt at this axis, in this particular question set, turned out to have no clean test cases once checked directly), and we did not test confusion between different companies at all in this run, since that would have required source documents from more companies than the five we used. We also tested on a small set of 37 questions, drawn from only five documents, mostly belonging to one company (3M), with two more companies represented (Activision Blizzard and Adobe).

These results are promising, but they should be read as evidence from a focused, first pass — not as proof that the same numbers would hold on a larger, more varied set of companies, documents, and types of confusion. Confirming these findings on a bigger and more diverse test set is the natural next step.

# Conclusion

We set out to test two techniques for reducing confidently wrong answers in an AI system that reads financial documents: NPCE, which splits a confusing question into simple one-at-a-time questions, and CoVe, which double-checks the AI's draft answer against the source before it is allowed out. We found that NPCE strongly reduces the specific problem it targets — confusing similar-looking numbers — turning a baseline that got none of the hardest test questions right into one that got the large majority right. We found that CoVe, on its own, makes the system's answers noticeably safer, sharply cutting confidently wrong answers, at the cost of answering fewer questions. And we found that, once a hidden inconsistent setting in the double-checking step was fixed, using both techniques together gave the best overall balance of any setup we tested: high precision, a very low confidently-wrong rate, and solid coverage.

This matters because, in a setting like financial question-answering, a wrong number is not a small mistake — it can lead to a bad decision made with false confidence. A system that is willing to say "I don't know" is far safer than one that guesses and sounds sure either way.

Future work should test these techniques on more kinds of confusion — different time periods and different companies, not just similar metrics — across more companies and a larger set of questions, to see whether these findings hold at a bigger scale.

The takeaway a newcomer should walk away with is simple: when an AI has to tell apart several similar-looking numbers, splitting the question into simple one-at-a-time pieces helps it search correctly, and having it double-check its own answer helps it avoid sounding confident about a mistake. Neither trick is magic on its own, but done together, and done carefully, they make the system both more complete and more trustworthy than either one alone.

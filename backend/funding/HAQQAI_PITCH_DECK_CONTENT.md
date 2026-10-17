# HaqqAI — Pitch Deck



## Company Purpose

HaqqAI exists so that a bank can finally trust an AI's answer the same way it trusts an
audited number.

---

## Problem

Banks want AI answering customers, supporting staff, running operations, and informing
leadership, but most have held back for one reason: ordinary AI can sound completely
confident while being wrong, and in banking a wrong answer is never just a wrong answer.
It is a compliance breach, a lost customer, or a headline.

Today, institutions handle this the only way they safely can, which is to keep a human in
the loop for anything that matters. Filings and records get reviewed manually by analysts
and compliance staff. Where AI is used at all, it is either a narrow transactional chatbot
that only handles things like balance checks and transfers, such as UBA's Leo or Zenith's
ZIVA, or a general-purpose assistant used informally with plain or lightly-augmented
retrieval and no independent check on whether its answer is actually true.

Both fall short the same way. The chatbots are safe because they are narrow, they were
never built to reason over a filing or answer an open question. The general-purpose
assistants are broad but unverified, so no institution can respon­sibly put them in front
of a customer or a regulator without a human checking the output first. Neither gives a
bank AI it can trust with the things that actually matter.

---

## Solution

The insight behind HaqqAI is simple to state and hard to build: hallucination is not a
prompting problem, it is an architecture problem. You cannot ask a language model to
promise it is telling the truth. You have to build a system where truth is checked by
something other than the model that just spoke.

HaqqAI does that by structurally separating generation from verification. A question is
broken down so it cannot confuse similar figures across companies, time periods, or
metrics. An answer is drafted from retrieved evidence. Then, independently, every claim in
that draft is rechecked against the underlying evidence before anything is shown to a
user, and if the evidence does not support a claim, the system says so instead of
guessing. This has already been built and tested end to end on real financial documents
with real AI models running live, and validated against a purpose-built adversarial test
set of 366 hand-verified questions designed specifically to expose the failure modes
ordinary AI systems have in finance.

This is durable for a specific reason: it does not depend on any one model being good
enough not to hallucinate. As models change and improve, the verification layer keeps
doing its job regardless, because it never trusted the model's confidence in the first
place. From here, the same verification core extends outward into a full platform, one
customer-facing, staff-facing, operations, leadership, and governance surface at a time,
all sitting on the same trust layer instead of each reinventing it.

---

## Why Now

Large language models have only recently become good and cheap enough to seriously
consider putting in front of a bank's customers or staff, which is exactly why the
hallucination problem has only recently become urgent rather than theoretical. A few years
ago the models were not capable enough for this conversation to matter. Now they are
capable enough that every serious financial institution is being asked by its own board
why it is not using AI yet, and the honest answer has been that nobody has closed the
trust gap for this industry specifically.

At the same time, competitive pressure inside Nigerian banking is rising fast. Fintechs
are moving quickly and forcing incumbent banks to modernize how they serve customers and
run operations, which pushes AI adoption from a nice-to-have onto the actual roadmap. And
regulatory expectations around explainability, auditability, and accountability in
financial services are only getting stricter, not looser, which favors a system built
around proof and audit trails from day one over one bolted on after the fact.

The personal timing matters too. This was built by someone sitting inside a leading
Nigerian bank as a working engineer, seeing the actual demand firsthand, at the same time
as researching and solving the underlying technical problem at a graduate level. That
combination of proximity to the real need and depth on the real solution is not
something that stays available indefinitely.

---

## Market Potential

The immediate customer is any regulated financial institution in Nigeria: commercial
banks, microfinance banks, and fintechs, each of which has both the money movement and
the regulatory exposure that make unverified AI too risky to deploy as-is. Nigeria
currently has 21 commercial banks, over 860 licensed microfinance banks, and more than
430 fintech companies, all of which are regulated entities with a direct incentive to
adopt AI that is provably safe rather than merely convenient.

Using HaqqAI's own published pricing across that universe gives a rough, illustrative
sense of scale: commercial banks at an average of roughly
fifty million naira a year, microfinance banks and fintechs at an average of roughly ten
to fifteen million naira a year, points to an addressable market on the order of fifteen
to twenty billion naira a year within Nigeria alone, before any expansion beyond it. The
realistic near-term segment is narrower than that total, made up of the institutions
already actively investing in digital transformation and AI right now, a group that is
growing every year under the same competitive and regulatory pressure described above.

Some of the strongest companies end up defining their own category rather than fighting
for share of an existing one. Verified financial AI, meaning AI for banking that is
sold and evaluated on provability rather than just capability, does not really exist as a
category in Nigeria yet. HaqqAI is positioned to be the definition of it, not a
competitor within it.

---

## Competition / Alternatives

Direct comparisons fall into two groups, and both are narrower than HaqqAI by design, not
by accident. FinRobot is built for financial analysis and research, a single function
aimed at analysts rather than a full institution. UBA's Leo and Zenith's ZIVA are
transactional customer chatbots, built for things like balance checks and transfers, not
for reasoning over documents or serving staff and leadership. The indirect alternative is
what most institutions actually do today: manual review by human analysts and compliance
staff, or informal use of general-purpose AI assistants with no independent verification
step at all.

The plan to win is not to out-feature any one of these on their own turf. It is to be the
only option that is both a full institution platform, covering customers, staff,
operations, and leadership, and structurally verified, meaning every answer is checked
against real evidence before it reaches anyone, with governance, compliance, audit, and
authentication built in from the start rather than added later. Winning also means
starting from a position none of these competitors have: direct, current access to how a
real Nigerian bank's systems and constraints actually work, from the inside.

---

## Business Model

HaqqAI is sold as an annual subscription, priced per financial institution rather than per
user, per seat, or per query, which keeps the pricing predictable for both sides and easy
for an institution's finance team to approve. There are four tiers:

- **Starter**, five million naira a year: customer-facing AI plus basic operations and
  governance.
- **Professional**, fifteen million naira a year: adds employee-facing AI plus more
  advanced banking operations and governance.
- **Business**, thirty five million naira a year: every customer, employee, and
  operations module, plus full AI governance.
- **Enterprise**, seventy five million naira a year and up: every module, custom
  integration into the institution's own systems, and private cloud deployment.

The model is built to expand naturally rather than depend only on new-logo sales. An
institution that starts on Starter for customer-facing AI has an obvious, low-friction
next step once it trusts the system: upgrade to unlock staff-facing AI, then operations,
then full governance and custom integration. Revenue growth comes from institutions
trusting the platform with more of themselves over time, not just from signing new ones.

---

## Team

HaqqAI is currently a solo-founder venture. The founder, Quadri Naheem, brings three
things together that matter directly for this specific problem. Six years of professional
software engineering experience, currently as a Senior Software Engineer at a leading
bank in Nigeria, meaning direct, current exposure to how a financial institution's
systems, data, and operational constraints actually work, not an outside guess at them.
Internationally recognized certifications in large language models and generative AI,
backed by hands-on experience building different kinds of AI systems, giving the
technical depth this product demands beyond general software engineering. And the
research behind the product itself: as a Master's-level research project in Information
Technology, the founder personally researched, designed, built, and evaluated the core
verification engine, including constructing the evaluation benchmark from real financial
filings and running the full study behind the reported results.

That combination, a working bank engineer, certified in the exact technology at stake,
who built and evaluated the system himself, is what closes the usual gap between an
academic prototype and something a bank could actually trust. The next hires, once funded,
would fill out what one person cannot: dedicated sales into financial institutions,
additional engineering to build out the customer-facing and staff-facing product surfaces,
and security and compliance expertise to get through enterprise procurement.

---

## Financials

HaqqAI is pre-revenue and has not raised outside funding. Everything built so far, the
verification engine, the governance layer, and the evaluation benchmark, has been a
self-funded research and engineering effort. This round is to take it from a validated
prototype to something a real institution is paying for.

**Raising: ₦50,000,000.**

- **Product build-out, ₦20,000,000 (40%).** Build the first customer-facing and
  staff-facing surfaces that do not exist yet, on top of the verification engine that
  already does. Leadership reporting follows once those two are in use.
- **Team, ₦8,000,000 (16%).** Contract support for what the founder cannot cover alone:
  a part time engineer to speed up the build, and sales support focused on introductions
  into financial institutions.
- **Pilot deployment, ₦8,000,000 (16%).** Fund the integration work needed to run
  HaqqAI live inside one real institution, connecting into an actual core-banking, HR,
  or CRM system rather than a representative test environment.
- **Security, compliance, and scale hardening, ₦7,000,000 (14%).** Take multi-tenant
  access control from its current representative scale to something stress-tested and
  independently reviewed before a bank puts real data through it.
- **Infrastructure and operating costs, ₦5,000,000 (10%).** Cloud hosting, LLM API
  usage, and tooling for roughly twelve months.
- **Contingency, ₦2,000,000 (4%).**

This is sized to do one thing well: get HaqqAI through its first real institutional pilot
and into its first paying subscription, not to build the full platform in one round.



---

## Vision

In five years, HaqqAI is the default trusted AI layer inside most regulated financial
institutions in Nigeria, not a vendor a bank is still evaluating but the system its
compliance, operations, and customer-facing teams already assume is there. The platform
that today is a verification engine plus a governance layer has grown out into all five
surfaces working in production: customers self-serving safely, staff finding answers in
seconds instead of digging through documents, operations reading and understanding filings
automatically, leadership seeing exactly how AI is performing across the institution, and
governance holding all of it accountable with a permanent, provable audit trail.

Getting AI adopted this deeply into Nigerian banking specifically, in an industry that has
had every reason to stay cautious, is the milestone that matters most. It is the proof
that trust, not raw capability, was always the actual product being sold.

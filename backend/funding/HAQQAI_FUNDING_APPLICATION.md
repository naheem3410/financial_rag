# HaqqAI — Funding Application

This is the funding application content as actually submitted. Kept here so it stays
consistent with whatever pitch deck or follow-up materials get built from it later.

---

## Project Title

HaqqAI

## One-Line Summary / Tagline

Provable and Trustworthy AI Platform for Financial Enterprises

## Project Description

Banks want AI in the places that matter most: answering customers, supporting staff,
running operations, informing leadership. However, most have held back, because ordinary
AI can sound confident while being wrong, and in banking that isn't a small mistake. It's
a fine, a lost customer, or a headline.

HaqqAI is built to close that gap. Before it gives an answer, it checks every number and
every claim against the institution's own real documents, and if it can't prove something
is true, it says so instead of guessing. This isn't a promise on a slide: it's been built
and tested end to end on real financial documents with real AI models running live, and
validated against an adversarial test set designed specifically to expose the failure
modes of ordinary AI systems, so institutions can trust it before it ever reaches a
customer or a regulator.

Underneath that trust layer sits a full platform, not a single chatbot:

- Customers get safe, accurate self-service answers.
- Staff stop digging through documents and systems by hand and find what they need quickly.
- Operations has the institution's own documents and filings read and understood automatically.
- Leadership can see how AI is being used and how well it is performing across the institution.

And running underneath all of it is governance built for banking from the start: strict
separation between institutions and business units on the platform, a permanent audit
trail of every answer and the reasoning behind it, and rigorous protection of sensitive
customer data. The result is an institution that can finally say yes to AI where it
matters most, without trading away trust, safety, or compliance to get there.

---

## Problem it is solving

HaqqAI is trying to solve the trust and governance problem that prevents banks from deploying AI broadly: how to make AI useful while ensuring its answers are accurate, evidence-based, auditable, secure, and appropriate for a regulated financial environment.

## Impact & commercialisation potential

HaqqAI will help financial institutions adopt AI with greater confidence by making AI answers verifiable, auditable, and grounded in their own data and documents. This can reduce the time staff spend searching for information, improve customer self-service, and support faster and more reliable operations.

The initial market is Nigeria, with 21 commercial banks, 860+ microfinance banks, and 430+ fintech companies. HaqqAI will generate recurring revenue through annual subscriptions ranging from ₦5 million to ₦75 million+, depending on the level of access and integration required.

We plan to start with document-based use cases and gradually expand into customer service, employee support, operations, and leadership tools. Over time, the same platform can be adapted for other regulated financial institutions and markets.

## Why Should Miva Select Your Project

Miva should select HaqqAI because it is solving a real problem in an industry I understand firsthand. I have built and tested the core technology using real financial documents and AI models, rather than stopping at an idea or concept.

The project also has clear commercial potential, with banks, fintechs, and other financial institutions as its target customers. Beyond building a product, HaqqAI aims to demonstrate how AI can be used responsibly in a highly regulated industry where accuracy, security, and trust are critical.

With Miva’s support, I can move the existing working prototype toward a complete product, validate it with more users, and take the first steps toward commercial deployment.

## Application Q&A

### 1. Who experiences this problem?

Financial institutions: banks, fintechs, and other regulated financial-services providers
in Nigeria. 21 commercial banks, over 860 licensed microfinance banks, and more than 430
fintech companies.

### 2. How does that group currently handle the problem without this venture?

Manual review of filings and records by human analysts and compliance staff, and,
increasingly, general-purpose AI chatbots used with plain or lightly-augmented retrieval
and no independent verification step.

### 3. What has the venture built so far, and what does it currently do?

A working pipeline that reads financial filings, breaks a question down so it cannot
confuse similar sounding figures across companies, time periods, or metrics, retrieves
the relevant evidence, generates a draft answer, and then independently rechecks every
claim in that draft against the evidence before it is shown to users, refusing to answer
rather than guessing when the evidence does not support it. This has been run end to end
with real documents and real AI model calls, and tested against a purpose built set of
366 hand verified financial questions designed specifically to trigger the confusions
this system exists to prevent.

### 4. What has not been built yet?

The customer-facing, staff-facing, and leadership-facing interfaces are yet to be built.
What exists is the verification engine and its audit/governance layer, not the
surrounding product surfaces, and not live connections into any institution's actual
core-banking, HR, or CRM systems. Multi-tenant access control has been designed and built
at a representative scale, not stress-tested at commercial transaction volumes.

### 5. What does the venture charge, and per what unit?

HaqqAI charges an annual subscription per financial institution, tiered by how much of
the platform they get access to. Starter is five million naira a year for customer
facing AI and basic operations and governance. Professional is fifteen million naira a
year, adding employee facing AI and more advanced banking operations and governance.
Business is thirty five million naira a year and unlocks every customer experience,
employee experience, and banking operations module along with full AI governance.
Enterprise starts at seventy five million naira a year and includes every module, custom
integration into the institution's own systems, and private cloud deployment. The unit
being priced is the institution itself, per year, with the price scaling by feature
access.

### 6. Why is this team able to build this?

The founder, Quadri Naheem, brings three things together that matter directly for this
venture. First, six years of professional software engineering experience, currently
working as a Senior Software Engineer at a leading bank in Nigeria, which means direct
and current exposure to how a financial institution's systems, data, and operational
constraints actually work, rather than an outside guess at them. Second, internationally
recognized certifications in large language models and generative AI, backed by hands on
experience building different kinds of AI systems, which gives the technical depth this
specific product needs beyond general software engineering. Third, the research behind
the product itself. As a Master's level research project in Information Technology, the
founder personally researched, designed, built, and evaluated the core verification
engine, including constructing the evaluation benchmark from real financial filings and
running the full study behind the reported results. That combination, a working bank
engineer who is certified in the exact technology involved and who built and evaluated
the system himself, is what closes the usual gap between an academic prototype and
something a bank could actually trust.

### 7. Who pays for this?

The financial institution itself: a bank, fintech, or similar regulated entity.

### 8. Name the closest existing product, company, or method, and state the one thing this venture does differently. *(Max 60 words)*

Closest comparisons are FinRobot, built for financial analysis, and bank chatbots such as
UBA's Leo and Zenith's ZIVA, built for customer transactions like balance checks and
transfers. All three serve one narrow function. HaqqAI is a full institution platform,
where an independent verification step checks every claim against the institution's own
evidence, across governance, compliance, audit, authentication and authorization.

### 9. Project Stage

Working prototype with a completed research validation.

### 10. What state does the venture currently operate?

Lagos State, Nigeria.

### 11. What is the single strongest piece of evidence that someone wants this? *(Max 100 words)*

The strongest evidence comes from inside the industry itself. As a senior engineer at a
leading Nigerian bank, I see firsthand that banks constantly want tools that automate
banking operations while staying correct, auditable, and compliant, generic automation
without those guarantees isn't good enough for them. That demand is concrete enough that,
at my own bank, I have already helped build an early, smaller version of exactly this
kind of RAG system. This isn't a need I'm guessing at from outside the industry, it's one
I have already seen, and already started answering, from within it.

---

## Notes for the pitch deck (to build on later)

- Pricing table (naira/year, four tiers: Starter, Professional, Business, Enterprise) is
  final as submitted here — reuse directly, don't re-derive.
- Market sizing (21 commercial banks, 860+ microfinance banks, 430+ fintechs) is Nigeria-
  specific and came from this application; cite the same numbers for consistency.
- Closest-competitor framing (FinRobot, UBA Leo, Zenith ZIVA) is deliberately narrow and
  locally grounded — keep this comparison rather than swapping in more famous but less
  locally relevant names, unless the audience calls for it.
- Founder background (bank engineer + LLM/GenAI certifications + Master's research) is
  the core "why this team" narrative — carry it through consistently.

import type { AskResponse, Claim, Citation, Step, Verdict } from "@/lib/types";

const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  match: { label: "Matches the evidence", tone: "bg-ok-soft text-ok" },
  conflict: { label: "Conflicts with the evidence", tone: "bg-bad-soft text-bad" },
  unsupported: { label: "No supporting evidence", tone: "bg-warn-soft text-warn" },
};

// the model sometimes repeats the unit inside the value ("2,266 million" + "million")
function showUnit(value: string, unit: string): boolean {
  const u = unit.trim().toLowerCase();
  return u !== "" && !value.trim().toLowerCase().endsWith(u);
}

export function AnswerView({ data }: { data: AskResponse }) {
  return (
    <div className="rise flex flex-col gap-4">
      <AnswerCard data={data} />
      <HowItHappenedCard data={data} />
      <VerificationCard data={data} />
      {data.status === "answered" && data.citations.length > 0 && <CitationsCard citations={data.citations} />}
      <PipelineDetails data={data} />
    </div>
  );
}

function AnswerCard({ data }: { data: AskResponse }) {
  const answered = data.status === "answered";
  return (
    <article className="overflow-hidden rounded-xl border border-line bg-surface">
      <div
        className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold ${
          answered ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"
        }`}
      >
        {answered ? <CheckIcon /> : <PauseIcon />}
        {answered ? "Verified answer" : "HaqqAI declined to answer"}
      </div>
      <div className="p-5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">Question</p>
        <p className="mt-1 text-sm text-muted">{data.question}</p>

        {answered ? (
          <>
            <p className="mt-5 whitespace-pre-line text-2xl font-semibold leading-snug tracking-tight">
              {data.answer_text}
            </p>
            {data.answers.length > 0 && (
              <dl className="mt-4 flex flex-wrap gap-3">
                {data.answers.map((a, i) => (
                  <div key={`${a.metric}-${i}`} className="rounded-lg border border-line bg-bg px-3 py-2">
                    <dt className="text-xs capitalize text-muted">{a.metric}</dt>
                    <dd className="text-lg font-semibold tabular-nums">
                      {a.value}
                      {showUnit(a.value, a.unit) && (
                        <span className="ml-1 text-sm font-normal text-muted">{a.unit}</span>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </>
        ) : (
          <>
            <p className="mt-5 text-lg font-semibold leading-snug">
              The indexed filings do not prove this, so HaqqAI did not answer.
            </p>
            <p className="mt-2 text-sm text-muted">
              This is the safe outcome. A guess that sounds sure is worse than no answer. Check that the company
              and year are in the indexed filings, or rephrase the question.
            </p>
            {data.abstention_message && (
              <p className="mt-3 font-mono text-xs text-muted">{data.abstention_message}</p>
            )}
          </>
        )}
      </div>
    </article>
  );
}

function VerificationCard({ data }: { data: AskResponse }) {
  const { verification } = data;
  const pct = Math.round(verification.score * 100);
  const barTone =
    verification.total_count === 0 || pct === 0 ? "bg-bad" : pct === 100 ? "bg-ok" : "bg-warn";

  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Verification</h2>
        <p className="text-sm tabular-nums text-muted">
          {verification.verified_count} of {verification.total_count} claims verified
        </p>
      </div>

      <div
        role="meter"
        aria-label="Share of claims verified"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="mt-3 h-2 overflow-hidden rounded-full bg-bg"
      >
        <div className={`h-full rounded-full ${barTone}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-xs text-muted">
        Score {pct}%. The share of claims in the draft that matched the filings. Each claim is re-checked on its
        own, without seeing the draft.
      </p>

      <ol className="mt-4 flex flex-col gap-3">
        {verification.claims.map((claim) => (
          <ClaimRow key={claim.claim_id} claim={claim} />
        ))}
      </ol>
    </section>
  );
}

function ClaimRow({ claim }: { claim: Claim }) {
  const verdict = VERDICT[claim.verdict] ?? VERDICT.unsupported;
  return (
    <li className="rounded-lg border border-line px-3 py-2.5">
      <p className="text-sm">{claim.claim_text}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full px-2 py-0.5 font-medium ${verdict.tone}`}>{verdict.label}</span>
        <span className="text-muted">
          {claim.is_numeric ? "Number, compared exactly by code" : "Text claim"}
        </span>
      </div>
    </li>
  );
}

function CitationsCard({ citations }: { citations: Citation[] }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <h2 className="text-sm font-semibold">Where it came from</h2>
      <ul className="mt-3 flex flex-col gap-3">
        {citations.map((c) => (
          <li key={c.proposition_id} className="rounded-lg border border-line p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              <span className="rounded-md bg-brand-soft px-2 py-0.5 font-mono text-[11px] text-brand">
                {c.source_uri}
              </span>
              <span>{c.filing_type}</span>
              <span>Filed {c.filing_date}</span>
              <span className="font-medium text-ink">Page {c.page_number}</span>
            </div>
            {c.evidence && (
              <blockquote className="mt-2 border-l-2 border-brand pl-3 text-sm leading-relaxed">
                {c.evidence}
              </blockquote>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// Step 4.7: "How it happened", in plain words -- the road taken, the
// question as understood, the pieces or steps it broke into, and whether
// another name was tried. Always shown, even when HaqqAI declined to
// answer, so the person can see why.
function HowItHappenedCard({ data }: { data: AskResponse }) {
  const how = data.how_it_happened;
  const questionWasChanged = how.question_understood && how.question_understood !== data.question;

  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <h2 className="text-sm font-semibold">How it happened</h2>
      <p className="mt-2 text-sm text-muted">{how.summary}</p>

      {questionWasChanged && (
        <p className="mt-3 text-xs text-muted">
          Understood as: <span className="text-ink">{how.question_understood}</span>
        </p>
      )}

      {how.pieces.length > 1 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Split into</p>
          <ul className="mt-1.5 flex flex-col gap-1 text-sm">
            {how.pieces.map((piece, i) => (
              <li key={i} className="rounded-md bg-bg px-2.5 py-1.5">
                {piece}
              </li>
            ))}
          </ul>
        </div>
      )}

      {how.steps.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Steps</p>
          <ol className="mt-1.5 flex flex-col gap-1.5 text-sm">
            {how.steps.map((step) => (
              <StepRow key={step.step_id} step={step} />
            ))}
          </ol>
        </div>
      )}

      {how.retry_attempted && (
        <p className="mt-3 text-xs text-muted">
          The first search came back empty, so it tried again as{" "}
          {how.retry_names_tried.map((name, i) => (
            <span key={name}>
              {i > 0 && ", "}
              <span className="text-ink">{name}</span>
            </span>
          ))}
          .
        </p>
      )}

      {how.several_versions_note && <p className="mt-3 text-xs text-muted">{how.several_versions_note}</p>}
    </section>
  );
}

function StepRow({ step }: { step: Step }) {
  return (
    <li className="rounded-md bg-bg px-2.5 py-1.5">
      <p>{step.question}</p>
      <p className={`mt-0.5 text-xs ${step.answerable ? "text-muted" : "text-bad"}`}>
        {step.answerable ? step.answer_text : "Could not be answered."}
      </p>
    </li>
  );
}

function PipelineDetails({ data }: { data: AskResponse }) {
  const { pipeline } = data;
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <h2 className="text-sm font-semibold">How this was produced</h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
        <dt className="text-muted">Sub-questions</dt>
        <dd className="tabular-nums">{pipeline.sub_query_count}</dd>
        <dt className="text-muted">Facts used</dt>
        <dd className="tabular-nums">{pipeline.fact_count}</dd>
        <dt className="text-muted">Models</dt>
        <dd>
          <ul className="flex flex-col gap-0.5 font-mono">
            {Object.entries(pipeline.models).map(([step, model]) => (
              <li key={step}>
                {step}: {model}
              </li>
            ))}
          </ul>
        </dd>
      </dl>
    </section>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2">
      <path d="M4 10.5l4 4 8-9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2">
      <path d="M7 4.5v11M13 4.5v11" strokeLinecap="round" />
    </svg>
  );
}

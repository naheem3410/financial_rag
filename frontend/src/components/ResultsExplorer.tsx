"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  AxisMetricsCI,
  CollisionAxis,
  Outcome,
  ResultQuestion,
  ResultsData,
  SetupAnswer,
  SetupName,
  Verdict,
} from "@/lib/types";

const SETUP_LABEL: Record<SetupName, string> = {
  plain_ai: "Plain LLM",
  c0: "Naive RAG (C0)",
  c4_plus: "Extended System (C4+)",
};

const SETUP_HINT: Record<SetupName, string> = {
  plain_ai: "No retrieval -- answers from memory only",
  c0: "Dense retrieval, no reranking, no verification",
  c4_plus: "Entity/time disambiguation, reranking, verification",
};

const AXIS_LABEL: Record<CollisionAxis, string> = {
  entity: "Entity (two companies)",
  metric: "Metric (plain lookup)",
  temporal: "Temporal (two periods)",
};

const OUTCOME_TONE: Record<Outcome, string> = {
  correct: "bg-ok-soft text-ok",
  incorrect: "bg-bad-soft text-bad",
  abstained: "bg-warn-soft text-warn",
};

const OUTCOME_LABEL: Record<Outcome, string> = {
  correct: "Correct",
  incorrect: "Wrong",
  abstained: "Abstained",
};

const VERDICT_TONE: Record<Verdict, string> = {
  match: "bg-ok-soft text-ok",
  conflict: "bg-bad-soft text-bad",
  unsupported: "bg-warn-soft text-warn",
};

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "--";
  return `${Math.round(v * 100)}%`;
}

export function ResultsExplorer() {
  const [data, setData] = useState<ResultsData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/results/full366.json", { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error("not found");
        return res.json();
      })
      .then((body) => {
        if (!cancelled) setData(body as ResultsData);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="rounded-xl border border-line bg-bad-soft px-5 py-4 text-sm text-bad">
        Could not load the results data file. It should be at{" "}
        <code className="font-mono">public/results/full366.json</code>.
      </div>
    );
  }
  if (!data) {
    return <div className="rounded-xl border border-line bg-surface px-5 py-10 text-center text-sm text-muted">Loading results...</div>;
  }

  return (
    <div className="flex flex-col gap-6">
      <OverviewDashboard data={data} />
      <QuestionBrowser data={data} />
    </div>
  );
}

// ---------------------------------------------------------------- overview

function OverviewDashboard({ data }: { data: ResultsData }) {
  const setups: SetupName[] = ["plain_ai", "c0", "c4_plus"];
  const axes: CollisionAxis[] = ["entity", "metric", "temporal"];

  return (
    <section className="flex flex-col gap-5">
      <header>
        <h2 className="text-lg font-semibold tracking-tight">Full 366-question evaluation</h2>
        <p className="mt-1 text-sm text-muted">
          All three setups run over the same {data.n_questions} real questions. 95% confidence intervals shown as
          [low, high] under each number. Generated {data.generated_at}.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        {setups.map((s) => (
          <SetupOverviewCard key={s} setup={s} metrics={data.overall[s]} cost={data.cost[s]} />
        ))}
      </div>

      <div className="overflow-x-auto rounded-xl border border-line bg-surface">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
              <th className="px-4 py-3">By question type</th>
              {setups.map((s) => (
                <th key={s} className="px-4 py-3">
                  {SETUP_LABEL[s]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {axes.map((axis) => (
              <tr key={axis} className="border-b border-line last:border-0">
                <td className="px-4 py-3 align-top font-medium">{AXIS_LABEL[axis]}</td>
                {setups.map((s) => {
                  const m = data.by_axis[axis]?.[s];
                  return (
                    <td key={s} className="px-4 py-3 align-top text-xs">
                      {m ? (
                        <div className="flex flex-col gap-1">
                          <MetricLine label="Precision" stat={m.precision} />
                          <MetricLine label="Recall" stat={m.recall} />
                          <MetricLine label="Coverage" stat={m.coverage} />
                        </div>
                      ) : (
                        <span className="text-muted">n/a</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {data.ragas && (
          <div className="rounded-xl border border-line bg-surface p-4">
            <h3 className="text-sm font-semibold">RAGAS (Extended System only)</h3>
            <p className="mt-1 text-xs text-muted">An independent AI judge's opinion of the 340 answered questions.</p>
            <dl className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <dt className="text-xs text-muted">Faithfulness</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {pct(data.ragas.faithfulness.mean)}
                  <span className="ml-1 text-xs font-normal text-muted">
                    ({data.ragas.faithfulness.valid}/{data.ragas.faithfulness.total} scored)
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Answer relevancy</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {pct(data.ragas.answer_relevancy.mean)}
                  <span className="ml-1 text-xs font-normal text-muted">
                    ({data.ragas.answer_relevancy.valid}/{data.ragas.answer_relevancy.total} scored)
                  </span>
                </dd>
              </div>
            </dl>
          </div>
        )}

        <div className="rounded-xl border border-line bg-surface p-4">
          <h3 className="text-sm font-semibold">Real API cost, this evaluation</h3>
          <dl className="mt-3 flex flex-col gap-1.5 text-sm">
            {setups.map((s) => (
              <div key={s} className="flex items-baseline justify-between gap-3">
                <dt className="text-muted">{SETUP_LABEL[s]}</dt>
                <dd className="tabular-nums font-medium">${data.cost[s].toFixed(4)}</dd>
              </div>
            ))}
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted">RAGAS judging</dt>
              <dd className="tabular-nums font-medium">${data.cost.ragas.toFixed(4)}</dd>
            </div>
            <div className="mt-1 flex items-baseline justify-between gap-3 border-t border-line pt-1.5">
              <dt className="font-semibold">Total</dt>
              <dd className="tabular-nums font-semibold">
                ${(setups.reduce((sum, s) => sum + data.cost[s], 0) + data.cost.ragas).toFixed(2)}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>
  );
}

function MetricLine({ label, stat }: { label: string; stat: { value: number; lo: number; hi: number } | null }) {
  if (!stat) return null;
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted">{label}</span>
      <span className="tabular-nums">
        {pct(stat.value)} <span className="text-muted">[{pct(stat.lo)}, {pct(stat.hi)}]</span>
      </span>
    </div>
  );
}

function SetupOverviewCard({ setup, metrics, cost }: { setup: SetupName; metrics: AxisMetricsCI; cost: number }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <h3 className="text-sm font-semibold">{SETUP_LABEL[setup]}</h3>
      <p className="mt-0.5 text-xs text-muted">{SETUP_HINT[setup]}</p>

      <div className="mt-3 flex gap-3 text-xs">
        <span className="rounded-md bg-ok-soft px-2 py-1 font-medium text-ok">{metrics.correct} correct</span>
        <span className="rounded-md bg-bad-soft px-2 py-1 font-medium text-bad">{metrics.incorrect} wrong</span>
        <span className="rounded-md bg-warn-soft px-2 py-1 font-medium text-warn">{metrics.abstained} abstained</span>
      </div>

      <dl className="mt-3 flex flex-col gap-1.5 text-sm">
        <MetricLine label="Precision" stat={metrics.precision} />
        <MetricLine label="Recall" stat={metrics.recall} />
        <MetricLine label="Coverage" stat={metrics.coverage} />
        <MetricLine label="Confidently-wrong" stat={metrics.confidently_wrong} />
      </dl>

      <p className="mt-3 text-xs text-muted">
        Cost for all {metrics.n} questions: <span className="font-medium text-ink">${cost.toFixed(4)}</span>
      </p>
    </div>
  );
}

// ------------------------------------------------------------- browser

type OutcomeFilter = "all" | Outcome;

export function QuestionBrowser({ data }: { data: ResultsData }) {
  const [search, setSearch] = useState("");
  const [axisFilter, setAxisFilter] = useState<CollisionAxis | "all">("all");
  const [outcomeSetup, setOutcomeSetup] = useState<SetupName>("c4_plus");
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>("all");
  const [selected, setSelected] = useState<ResultQuestion | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return data.questions.filter((item) => {
      if (axisFilter !== "all" && item.axis !== axisFilter) return false;
      if (outcomeFilter !== "all" && item.setups[outcomeSetup]?.outcome !== outcomeFilter) return false;
      if (q && !item.question.toLowerCase().includes(q) && !item.collision_id.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data.questions, search, axisFilter, outcomeSetup, outcomeFilter]);

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h2 className="text-lg font-semibold tracking-tight">Every question, all three setups</h2>
        <p className="mt-1 text-sm text-muted">
          Tap any question to compare the plain LLM, naive RAG, and the Extended System side by side, including the
          verification trace and citations behind each answer.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search questions..."
          className="min-w-[14rem] flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
        />
        <select
          value={axisFilter}
          onChange={(e) => setAxisFilter(e.target.value as CollisionAxis | "all")}
          className="rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand"
        >
          <option value="all">All question types</option>
          <option value="entity">Entity</option>
          <option value="metric">Metric</option>
          <option value="temporal">Temporal</option>
        </select>
        <select
          value={outcomeSetup}
          onChange={(e) => setOutcomeSetup(e.target.value as SetupName)}
          className="rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand"
        >
          <option value="plain_ai">Filter by: Plain LLM</option>
          <option value="c0">Filter by: Naive RAG</option>
          <option value="c4_plus">Filter by: Extended System</option>
        </select>
        <select
          value={outcomeFilter}
          onChange={(e) => setOutcomeFilter(e.target.value as OutcomeFilter)}
          className="rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand"
        >
          <option value="all">Any outcome</option>
          <option value="correct">Correct</option>
          <option value="incorrect">Wrong</option>
          <option value="abstained">Abstained</option>
        </select>
        <span className="text-xs text-muted">{filtered.length} of {data.questions.length}</span>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] lg:items-start">
        <ol className="flex max-h-[32rem] flex-col gap-1.5 overflow-y-auto rounded-xl border border-line bg-surface p-2 lg:max-h-[42rem]">
          {filtered.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">No questions match.</li>}
          {filtered.map((item) => (
            <li key={item.collision_id}>
              <button
                type="button"
                onClick={() => setSelected(item)}
                className={`w-full rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  selected?.collision_id === item.collision_id
                    ? "bg-brand-soft text-brand"
                    : "hover:bg-bg"
                }`}
              >
                <span className="line-clamp-2">{item.question}</span>
                <span className="mt-1 flex flex-wrap items-center gap-1.5">
                  <span className="rounded-md bg-bg px-1.5 py-0.5 text-[11px] text-muted">{AXIS_LABEL[item.axis]}</span>
                  {(["plain_ai", "c0", "c4_plus"] as SetupName[]).map((s) => {
                    const outcome = item.setups[s]?.outcome;
                    if (!outcome) return null;
                    return (
                      <span key={s} className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${OUTCOME_TONE[outcome]}`}>
                        {s === "plain_ai" ? "P" : s === "c0" ? "C0" : "C4+"}
                      </span>
                    );
                  })}
                </span>
              </button>
            </li>
          ))}
        </ol>

        <div className="min-w-0">
          {selected ? <QuestionDetail item={selected} /> : <EmptyDetail />}
        </div>
      </div>
    </section>
  );
}

function EmptyDetail() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-14 text-center">
      <p className="text-sm font-medium">Select a question from the list</p>
      <p className="mx-auto mt-1 max-w-sm text-xs text-muted">
        See how the plain LLM, naive RAG and the Extended System each answered it, with scoring and evidence.
      </p>
    </div>
  );
}

function QuestionDetail({ item }: { item: ResultQuestion }) {
  const setups: SetupName[] = ["plain_ai", "c0", "c4_plus"];
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-line bg-surface p-4">
        <p className="font-mono text-[11px] text-muted">{item.collision_id}</p>
        <p className="mt-1 text-base font-medium">{item.question}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="rounded-md bg-bg px-2 py-0.5">{AXIS_LABEL[item.axis]}</span>
          {item.scale && <span className="rounded-md bg-bg px-2 py-0.5">scale: {item.scale}</span>}
        </div>
        {item.gold_answers && (
          <div className="mt-3">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">Gold (correct) values</p>
            <dl className="mt-1 flex flex-wrap gap-3">
              {Object.entries(item.gold_answers).map(([k, v]) => (
                <div key={k} className="rounded-md bg-bg px-2.5 py-1 text-sm">
                  <span className="text-muted">{k}:</span> <span className="font-semibold tabular-nums">{v}</span>
                </div>
              ))}
            </dl>
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {setups.map((s) => {
          const ans = item.setups[s];
          return <SetupAnswerCard key={s} setup={s} answer={ans} />;
        })}
      </div>
    </div>
  );
}

function SetupAnswerCard({ setup, answer }: { setup: SetupName; answer: SetupAnswer | undefined }) {
  if (!answer) {
    return (
      <div className="rounded-xl border border-line bg-surface p-4">
        <h3 className="text-sm font-semibold">{SETUP_LABEL[setup]}</h3>
        <p className="mt-2 text-xs text-muted">Not run for this question.</p>
      </div>
    );
  }
  const outcome = answer.outcome;
  return (
    <div className="flex flex-col rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{SETUP_LABEL[setup]}</h3>
        {outcome && (
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${OUTCOME_TONE[outcome]}`}>
            {OUTCOME_LABEL[outcome]}
          </span>
        )}
      </div>

      <p className="mt-2 whitespace-pre-line text-sm leading-relaxed">{answer.answer_text || "(no answer text)"}</p>

      {answer.answers.length > 0 && (
        <dl className="mt-2 flex flex-wrap gap-1.5">
          {answer.answers.map((a, i) => (
            <div key={i} className="rounded-md bg-bg px-2 py-1 text-xs">
              <span className="text-muted">{a.metric}:</span>{" "}
              <span className="font-medium tabular-nums">
                {a.value} {a.unit}
              </span>
            </div>
          ))}
        </dl>
      )}

      {answer.verification_trace && answer.verification_trace.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">
            Verification{answer.confidence_score != null ? ` -- ${pct(answer.confidence_score)} confidence` : ""}
          </p>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {answer.verification_trace.map((claim) => (
              <li key={claim.claim_id} className="rounded-md border border-line px-2 py-1.5 text-xs">
                <p>{claim.claim_text}</p>
                <span className={`mt-1 inline-block rounded-full px-1.5 py-0.5 text-[10px] font-medium ${VERDICT_TONE[claim.verdict]}`}>
                  {claim.verdict}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {answer.ragas && (answer.ragas.faithfulness != null || answer.ragas.answer_relevancy != null) && (
        <div className="mt-3 flex gap-3 text-xs">
          <span className="text-muted">
            Faithfulness: <span className="font-medium text-ink">{pct(answer.ragas.faithfulness)}</span>
          </span>
          <span className="text-muted">
            Relevancy: <span className="font-medium text-ink">{pct(answer.ragas.answer_relevancy)}</span>
          </span>
        </div>
      )}

      {answer.citations.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Where it came from</p>
          <ul className="mt-1.5 flex flex-col gap-2">
            {answer.citations.map((c, i) => (
              <li key={i} className="rounded-md border border-line p-2">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
                  <span className="rounded bg-brand-soft px-1.5 py-0.5 font-mono text-brand">{c.source_uri}</span>
                  <span>{c.filing_type}</span>
                  <span>Filed {c.filing_date}</span>
                  {c.page != null && <span className="font-medium text-ink">Page {c.page}</span>}
                </div>
                {c.evidence && <EvidenceQuote text={c.evidence} />}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">How this was produced</p>
        <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
          {answer.pipeline?.sub_query_count != null && (
            <>
              <dt className="text-muted">Sub-questions</dt>
              <dd className="tabular-nums">{answer.pipeline.sub_query_count}</dd>
            </>
          )}
          {answer.pipeline?.fff_count != null && (
            <>
              <dt className="text-muted">Facts used</dt>
              <dd className="tabular-nums">{answer.pipeline.fff_count}</dd>
            </>
          )}
          {answer.pipeline?.retrieved_chunk_count != null && (
            <>
              <dt className="text-muted">Chunks retrieved</dt>
              <dd className="tabular-nums">{answer.pipeline.retrieved_chunk_count}</dd>
            </>
          )}
          <dt className="text-muted">Models</dt>
          <dd className="font-mono">
            {Object.entries(answer.pipeline?.models ?? {}).map(([step, model]) => (
              <div key={step}>
                {step}: {model}
              </div>
            ))}
          </dd>
        </dl>
      </div>

      <p className="mt-3 text-[11px] text-muted">Cost: ${answer.cost_usd.toFixed(4)}</p>
    </div>
  );
}

const EVIDENCE_CLAMP = 220;

// Naive RAG retrieves whole fixed-size chunks (can run over a thousand
// characters -- a raw table dump, say); the Extended System retrieves single
// atomic facts (usually under 100). Clamped by default so both look like
// cards instead of C0's blowing out the column height, with a toggle to
// read the full retrieved chunk -- that size difference is itself part of
// what the two setups are.
function EvidenceQuote({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > EVIDENCE_CLAMP;
  const shown = expanded || !isLong ? text : `${text.slice(0, EVIDENCE_CLAMP).trimEnd()}...`;

  return (
    <blockquote className="mt-1.5 border-l-2 border-brand pl-2 text-xs leading-relaxed">
      {shown}
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="ml-1.5 inline text-[11px] font-medium text-brand hover:underline"
        >
          {expanded ? "Show less" : "Show full chunk"}
        </button>
      )}
    </blockquote>
  );
}

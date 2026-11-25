"use client";

import { useState } from "react";
import type { SystemInfo } from "@/lib/types";

const EXAMPLES = [
  {
    label: "Single figure",
    question: "What was 3M's accounts payable for FY2018? (in USD millions)",
  },
  {
    label: "Two similar figures",
    question:
      "What were 3M's accounts payable and accounts receivable for FY2018? Report both figures separately. (in USD millions)",
  },
  {
    label: "Not in the filings",
    question: "What was Costco's total revenue for FY2021?",
  },
];

interface Props {
  info: SystemInfo | null;
  busy: boolean;
  disabled: boolean;
  onAsk: (question: string) => void;
}

export function QueryPanel({ info, busy, disabled, onAsk }: Props) {
  const [question, setQuestion] = useState("");
  const trimmed = question.trim();
  const canSubmit = trimmed.length >= 3 && !busy && !disabled;

  function submit() {
    if (canSubmit) onAsk(trimmed);
  }

  return (
    <aside className="flex flex-col gap-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="rounded-xl border border-line bg-surface p-4"
      >
        <label htmlFor="question" className="text-sm font-semibold">
          Your question
        </label>
        <textarea
          id="question"
          value={question}
          maxLength={1000}
          rows={5}
          placeholder="For example: What was 3M's accounts payable for FY2018?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          className="mt-2 w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-muted">Ctrl or Cmd + Enter to ask</span>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-bg transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Checking..." : "Ask HaqqAI"}
          </button>
        </div>
      </form>

      <div className="rounded-xl border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold">Try one</h2>
        <ul className="mt-2 flex flex-col gap-2">
          {EXAMPLES.map((ex) => (
            <li key={ex.label}>
              <button
                type="button"
                disabled={busy}
                onClick={() => setQuestion(ex.question)}
                className="w-full rounded-lg border border-line px-3 py-2 text-left transition-colors hover:border-brand hover:bg-brand-soft disabled:opacity-50"
              >
                <span className="block text-xs font-medium text-brand">{ex.label}</span>
                <span className="mt-0.5 block text-sm">{ex.question}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-xl border border-line bg-surface p-4">
        <h2 className="text-sm font-semibold">What is indexed</h2>
        {info ? (
          <>
            <p className="mt-1 text-xs text-muted">
              {info.companies.join(", ")}. {info.proposition_count.toLocaleString()} facts across{" "}
              {info.documents.length} filings.
            </p>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {info.documents.map((doc) => (
                <li
                  key={doc}
                  className="rounded-md bg-brand-soft px-2 py-0.5 font-mono text-[11px] text-brand"
                >
                  {doc}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="mt-1 text-xs text-muted">Available once the API is connected.</p>
        )}
      </div>
    </aside>
  );
}

"use client";

import { useEffect, useState } from "react";

export function LoadingCard({ question }: { question: string }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rise rounded-xl border border-line bg-surface p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">Question</p>
      <p className="mt-1 text-sm">{question}</p>

      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-brand-soft" role="progressbar" aria-label="Checking the answer">
        <div className="sweep h-full w-1/4 rounded-full bg-brand" />
      </div>

      <p className="mt-4 text-sm font-medium">Checking the answer against the filings</p>
      <p className="mt-1 text-sm text-muted">
        HaqqAI splits the question, finds the evidence, drafts an answer, then re-checks every claim on its
        own. On this prototype that usually takes one to two minutes.
      </p>
      <p className="mt-3 font-mono text-xs tabular-nums text-muted">{seconds}s elapsed</p>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import type { AskResponse, SystemInfo } from "@/lib/types";
import { AnswerView } from "./AnswerView";
import { LoadingCard } from "./LoadingCard";
import { QueryPanel } from "./QueryPanel";
import { UploadPanel } from "./UploadPanel";

type Tab = "ask" | "upload";

type Phase =
  | { kind: "idle" }
  | { kind: "loading"; question: string }
  | { kind: "done"; data: AskResponse }
  | { kind: "error"; message: string };

type Connection = "checking" | "online" | "offline";

export function Workspace() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [connection, setConnection] = useState<Connection>("checking");
  const [tab, setTab] = useState<Tab>("ask");

  const loadInfo = useCallback(async () => {
    try {
      const res = await fetch("/api/info", { cache: "no-store" });
      if (!res.ok) throw new Error("offline");
      setInfo((await res.json()) as SystemInfo);
      setConnection("online");
    } catch {
      setInfo(null);
      setConnection("offline");
    }
  }, []);

  useEffect(() => {
    // fetch on mount; the setState calls happen after the await, not synchronously
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadInfo();
  }, [loadInfo]);

  const ask = useCallback(
    async (question: string) => {
      setPhase({ kind: "loading", question });
      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ question }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail =
            typeof body?.detail === "string"
              ? body.detail
              : "Something went wrong while answering. Please try again.";
          setPhase({ kind: "error", message: detail });
          if (res.status === 503) setConnection("offline");
          return;
        }
        setConnection("online");
        setPhase({ kind: "done", data: body as AskResponse });
      } catch {
        setPhase({ kind: "error", message: "The request could not be completed. Check your connection and try again." });
      }
    },
    [],
  );

  const busy = phase.kind === "loading";

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="grid h-9 w-9 place-items-center rounded-lg bg-brand text-lg font-semibold text-bg"
            >
              H
            </span>
            <div>
              <h1 className="text-lg font-semibold leading-tight tracking-tight">HaqqAI</h1>
              <p className="text-xs text-muted">Trustworthy AI platform for financial enterprises</p>
            </div>
          </div>
          <ConnectionPill state={connection} onRetry={loadInfo} />
        </div>
      </header>

      <nav className="mx-auto flex w-full max-w-6xl gap-2 px-4 pt-6 sm:px-6" role="tablist">
        <TabButton active={tab === "ask"} onClick={() => setTab("ask")}>
          Ask a question
        </TabButton>
        <TabButton active={tab === "upload"} onClick={() => setTab("upload")}>
          Upload a document
        </TabButton>
      </nav>

      {tab === "ask" ? (
        <main className="mx-auto grid w-full max-w-6xl flex-1 gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:items-start">
          <QueryPanel info={info} busy={busy} disabled={connection === "offline"} onAsk={ask} />

          <section aria-live="polite" aria-busy={busy} className="min-w-0">
            {phase.kind === "idle" && <EmptyState />}
            {phase.kind === "loading" && <LoadingCard question={phase.question} />}
            {phase.kind === "error" && <ErrorCard message={phase.message} />}
            {phase.kind === "done" && <AnswerView data={phase.data} />}
          </section>
        </main>
      ) : (
        <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-6 sm:px-6">
          <UploadPanel info={info} />
        </main>
      )}

      <footer className="border-t border-line px-4 py-4 text-center text-xs text-muted sm:px-6">
        Prototype. Answers come only from the indexed filings shown on the left. When HaqqAI cannot prove
        something, it says so instead of guessing.
      </footer>
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        active ? "bg-brand text-bg" : "border border-line bg-surface text-muted hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function ConnectionPill({ state, onRetry }: { state: Connection; onRetry: () => void }) {
  if (state === "offline") {
    return (
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-2 rounded-full border border-line bg-bad-soft px-3 py-1 text-xs font-medium text-bad"
      >
        <span className="h-2 w-2 rounded-full bg-bad" />
        API offline. Retry
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-muted">
      <span className={`h-2 w-2 rounded-full ${state === "online" ? "bg-ok" : "bg-warn"}`} />
      {state === "online" ? "API connected" : "Connecting"}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-14 text-center">
      <p className="text-base font-medium">Ask a question about the indexed filings</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted">
        You will see the answer, the check that proved it, and the exact page it came from. If the filings do
        not support an answer, HaqqAI declines.
      </p>
    </div>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div role="alert" className="rise rounded-xl border border-line bg-bad-soft px-5 py-4 text-bad">
      <p className="text-sm font-semibold">Could not get an answer</p>
      <p className="mt-1 text-sm">{message}</p>
    </div>
  );
}

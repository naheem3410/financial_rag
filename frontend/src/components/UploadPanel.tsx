"use client";

import { useCallback, useRef, useState } from "react";
import type { DocumentEvent, DocumentEventsResponse, DocumentUploadResponse, SystemInfo } from "@/lib/types";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "watching"; docId: string; events: DocumentEvent[] }
  | { kind: "done"; docId: string; events: DocumentEvent[]; skipped: boolean }
  | { kind: "failed"; docId: string; events: DocumentEvent[] }
  | { kind: "error"; message: string };

interface FieldErrors {
  file?: string;
  title?: string;
  docType?: string;
}

const DEFAULT_TYPES = ["policy", "procedure", "product guide", "faq", "form or notice", "other"];

export function UploadPanel({ info }: { info: SystemInfo | null }) {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [notSure, setNotSure] = useState(true);
  const formRef = useRef<HTMLFormElement>(null);
  const submittingRef = useRef(false); // guards a real double click even before React re-renders the disabled button

  const documentTypes = info?.document_types?.length ? info.document_types : DEFAULT_TYPES;

  const watchProgress = useCallback((docId: string) => {
    let afterSeq = 0;
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`/api/documents/${docId}/events?after_seq=${afterSeq}`, { cache: "no-store" });
        if (!res.ok) throw new Error("poll failed");
        const body = (await res.json()) as DocumentEventsResponse;
        if (cancelled) return;
        afterSeq = body.events.length ? body.events[body.events.length - 1].seq : afterSeq;

        setPhase((prev) => {
          const events = prev.kind === "watching" && prev.docId === docId ? [...prev.events, ...body.events] : body.events;
          if (body.done) {
            return body.failed
              ? { kind: "failed", docId, events }
              : { kind: "done", docId, events, skipped: events.some((e) => e.type === "document.skipped") };
          }
          return { kind: "watching", docId, events };
        });

        if (!body.done) setTimeout(poll, 1000);
      } catch {
        if (!cancelled) setPhase({ kind: "error", message: "Lost the connection while watching progress. The upload may still finish; try refreshing." });
      }
    }

    void poll();
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = useCallback(
    async (form: HTMLFormElement) => {
      if (submittingRef.current) return; // a real double click on the same form: ignore the second one outright
      const data = new FormData(form);
      const file = data.get("file") as File | null;
      const title = String(data.get("title") ?? "").trim();
      const docType = String(data.get("doc_type") ?? "");

      const fieldErrors: FieldErrors = {};
      if (!file || file.size === 0) fieldErrors.file = "Choose a file to upload.";
      if (!title) fieldErrors.title = "Give the document a title.";
      if (!docType) fieldErrors.docType = "Choose a type.";
      setErrors(fieldErrors);
      if (Object.keys(fieldErrors).length > 0) return;

      if (notSure) data.delete("effective_date");
      data.set("ticket", crypto.randomUUID());

      submittingRef.current = true;
      setPhase({ kind: "uploading" });
      try {
        const res = await fetch("/api/documents", { method: "POST", body: data });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof body?.detail === "string" ? body.detail : "Could not upload. Please try again.";
          setPhase({ kind: "error", message: detail });
          return;
        }
        const uploaded = body as DocumentUploadResponse;
        setPhase({ kind: "watching", docId: uploaded.doc_id, events: [] });
        watchProgress(uploaded.doc_id);
      } catch {
        setPhase({ kind: "error", message: "Cannot reach the server. Check your connection and try again." });
      } finally {
        submittingRef.current = false;
      }
    },
    [notSure, watchProgress],
  );

  const busy = phase.kind === "uploading" || phase.kind === "watching";

  return (
    <div className="flex flex-col gap-4">
      <form
        ref={formRef}
        onSubmit={(e) => {
          e.preventDefault();
          void submit(e.currentTarget);
        }}
        className="rounded-xl border border-line bg-surface p-4"
      >
        <h2 className="text-sm font-semibold">Upload a document</h2>
        <p className="mt-1 text-xs text-muted">A policy, procedure, guide, FAQ, or form -- not a financial filing.</p>

        <Field label="File" error={errors.file}>
          <input
            type="file"
            name="file"
            disabled={busy}
            className="mt-1 block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-brand-soft file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-brand"
          />
        </Field>

        <Field label="Title" error={errors.title}>
          <input
            type="text"
            name="title"
            disabled={busy}
            placeholder="For example: Account Opening Policy"
            className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
          />
        </Field>

        <Field label="Type" error={errors.docType}>
          <select
            name="doc_type"
            disabled={busy}
            defaultValue=""
            className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
          >
            <option value="" disabled>
              Choose one
            </option>
            {documentTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Product or area (optional)">
          <input
            type="text"
            name="product_area"
            disabled={busy}
            className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
          />
        </Field>

        <Field label="Who this is for">
          <input
            type="text"
            name="audience"
            disabled={busy}
            defaultValue="everyone"
            className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
          />
        </Field>

        <Field label="Effective date">
          <label className="mt-1 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={notSure}
              disabled={busy}
              onChange={(e) => setNotSure(e.target.checked)}
              className="h-4 w-4 rounded border-line"
            />
            Not sure (uses today&apos;s date)
          </label>
          {!notSure && (
            <input
              type="date"
              name="effective_date"
              disabled={busy}
              className="mt-2 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/30"
            />
          )}
        </Field>

        <Field label="Version label (optional)">
          <input
            type="text"
            name="doc_version_label"
            disabled={busy}
            placeholder="For example: v2"
            className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-brand focus:ring-2 focus:ring-brand/30"
          />
        </Field>

        <button
          type="submit"
          disabled={busy}
          className="mt-4 w-full rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-bg transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-40"
        >
          {phase.kind === "uploading" ? "Uploading..." : phase.kind === "watching" ? "Processing..." : "Upload"}
        </button>
      </form>

      <div aria-live="polite">
        {phase.kind === "watching" && <ProgressCard events={phase.events} label="Working on it..." />}
        {phase.kind === "done" && (
          <ProgressCard
            events={phase.events}
            label={phase.skipped ? "Already there -- this exact file was uploaded before." : "Done. The document is now searchable."}
            tone="ok"
          />
        )}
        {phase.kind === "failed" && <ProgressCard events={phase.events} label="Failed. See the details below." tone="bad" />}
        {phase.kind === "error" && (
          <div role="alert" className="rise rounded-xl border border-line bg-bad-soft px-5 py-4 text-bad">
            <p className="text-sm font-semibold">Could not upload</p>
            <p className="mt-1 text-sm">{phase.message}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <label className="text-sm font-medium">{label}</label>
      {children}
      {error && <p className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  );
}

function ProgressCard({ events, label, tone }: { events: DocumentEvent[]; label: string; tone?: "ok" | "bad" }) {
  const toneClass = tone === "ok" ? "border-line bg-ok-soft text-ok" : tone === "bad" ? "border-line bg-bad-soft text-bad" : "border-line bg-surface";
  return (
    <div className={`rise rounded-xl border px-4 py-3 ${toneClass}`}>
      <p className="text-sm font-semibold">{label}</p>
      <ul className="mt-2 flex flex-col gap-1 text-xs text-muted">
        {events.map((e) => (
          <li key={e.seq}>{e.message || e.type}</li>
        ))}
      </ul>
    </div>
  );
}

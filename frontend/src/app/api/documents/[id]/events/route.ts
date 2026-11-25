import { callBackend } from "@/lib/backend";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const afterSeq = new URL(request.url).searchParams.get("after_seq") ?? "0";
  return callBackend(
    `/api/documents/${encodeURIComponent(id)}/events?after_seq=${encodeURIComponent(afterSeq)}`,
    { method: "GET" },
    10_000,
  );
}

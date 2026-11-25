import { callBackendBinary } from "@/lib/backend";

// A file can take a moment to save and start indexing; generous but not as
// long as /api/ask, since this route never waits for the slow part.
export const maxDuration = 60;

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") ?? "";
  const body = await request.arrayBuffer();
  return callBackendBinary(
    "/api/documents",
    { method: "POST", headers: { "content-type": contentType }, body },
    55_000,
  );
}

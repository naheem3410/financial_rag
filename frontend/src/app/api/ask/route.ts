import { callBackend } from "@/lib/backend";

// A verified answer can take a couple of minutes on this prototype, so the default timeouts are too short.
export const maxDuration = 300;

export async function POST(request: Request) {
  const body = await request.text();
  return callBackend(
    "/api/ask",
    { method: "POST", headers: { "content-type": "application/json" }, body },
    290_000,
  );
}

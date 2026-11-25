const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function callBackend(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  try {
    const upstream = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    const detail = timedOut
      ? "The HaqqAI API took too long to answer. Try again."
      : "Cannot reach the HaqqAI API. Is it running on " + BACKEND_URL + "?";
    return Response.json({ detail }, { status: timedOut ? 504 : 503 });
  }
}

// A file upload's bytes must never go through .text() -- that re-encodes
// as a string and corrupts anything that isn't plain text. This passes the
// body straight through as raw bytes instead.
export async function callBackendBinary(
  path: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  try {
    const upstream = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await upstream.arrayBuffer();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    const detail = timedOut
      ? "The upload took too long. Try again."
      : "Cannot reach the HaqqAI API. Is it running on " + BACKEND_URL + "?";
    return Response.json({ detail }, { status: timedOut ? 504 : 503 });
  }
}

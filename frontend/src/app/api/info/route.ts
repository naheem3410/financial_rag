import { callBackend } from "@/lib/backend";

export async function GET() {
  return callBackend("/api/info", { method: "GET" }, 8_000);
}

import type { SignalOutput } from "./types";

/**
 * Fetch a signal from the FastAPI backend via the Next.js /api/* proxy.
 *
 * On the server (Server Components), we hit the backend directly using
 * BACKEND_URL so we aren't routing through the dev server's rewrite layer.
 * On the client, we use the /api/* proxy path.
 */
function getBaseUrl(): string {
  if (typeof window === "undefined") {
    // Server-side (next dev / next start): call backend directly via BACKEND_URL.
    return process.env.BACKEND_URL ?? "http://localhost:8000";
  }
  // Client-side: NEXT_PUBLIC_API_URL is baked in at build time.
  // Falls back to the /api rewrite proxy which works in `next dev`.
  return process.env.NEXT_PUBLIC_API_URL ?? "/api";
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Fetch a signal for a given ticker symbol.
 *
 * @param symbol - Ticker symbol (e.g. "AAPL"), will be uppercased.
 * @param period - Analysis period string (e.g. "3mo").
 * @param noLlm - If true, skip LLM synthesis and return rule-based signal.
 * @returns Typed SignalOutput response from the backend.
 * @throws ApiError if the backend returns a non-2xx status.
 */
export async function fetchSignal(
  symbol: string,
  period: string,
  noLlm: boolean
): Promise<SignalOutput> {
  const base = getBaseUrl();
  const params = new URLSearchParams({
    period,
    no_llm: String(noLlm),
  });
  const url = `${base}/signals/${encodeURIComponent(symbol.toUpperCase())}?${params}`;

  const res = await fetch(url, {
    // Disable Next.js fetch cache so every request is fresh
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore JSON parse errors
    }
    throw new ApiError(res.status, detail);
  }

  return res.json() as Promise<SignalOutput>;
}

/**
 * Check backend health.
 *
 * @returns true if the backend reports status "ok".
 */
export async function checkHealth(): Promise<boolean> {
  try {
    const base = getBaseUrl();
    const res = await fetch(`${base}/health`, { cache: "no-store" });
    if (!res.ok) return false;
    const body = (await res.json()) as { status?: string };
    return body.status === "ok";
  } catch {
    return false;
  }
}

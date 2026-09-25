/**
 * Client for the local FastAPI backend, reached through the dev-only
 * `/api/*` rewrite in next.config.ts (scripts/run_local.sh on :8000).
 *
 * Two details the rewrite imposes, both learned the hard way:
 * - rewrite sources are basePath-relative, so the URL must carry
 *   `/signals-app` — a bare `/api/scan` falls through to a 404;
 * - `trailingSlash: true` 308-redirects slashless paths, so the URL ends in
 *   `/` up front rather than relying on the redirect.
 *
 * Not available on the deployed static export.
 */
import { ApiError } from "./api";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** Browser URL for backend route `path` (e.g. "backtest/run"). */
export function backendUrl(path: string): string {
  const clean = path.replace(/^\/+|\/+$/g, "");
  return `${BASE_PATH}/api/${clean}/`;
}

/**
 * POST JSON to a backend route and return the parsed body.
 *
 * @throws ApiError(503) when the backend isn't reachable (not running, or
 *   the static site); ApiError(status) with FastAPI's `detail` otherwise.
 */
export async function postBackend<T>(
  path: string,
  body: unknown,
  label: string,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(backendUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(503, backendUnreachableMessage(path));
  }
  const parsed = (await res.json().catch(() => null)) as
    | (T & { detail?: unknown })
    | null;
  if (!res.ok) {
    // 404 from the Next dev server itself = the rewrite has nowhere to go.
    if (res.status === 404 && parsed == null) {
      throw new ApiError(503, backendUnreachableMessage(path));
    }
    throw new ApiError(res.status, `${label} failed: ${formatDetail(parsed?.detail) ?? res.statusText}`);
  }
  return parsed as T;
}

function backendUnreachableMessage(path: string): string {
  return (
    `Local backend not reachable at /api/${path} — start it with ` +
    "`scripts/run_local.sh` (needs `next dev`, not the static export)."
  );
}

/** FastAPI puts a string in `detail` for HTTPException, a list for 422s. */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
      .join("; ");
  }
  return null;
}

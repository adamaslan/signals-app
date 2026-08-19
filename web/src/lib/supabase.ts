/**
 * Supabase client for the signals-app frontend.
 *
 * Reads go straight from the browser to Supabase — no backend hop (see
 * docs/backend-state-and-supabase-plan.md Part 2). The anon key is safe in
 * a client bundle because RLS is on: this key can only read `signals`,
 * `symbols`, active `calibration` rows, and `engine_runs`, and can only
 * touch `profiles`/`watchlist` rows the signed-in user owns. It can never
 * write to `signals` or `detector_hits` — those require the service-role
 * key, which lives only in GitHub Actions secrets.
 */
import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const supabaseConfigured = Boolean(url && anonKey);

/**
 * Lazily-constructed singleton. Throws only if actually called without
 * config — importing this module never throws, so pages that don't need
 * Supabase (or run before env vars are set) aren't broken by the import.
 */
export const supabase = supabaseConfigured
  ? createClient(url as string, anonKey as string)
  : null;

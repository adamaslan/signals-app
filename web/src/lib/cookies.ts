/**
 * Lightweight cookie mirror of a few profile fields.
 *
 * The authoritative local store is the IndexedDB DB (db.ts), but IndexedDB is
 * client-only and async — the server can't read it during SSR. This cookie
 * holds a tiny, non-sensitive subset (name, last ticker, last signal) so the
 * server can render a personalised greeting on first paint, before the client
 * DB hydrates. It is written client-side and read on both sides.
 *
 * Nothing here is a credential; it's pure UX convenience and stays on-device
 * (SameSite=Lax, not HttpOnly, so the client can manage it).
 */
import type { SignalDirection } from "./types";

export const COOKIE_NAME = "sa_profile";
const MAX_AGE_DAYS = 400; // browsers cap at ~400 days

export interface ProfileCookie {
  name: string;
  lastTicker: string | null;
  lastSignal: SignalDirection | null;
  lastSeen: number;
}

/** Serialise + write the cookie (client-side only). */
export function writeProfileCookie(data: ProfileCookie): void {
  if (typeof document === "undefined") return;
  const value = encodeURIComponent(JSON.stringify(data));
  const maxAge = MAX_AGE_DAYS * 24 * 60 * 60;
  document.cookie = `${COOKIE_NAME}=${value}; path=/; max-age=${maxAge}; SameSite=Lax`;
}

/** Parse a raw Cookie header / document.cookie string into the profile. */
export function parseProfileCookie(raw: string | undefined): ProfileCookie | null {
  if (!raw) return null;
  const match = raw
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${COOKIE_NAME}=`));
  if (!match) return null;
  try {
    const json = decodeURIComponent(match.slice(COOKIE_NAME.length + 1));
    return JSON.parse(json) as ProfileCookie;
  } catch {
    return null;
  }
}

/** Read the cookie from document.cookie (client-side). */
export function readProfileCookieClient(): ProfileCookie | null {
  if (typeof document === "undefined") return null;
  return parseProfileCookie(document.cookie);
}

/** Delete the cookie (part of "forget me"). */
export function clearProfileCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax`;
}

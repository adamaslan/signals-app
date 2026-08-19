"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * GitHub Pages SPA deep-link handler.
 *
 * Pairs with public/404.html: when GitHub Pages 404s on a deep link (e.g.
 * /signals-app/signals/AAPL/), that page encodes the path RELATIVE TO THE
 * BASEPATH (/signals/AAPL/, not /signals-app/signals/AAPL/) as ?p=... and
 * redirects to the site root (/signals-app/).
 *
 * A bare `history.replaceState` here is not enough — it rewrites the address
 * bar but Next's client router has no idea the URL changed underneath it, so
 * the already-mounted homepage stays on screen. `router.replace()` performs
 * a real client-side navigation, which is what actually swaps in the right
 * route (e.g. /signals/[symbol]).
 *
 * `p` is already basePath-relative (that's what 404.html encodes), and
 * next/navigation's router.replace() auto-prepends next.config.ts's
 * basePath to whatever path it's given — so `p` is passed straight through
 * unmodified. Manually prepending basePath here as well double-prefixes it
 * (/signals-app/signals-app/...) and sends the router into an infinite
 * navigate loop, since the mismatched URL never resolves to a real route.
 *
 * Guard: only accept relative paths (/foo) — reject protocol-relative (//evil).
 */
export function SpaRedirect() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const p = params.get("p");
    if (!p || !/^\/[^/]/.test(p)) return;

    const q = params.get("q");
    const target =
      p.replace(/~and~/g, "&") +
      (q ? "?" + q.replace(/~and~/g, "&") : "") +
      window.location.hash;

    router.replace(target);
  }, [router]);

  return null;
}

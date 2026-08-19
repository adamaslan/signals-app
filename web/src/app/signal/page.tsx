import { Suspense } from "react";
import { SignalPageClient } from "./_client";

/**
 * A single static page (`/signal/`, no dynamic segment) that reads the
 * ticker from a query param (?symbol=AAPL) instead of a route param
 * (/signals/[symbol]). Every ticker resolves to the same prerendered HTML;
 * SignalPageClient reads `symbol`/`period`/`no_llm` from useSearchParams and
 * fetches from Supabase client-side.
 *
 * This replaces an earlier dynamic-route design
 * (/signals/[symbol]/?period=...) that fought `output: export` on two
 * fronts: generateStaticParams needing a non-empty array to satisfy Next's
 * export validator, and — the harder problem — router.push()/replace() to
 * an un-prerendered dynamic route always attempting a server-side RSC
 * fetch, which a static export has no server to answer. A query-param page
 * has neither problem: GitHub Pages serves this one URL directly for every
 * symbol, no 404-redirect trick needed.
 */
export default function SignalPage() {
  return (
    <Suspense>
      <SignalPageClient />
    </Suspense>
  );
}

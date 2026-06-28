import { SignalPageClient } from "./_client";

interface PageProps {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<{ period?: string; no_llm?: string }>;
}

/**
 * Return an empty list so Next.js accepts this dynamic route during
 * `next build --output export`. Pages are not pre-rendered at build time;
 * data is fetched client-side by SignalPageClient on first mount.
 * Direct deep links work via the public/404.html SPA redirect trick.
 */
export function generateStaticParams() {
  return [];
}

export default async function SignalPage({ params, searchParams }: PageProps) {
  const { symbol } = await params;
  const sp = await searchParams;

  return (
    <SignalPageClient
      symbol={symbol.toUpperCase()}
      period={sp.period ?? "3mo"}
      noLlm={sp.no_llm === "true"}
    />
  );
}

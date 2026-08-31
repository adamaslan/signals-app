"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { UniverseListPanel } from "@/components/UniverseListPanel";
import { UniverseEditor } from "@/components/UniverseEditor";

export function UniversePageClient() {
  const params = useSearchParams();
  const idParam = params.get("id");
  const id = idParam != null ? Number(idParam) : null;

  if (id != null && Number.isFinite(id)) {
    return (
      <div className="space-y-4">
        <Link
          href="/universe/"
          className="text-gray-500 hover:text-gray-300 text-sm transition-colors"
        >
          ← All universes
        </Link>
        <UniverseEditor universeId={id} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Link
          href="/"
          className="text-gray-500 hover:text-gray-300 text-sm transition-colors"
        >
          ← Home
        </Link>
      </div>
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-white">
          Universes
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Named baskets of tickers you can track over time and backtest. All
          stored on this device — no account needed.
        </p>
      </div>
      <UniverseListPanel />
    </div>
  );
}

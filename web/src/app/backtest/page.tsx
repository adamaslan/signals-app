import { Suspense } from "react";
import { BacktestLabClient } from "./_client";

/**
 * Backtest Lab (`/backtest/`). Reads its basket and optional hypothesis spec
 * from the query string (`?symbols=&h=&period=&focus=&suggest=1`) — the same
 * query-param approach as /universe/ so it survives `output: export`.
 */
export default function BacktestPage() {
  return (
    <Suspense>
      <BacktestLabClient />
    </Suspense>
  );
}

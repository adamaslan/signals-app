import { TickerSearch } from "@/components/TickerSearch";
import { Greeting } from "@/components/Greeting";
import { RecentRunsTable } from "@/components/RecentRunsTable";
import { WatchlistPanel } from "@/components/WatchlistPanel";

export default function HomePage() {
  return (
    <div className="space-y-12">
      <div className="flex flex-col items-center justify-center gap-6 pt-6">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-extrabold tracking-tight text-white">
            Market Signal Analysis
          </h1>
          <p className="text-gray-400 text-base">
            Enter a ticker to run the full AI-powered signal pipeline
          </p>
        </div>

        <Greeting />
        <TickerSearch />
      </div>

      {/* Local-DB powered dashboard sections */}
      <div className="grid gap-8 md:grid-cols-3">
        <section className="md:col-span-2 space-y-3">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Recent Runs
          </h2>
          <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
            <RecentRunsTable />
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
            Watchlist
          </h2>
          <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
            <WatchlistPanel />
          </div>
        </section>
      </div>
    </div>
  );
}

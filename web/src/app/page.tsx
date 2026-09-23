import { TickerSearch } from "@/components/TickerSearch";
import { Greeting } from "@/components/Greeting";
import { RecentRunsTable } from "@/components/RecentRunsTable";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { LandingDataProvider } from "@/components/landing/LandingData";
import { Hero } from "@/components/landing/Hero";
import { ExampleChips } from "@/components/landing/ExampleChips";
import { TopSignals } from "@/components/landing/TopSignals";
import { HeatmapPreview } from "@/components/landing/HeatmapPreview";
import { PipelineFunnel } from "@/components/landing/PipelineFunnel";
import { TrackRecord } from "@/components/landing/TrackRecord";
import { FeaturedDeepDive } from "@/components/landing/FeaturedDeepDive";
import { UniverseCta } from "@/components/landing/UniverseCta";
import { LandingFooter } from "@/components/landing/Footer";

export default function HomePage() {
  return (
    <LandingDataProvider>
      <div className="space-y-10">
        <div className="flex flex-col items-center justify-center gap-5 pt-4">
          <Hero />
          <Greeting />
          <TickerSearch />
          <ExampleChips />
        </div>

        <TopSignals />
        <HeatmapPreview />
        <PipelineFunnel />
        <TrackRecord />
        <FeaturedDeepDive />
        <UniverseCta />

        {/* Personal, device-local state sits below the shared showcase. */}
        <div data-testid="landing-activity" className="grid gap-8 md:grid-cols-3">
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

        <LandingFooter />
      </div>
    </LandingDataProvider>
  );
}

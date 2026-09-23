import Link from "next/link";
import { Section } from "./Shared";

export function UniverseCta() {
  return (
    <Section testId="landing-universe-cta" title="Build your own universe">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <p className="text-sm text-gray-400">
          Save a basket of tickers, run a scan over it, backtest the detectors on your names,
          and diff one run against the next. Everything stays on this device.
        </p>
        <Link
          href="/universe/"
          className="shrink-0 rounded-lg bg-white/10 px-4 py-2 text-sm font-semibold text-white hover:bg-white/20 text-center"
        >
          Open Universes →
        </Link>
      </div>
    </Section>
  );
}

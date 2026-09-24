import Link from "next/link";

const EXAMPLES = ["SPY", "NVDA", "XOM", "BTC-USD"];

export function ExampleChips() {
  return (
    <div data-testid="landing-example-chips" className="flex flex-wrap justify-center gap-2 text-xs">
      <span className="text-gray-600">Try:</span>
      {EXAMPLES.map((t) => (
        <Link
          key={t}
          href={`/signal/?symbol=${t}&period=3mo`}
          className="rounded-full border border-white/10 px-3 py-1 text-gray-300 hover:border-white/30 hover:text-white transition-colors"
        >
          {t}
        </Link>
      ))}
    </div>
  );
}

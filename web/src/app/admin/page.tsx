import { AdminBacktestHistory } from "@/components/AdminBacktestHistory";

export default function AdminPage() {
  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <a href="/" className="text-gray-500 hover:text-gray-300 text-sm">
          ← Home
        </a>
        <h1 className="text-3xl font-extrabold tracking-tight text-white mt-1">
          Admin
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Backtest history for this device&apos;s profile. Local-only — no
          account required.
        </p>
      </div>
      <AdminBacktestHistory />
    </div>
  );
}

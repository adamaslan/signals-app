import type { ReactNode } from "react";

/** Common frame for a showcase section: heading, testid hook, card body. */
export function Section({
  testId,
  title,
  aside,
  children,
}: {
  testId: string;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section data-testid={testId} className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-gray-400 text-xs font-semibold uppercase tracking-widest">
          {title}
        </h2>
        {aside}
      </div>
      <div className="rounded-xl bg-[#1a1a2e] border border-white/5 p-4">
        {children}
      </div>
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-gray-600 text-sm">{children}</p>;
}

export function SkeletonBlock({ height = "h-24" }: { height?: string }) {
  return <div className={`${height} animate-pulse rounded-lg bg-white/5`} />;
}

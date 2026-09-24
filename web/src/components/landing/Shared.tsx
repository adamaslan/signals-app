"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";

/** Common frame for a showcase section: heading, testid hook, card body. */
export function Section({
  testId,
  title,
  aside,
  children,
  sectionRef,
}: {
  testId: string;
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  sectionRef?: RefObject<HTMLElement | null>;
}) {
  return (
    <section data-testid={testId} ref={sectionRef} className="space-y-3 min-w-0">
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

/**
 * True once the element has come within `rootMargin` of the viewport.
 * Sticky: never flips back. Falls back to true where IntersectionObserver is
 * missing, so below-the-fold sections still load.
 */
export function useInView<T extends Element>(
  rootMargin = "200px",
): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [rootMargin]);

  return [ref, inView];
}

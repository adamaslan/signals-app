"use client";

import { Component, type ReactNode } from "react";

interface ErrorBoundaryProps {
  /** Shown in the fallback message, e.g. "heatmap" or "deep-dive matrix". */
  label: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Isolates one section of the page from a rendering crash in another. A bad
 * row (e.g. an unexpected `divergence_pattern`) should blank that one
 * section, never the whole page — see §C in
 * docs/frontend-robustness-large-universe.md.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-3 py-2 text-red-400 text-xs">
          {this.props.label} failed to render: {this.state.error.message}
        </div>
      );
    }
    return this.props.children;
  }
}

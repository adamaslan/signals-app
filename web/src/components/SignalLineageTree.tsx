"use client";

/**
 * Signal lineage tree for a ticker.
 *
 * A "lineage node" is a streak of consecutive runs that share the same
 * direction. When direction changes, a new child node branches off from the
 * previous streak. This gives a visual parent→child tree that shows how the
 * signal evolved over time:
 *
 *   BUY (3 runs, Jun 1–3)
 *   └── HOLD (2 runs, Jun 4–5)
 *       └── STRONG BUY (1 run, Jun 6)  ← current
 *
 * Powered by Dexie useLiveQuery so it updates live after each new run.
 */
import { useLiveQuery } from "dexie-react-hooks";
import { db, type HistoryEntry } from "@/lib/db";
import {
  SIGNAL_COLORS,
  SIGNAL_LABELS,
  SIGNAL_ARROWS,
  type SignalDirection,
} from "@/lib/types";

/** One node in the lineage tree: a consecutive streak of the same direction. */
interface LineageNode {
  id: string;
  direction: SignalDirection | null;
  runs: HistoryEntry[];
  /** 0-based depth from the root. */
  depth: number;
  /** Index of parent node in the flat nodes array, or -1 for root. */
  parentIndex: number;
}

/**
 * Build a flat ordered list of lineage nodes from a newest-first run list.
 * We reverse to oldest-first, group consecutive same-direction runs, then
 * return oldest-to-newest so the tree renders root-at-top.
 */
function buildLineage(runs: HistoryEntry[]): LineageNode[] {
  if (runs.length === 0) return [];

  // Work oldest-first
  const ordered = [...runs].reverse();
  const nodes: LineageNode[] = [];

  let currentNode: LineageNode | null = null;

  for (const run of ordered) {
    const dir = run.signal as SignalDirection | null;

    if (!currentNode || currentNode.direction !== dir) {
      const depth: number = currentNode ? currentNode.depth + 1 : 0;
      const parentIndex: number = currentNode ? nodes.length - 1 : -1;
      currentNode = {
        id: `node-${nodes.length}`,
        direction: dir,
        runs: [run],
        depth,
        parentIndex,
      };
      nodes.push(currentNode);
    } else {
      currentNode.runs.push(run);
    }
  }

  return nodes;
}

function formatDateRange(runs: HistoryEntry[]): string {
  if (runs.length === 0) return "";
  const first = new Date(runs[0].ts);
  const last = new Date(runs[runs.length - 1].ts);

  const fmt = (d: Date) =>
    d.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  if (runs.length === 1) return fmt(first);
  return `${fmt(first)} – ${fmt(last)}`;
}

function avgConfidence(runs: HistoryEntry[]): number | null {
  const valid = runs.filter((r) => r.confidence != null);
  if (valid.length === 0) return null;
  const sum = valid.reduce((a, r) => a + (r.confidence ?? 0), 0);
  return Math.round((sum / valid.length) * 100);
}

interface NodeCardProps {
  node: LineageNode;
  isLatest: boolean;
}

function NodeCard({ node, isLatest }: NodeCardProps) {
  const dir = node.direction;
  const color = dir ? SIGNAL_COLORS[dir] : "#555";
  const pct = avgConfidence(node.runs);
  const dateRange = formatDateRange(node.runs);
  const count = node.runs.length;

  return (
    <div
      className="rounded-lg px-3 py-2.5 text-sm transition-all"
      style={{
        backgroundColor: `${color}10`,
        border: `1px solid ${color}${isLatest ? "88" : "33"}`,
        boxShadow: isLatest ? `0 0 12px ${color}22` : "none",
      }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        {dir ? (
          <span className="font-bold text-base" style={{ color }}>
            {SIGNAL_ARROWS[dir]} {SIGNAL_LABELS[dir]}
          </span>
        ) : (
          <span className="text-gray-500 font-medium">— no signal</span>
        )}
        {isLatest && (
          <span
            className="text-[10px] rounded-full px-1.5 py-0.5 font-semibold uppercase tracking-wide"
            style={{
              backgroundColor: `${color}22`,
              color,
              border: `1px solid ${color}55`,
            }}
          >
            current
          </span>
        )}
      </div>

      <div className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
        <span>
          {count} run{count !== 1 ? "s" : ""}
        </span>
        {pct != null && <span>avg {pct}% conf.</span>}
        {dateRange && <span>{dateRange}</span>}
      </div>
    </div>
  );
}

interface SignalLineageTreeProps {
  ticker: string;
  limit?: number;
}

export function SignalLineageTree({ ticker, limit = 50 }: SignalLineageTreeProps) {
  const runs = useLiveQuery(
    async () => {
      if (!db) return [];
      return db.history
        .where("ticker")
        .equals(ticker)
        .reverse()
        .sortBy("ts")
        .then((rows) => rows.slice(0, limit));
    },
    [ticker, limit],
    [] as HistoryEntry[],
  );

  if (!runs || runs.length === 0) {
    return (
      <p className="text-gray-600 text-sm">
        No lineage yet — run an analysis to build the signal tree.
      </p>
    );
  }

  const nodes = buildLineage(runs);

  if (nodes.length === 0) return null;

  return (
    <div className="space-y-1">
      <p className="text-gray-600 text-xs mb-3">
        {nodes.length} direction streak{nodes.length !== 1 ? "s" : ""} from{" "}
        {runs.length} run{runs.length !== 1 ? "s" : ""}
      </p>

      <ol className="space-y-0">
        {nodes.map((node, i) => {
          const isLatest = i === nodes.length - 1;
          const indent = node.depth * 20;

          return (
            <li key={node.id}>
              {/* Connector line + branch arrow, skipped for root */}
              {node.depth > 0 && (
                <div
                  className="flex items-center"
                  style={{ paddingLeft: indent - 20 }}
                  aria-hidden
                >
                  <div
                    className="w-4 h-4 border-l border-b rounded-bl"
                    style={{ borderColor: "rgba(255,255,255,0.10)" }}
                  />
                </div>
              )}

              <div style={{ paddingLeft: indent }}>
                <NodeCard node={node} isLatest={isLatest} />
              </div>

              {/* Vertical connector to next sibling/child */}
              {i < nodes.length - 1 && (
                <div
                  className="w-px h-3 bg-white/10"
                  style={{
                    marginLeft: Math.min(nodes[i + 1].depth, node.depth) * 20 + 12,
                  }}
                  aria-hidden
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

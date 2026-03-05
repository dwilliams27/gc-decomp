import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useWorkerStore } from "../../stores/workerStore";
import { api } from "../../api/client";
import { matchColor } from "../../utils/colors";

interface TickerEntry {
  key: string;
  name: string;
  matched: boolean;
  matchPct: number;
  ts: number;
}

const MAX_ENTRIES = 8;

export function ActivityTicker() {
  const workers = useWorkerStore((s) => s.workers);
  const [entries, setEntries] = useState<TickerEntry[]>([]);
  const seenRef = useRef<Set<string>>(new Set());

  // Poll batch API for recent completions (works for CLI-started batches)
  const { data: batch } = useQuery({
    queryKey: ["batch-status"],
    queryFn: api.getBatchStatus,
    refetchInterval: 5_000,
  });

  // Ingest from workerStore (real-time WebSocket events)
  useEffect(() => {
    const newEntries: TickerEntry[] = [];
    for (const [name, w] of Object.entries(workers)) {
      if (
        (w.status === "matched" ||
          w.status === "failed" ||
          w.status === "crashed") &&
        !seenRef.current.has(name)
      ) {
        seenRef.current.add(name);
        newEntries.push({
          key: `${name}-${w.lastEventTs}`,
          name,
          matched: w.status === "matched",
          matchPct: w.matchPct,
          ts: w.lastEventTs,
        });
      }
    }

    if (newEntries.length > 0) {
      setEntries((prev) => [...newEntries, ...prev].slice(0, MAX_ENTRIES));
    }
  }, [workers]);

  // Ingest from batch API polling (for CLI-started batches)
  useEffect(() => {
    if (!batch?.recent_completed) return;

    const newEntries: TickerEntry[] = [];
    for (const item of batch.recent_completed) {
      const name = String(item.function_name ?? "");
      if (!name || seenRef.current.has(name)) continue;
      seenRef.current.add(name);
      newEntries.push({
        key: `${name}-poll`,
        name,
        matched: item.matched as boolean,
        matchPct: (item.best_match_pct as number) ?? 0,
        ts: Date.now() / 1000,
      });
    }

    if (newEntries.length > 0) {
      setEntries((prev) => [...newEntries, ...prev].slice(0, MAX_ENTRIES));
    }
  }, [batch?.recent_completed]);

  if (entries.length === 0) {
    return (
      <div className="w-80 rounded-lg bg-gray-900/80 px-4 py-3 text-xs text-gray-600 backdrop-blur-sm">
        Waiting for completed functions...
      </div>
    );
  }

  return (
    <div className="w-80 rounded-lg bg-gray-900/80 p-3 backdrop-blur-sm">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Recent Activity
      </div>
      <div className="space-y-1 overflow-hidden">
        {entries.map((entry) => (
          <div
            key={entry.key}
            className="ticker-slide-in flex items-center gap-2 text-xs"
          >
            <span
              className={
                entry.matched ? "text-green-400" : "text-yellow-400"
              }
            >
              {entry.matched ? "\u2713" : "\u2717"}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-gray-300">
              {entry.name}
            </span>
            <span
              className="font-mono"
              style={{ color: matchColor(entry.matchPct) }}
            >
              {entry.matchPct.toFixed(0)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

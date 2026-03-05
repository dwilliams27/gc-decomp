import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export function RateDisplay() {
  const { data: batch } = useQuery({
    queryKey: ["batch-status"],
    queryFn: api.getBatchStatus,
    refetchInterval: 5_000,
  });

  const { data: overview } = useQuery({
    queryKey: ["overview"],
    queryFn: api.getOverview,
    refetchInterval: 10_000,
  });

  // Client-side elapsed counter for smooth ticking
  const [elapsed, setElapsed] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  useEffect(() => {
    if (batch?.running && batch.started_at) {
      setStartedAt(batch.started_at);
    } else if (!batch?.running) {
      setStartedAt(null);
      if (batch?.elapsed) {
        setElapsed(Math.round(batch.elapsed));
      }
    }
  }, [batch?.running, batch?.started_at, batch?.elapsed]);

  useEffect(() => {
    if (!startedAt) return;
    const tick = () => {
      setElapsed(Math.floor(Date.now() / 1000 - startedAt));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const hours = Math.floor(elapsed / 3600);
  const mins = Math.floor((elapsed % 3600) / 60);
  const secs = elapsed % 60;
  const timeStr = `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;

  const attempted = batch?.attempted ?? 0;
  const matched = batch?.matched ?? 0;
  const elapsedHrs = elapsed / 3600;
  const fnPerHour = elapsedHrs > 0.01 ? attempted / elapsedHrs : 0;
  const matchRate = elapsedHrs > 0.01 ? matched / elapsedHrs : 0;

  const totalFunctions = overview?.total_functions ?? 0;
  const totalMatched = overview?.status_counts?.matched ?? 0;
  const overallPct =
    totalFunctions > 0
      ? ((totalMatched / totalFunctions) * 100).toFixed(1)
      : "0.0";

  return (
    <div className="rounded-lg bg-gray-900/80 px-6 py-3 text-center backdrop-blur-sm">
      <div className="font-mono text-3xl font-bold tabular-nums text-white">
        {timeStr}
      </div>
      <div className="mt-1 flex items-center justify-center gap-4 text-xs text-gray-400">
        <span>
          <span className="font-mono text-white">
            {fnPerHour.toFixed(1)}
          </span>{" "}
          fn/hr
        </span>
        <span className="text-gray-600">|</span>
        <span>
          <span className="font-mono text-green-400">
            {matchRate.toFixed(1)}
          </span>{" "}
          matches/hr
        </span>
        <span className="text-gray-600">|</span>
        <span>
          <span className="font-mono text-green-400">{overallPct}%</span>{" "}
          overall
        </span>
      </div>
    </div>
  );
}

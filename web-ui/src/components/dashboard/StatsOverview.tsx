import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MatchDistribution } from "./MatchDistribution";

export function StatsOverview() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["overview"],
    queryFn: api.getOverview,
    refetchInterval: 10_000,
  });

  if (isLoading) return <div className="text-gray-500 text-sm">Loading stats...</div>;
  if (error) return <div className="text-red-500 text-sm">Error: {(error as Error).message}</div>;
  if (!data) return null;

  const matched = data.status_counts["matched"] || 0;
  const pending = data.status_counts["pending"] || 0;
  const failed = data.status_counts["failed"] || 0;
  const inProgress = data.status_counts["in_progress"] || 0;
  const matchPct = data.total_bytes > 0 ? (data.matched_bytes / data.total_bytes * 100) : 0;

  return (
    <div className="mb-4">
      <h2 className="mb-2 text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Overview
      </h2>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <Stat label="Functions" value={data.total_functions.toLocaleString()} />
        <Stat label="Matched" value={matched.toLocaleString()} color="text-green-400" />
        <Stat label="Pending" value={pending.toLocaleString()} color="text-yellow-400" />
        <Stat label="In Progress" value={inProgress.toLocaleString()} color="text-blue-400" />
        <Stat label="Failed" value={failed.toLocaleString()} color="text-red-400" />
        <Stat label="Attempts" value={data.total_attempts.toLocaleString()} />
        <Stat label="Tokens" value={formatNumber(data.total_tokens)} />
        <Stat label="Cost" value={`$${data.total_cost.toFixed(2)}`} />
      </div>

      {/* Byte progress bar */}
      <div className="mt-3">
        <div className="flex justify-between text-xs text-gray-500">
          <span>Bytes matched</span>
          <span>{matchPct.toFixed(1)}%</span>
        </div>
        <div className="mt-1 h-2 w-full rounded bg-gray-800">
          <div
            className="h-2 rounded bg-green-500"
            style={{ width: `${Math.min(matchPct, 100)}%` }}
          />
        </div>
      </div>

      <MatchDistribution histogram={data.match_histogram} />
    </div>
  );
}

function Stat({
  label,
  value,
  color = "text-white",
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div>
      <div className="text-gray-500 text-xs">{label}</div>
      <div className={`font-mono ${color}`}>{value}</div>
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

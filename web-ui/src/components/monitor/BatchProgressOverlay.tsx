import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export function BatchProgressOverlay() {
  const { data } = useQuery({
    queryKey: ["batch-status"],
    queryFn: api.getBatchStatus,
    refetchInterval: 5_000,
  });

  if (!data || (!data.running && !data.attempted)) {
    return (
      <div className="rounded-lg bg-gray-900/80 px-4 py-3 text-sm text-gray-500 backdrop-blur-sm">
        No batch running
      </div>
    );
  }

  const attempted = data.attempted ?? 0;
  const matched = data.matched ?? 0;
  const failed = data.failed ?? 0;
  const cost = data.total_cost ?? 0;
  const tokens = data.total_tokens ?? 0;
  const total = data.params?.limit ?? attempted;
  const progressPct = total > 0 ? Math.min((attempted / total) * 100, 100) : 0;

  return (
    <div className="w-64 rounded-lg bg-gray-900/80 p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
          {data.running ? "Batch Running" : "Batch Complete"}
        </span>
        {data.running && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
        )}
      </div>

      {/* Progress bar */}
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-800">
        <div
          className="h-full rounded-full bg-blue-500 transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>
      <div className="mt-1 text-right text-xs text-gray-500">
        {attempted} / {total}
      </div>

      {/* Stats grid */}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <Stat label="Matched" value={String(matched)} color="text-green-400" />
        <Stat label="Failed" value={String(failed)} color="text-red-400" />
        <Stat label="Cost" value={`$${cost.toFixed(2)}`} />
        <Stat
          label="Tokens"
          value={`${(tokens / 1_000_000).toFixed(1)}M`}
        />
      </div>
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
    <div className="flex items-baseline justify-between">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color}`}>{value}</span>
    </div>
  );
}

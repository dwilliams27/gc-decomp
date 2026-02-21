import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

export function BatchMonitor() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["batch-status"],
    queryFn: api.getBatchStatus,
    refetchInterval: 2_000,
  });

  const cancelMutation = useMutation({
    mutationFn: api.cancelBatch,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batch-status"] });
    },
  });

  if (isLoading) {
    return <div className="text-sm text-gray-500">Loading batch status...</div>;
  }

  if (!data || !data.running && !data.attempted) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        No batch running. Configure and start one from the left panel.
      </div>
    );
  }

  return (
    <div className="rounded border border-gray-800 bg-gray-900 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-white">
          {data.running ? "Batch Running" : "Batch Complete"}
        </h2>
        {data.running && (
          <button
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending || data.cancelled}
            className="rounded bg-red-700 px-3 py-1 text-sm text-white hover:bg-red-600 disabled:opacity-50"
          >
            {data.cancelled ? "Cancelling..." : "Cancel"}
          </button>
        )}
      </div>

      {data.params && (
        <div className="mt-2 flex flex-wrap gap-2 text-xs text-gray-500">
          {Object.entries(data.params).map(
            ([k, v]) =>
              v !== null && (
                <span key={k} className="rounded bg-gray-800 px-2 py-0.5">
                  {k}: {String(v)}
                </span>
              ),
          )}
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-4 text-sm md:grid-cols-5">
        <Stat
          label="Attempted"
          value={String(data.attempted ?? 0)}
        />
        <Stat
          label="Matched"
          value={String(data.matched ?? 0)}
          color="text-green-400"
        />
        <Stat
          label="Failed"
          value={String(data.failed ?? 0)}
          color="text-red-400"
        />
        <Stat
          label="Cost"
          value={`$${(data.total_cost ?? 0).toFixed(2)}`}
        />
        <Stat
          label="Tokens"
          value={((data.total_tokens ?? 0) / 1000).toFixed(0) + "K"}
        />
      </div>

      {data.running && (data.current_functions?.length ?? 0) > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Currently Running
          </h3>
          <div className="mt-1 space-y-1">
            {data.current_functions!.map((fn) => (
              <div
                key={fn}
                className="flex items-center gap-2 text-sm text-blue-400"
              >
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                {fn}
              </div>
            ))}
          </div>
        </div>
      )}

      {(data.recent_completed?.length ?? 0) > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Recently Completed
          </h3>
          <div className="mt-1 space-y-1 text-sm overflow-hidden">
            {data.recent_completed!.map((item: Record<string, unknown>, i) => {
              const matched = item.matched as boolean;
              return (
                <div
                  key={i}
                  className="flex items-baseline gap-2 text-gray-400 break-words min-w-0"
                >
                  <span className={matched ? "text-green-400" : "text-yellow-400"}>
                    {matched ? "\u2713" : "\u2717"}
                  </span>
                  <span className="font-mono text-xs truncate">
                    {String(item.function_name ?? "")}
                  </span>
                  <span className="text-xs">
                    {(item.best_match_pct as number)?.toFixed(1) ?? 0}%
                  </span>
                  <span className="text-xs text-gray-600">
                    ${(item.cost as number)?.toFixed(2)} · {item.elapsed}s
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
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
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`text-lg font-mono ${color}`}>{value}</div>
    </div>
  );
}

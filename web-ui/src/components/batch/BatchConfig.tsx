import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { BatchParams } from "../../api/types";

export function BatchConfig() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: ["config"],
    queryFn: api.getConfig,
  });

  const [params, setParams] = useState<BatchParams>({
    limit: 50,
    max_size: null,
    budget: null,
    workers: 1,
    strategy: "smallest_first",
    library: null,
    min_match: null,
    max_match: null,
    max_tokens: null,
  });

  // Populate defaults from config
  const defaults = config?.orchestration;
  const effectiveLimit = params.limit || defaults?.batch_size || 50;
  const effectiveWorkers = params.workers || defaults?.default_workers || 1;
  const defaultMaxTokens = config?.agent?.max_tokens_per_attempt ?? null;
  const effectiveMaxTokens = params.max_tokens ?? defaultMaxTokens;

  const startMutation = useMutation({
    mutationFn: api.startBatch,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batch-status"] });
    },
  });

  const handleStart = () => {
    startMutation.mutate({
      ...params,
      limit: effectiveLimit,
      workers: effectiveWorkers,
      max_tokens: effectiveMaxTokens,
    });
  };

  return (
    <div className="rounded border border-gray-800 bg-gray-900 p-4">
      <h2 className="mb-4 text-lg font-bold text-white">Start Batch</h2>

      <div className="space-y-3 text-sm">
        <Field label="Limit">
          <input
            type="number"
            value={params.limit}
            onChange={(e) => setParams({ ...params, limit: Number(e.target.value) })}
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Max Size (bytes)">
          <input
            type="number"
            value={params.max_size ?? ""}
            onChange={(e) =>
              setParams({
                ...params,
                max_size: e.target.value ? Number(e.target.value) : null,
              })
            }
            placeholder="No limit"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Budget ($)">
          <input
            type="number"
            step="0.01"
            value={params.budget ?? ""}
            onChange={(e) =>
              setParams({
                ...params,
                budget: e.target.value ? Number(e.target.value) : null,
              })
            }
            placeholder="No limit"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Workers">
          <input
            type="number"
            min={1}
            max={8}
            value={params.workers}
            onChange={(e) => setParams({ ...params, workers: Number(e.target.value) })}
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Strategy">
          <select
            value={params.strategy}
            onChange={(e) => setParams({ ...params, strategy: e.target.value })}
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          >
            <option value="smallest_first">Smallest First</option>
            <option value="best_match_first">Best Match First</option>
          </select>
        </Field>

        <Field label="Library">
          <input
            type="text"
            value={params.library ?? ""}
            onChange={(e) =>
              setParams({
                ...params,
                library: e.target.value || null,
              })
            }
            placeholder="All libraries"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Min Match %">
          <input
            type="number"
            min={0}
            max={100}
            value={params.min_match ?? ""}
            onChange={(e) =>
              setParams({
                ...params,
                min_match: e.target.value ? Number(e.target.value) : null,
              })
            }
            placeholder="0"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Max Match %">
          <input
            type="number"
            min={0}
            max={100}
            value={params.max_match ?? ""}
            onChange={(e) =>
              setParams({
                ...params,
                max_match: e.target.value ? Number(e.target.value) : null,
              })
            }
            placeholder="100"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>

        <Field label="Max Tokens per Worker (MTok)">
          <input
            type="number"
            step={0.1}
            min={0.1}
            value={effectiveMaxTokens != null ? effectiveMaxTokens / 1_000_000 : ""}
            onChange={(e) =>
              setParams({
                ...params,
                max_tokens: e.target.value ? Math.round(Number(e.target.value) * 1_000_000) : null,
              })
            }
            placeholder="No limit"
            className="w-full rounded bg-gray-800 px-2 py-1 text-white"
          />
        </Field>
      </div>

      <button
        onClick={handleStart}
        disabled={startMutation.isPending}
        className="mt-4 w-full rounded bg-blue-600 px-4 py-2 text-sm font-bold text-white hover:bg-blue-500 disabled:opacity-50"
      >
        {startMutation.isPending ? "Starting..." : "Start Batch"}
      </button>

      {startMutation.isError && (
        <div className="mt-2 text-sm text-red-400">
          {(startMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-gray-400">{label}</span>
      {children}
    </label>
  );
}

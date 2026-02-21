import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useWorkerStore, type FunctionWorker } from "../../stores/workerStore";
import { useSelectionStore } from "../../stores/selectionStore";

export function WorkerView() {
  const workers = useWorkerStore((s) => s.workers);
  const activeOrder = useWorkerStore((s) => s.activeOrder);
  const clear = useWorkerStore((s) => s.clear);

  const { data: batchStatus } = useQuery({
    queryKey: ["batch-status"],
    queryFn: api.getBatchStatus,
    refetchInterval: 5_000,
  });
  const batchRunning = batchStatus?.running ?? false;

  const workerList = activeOrder
    .map((fn) => workers[fn])
    .filter(Boolean)
    .sort((a, b) => {
      if (a.status === "running" && b.status !== "running") return -1;
      if (b.status === "running" && a.status !== "running") return 1;
      return b.lastEventTs - a.lastEventTs;
    });

  if (workerList.length === 0) {
    return (
      <div className="text-sm text-gray-600 p-4 text-center">
        {batchRunning
          ? "Batch is running — waiting for live events..."
          : "No agent activity yet. Start a batch to see live progress."}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Agent Activity ({workerList.filter((w) => w.status === "running").length} running)
        </h3>
        <button onClick={clear} className="text-xs text-gray-600 hover:text-gray-400">
          Clear
        </button>
      </div>
      <div className="space-y-3">
        {workerList.map((worker) => (
          <WorkerCard key={worker.functionName} worker={worker} />
        ))}
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function WorkerCard({ worker }: { worker: FunctionWorker }) {
  const selectFunction = useSelectionStore((s) => s.selectFunction);

  const statusColor = {
    running: "text-blue-400",
    matched: "text-green-400",
    failed: "text-yellow-400",
    crashed: "text-red-400",
  }[worker.status];

  const statusIcon = {
    running: "\u25B6",
    matched: "\u2713",
    failed: "\u2717",
    crashed: "\u26A0",
  }[worker.status];

  const iterPct =
    worker.maxIterations > 0
      ? (worker.iteration / worker.maxIterations) * 100
      : 0;

  const tokenPct =
    worker.tokenBudget > 0
      ? (worker.tokens / worker.tokenBudget) * 100
      : 0;

  const recentTools = worker.toolCalls.slice(-8);

  return (
    <div className="rounded border border-gray-800 bg-gray-900 p-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-sm ${statusColor}`}>{statusIcon}</span>
          <span className="font-mono text-sm font-bold text-white">
            {worker.functionName}
          </span>
          {worker.sourceFile && (
            <span className="text-xs text-gray-600">{worker.sourceFile}</span>
          )}
        </div>
        <span className={`text-xs font-bold ${statusColor}`}>
          {worker.status.toUpperCase()}
        </span>
      </div>

      {/* Progress bars: 3 columns */}
      <div className="mt-2 grid grid-cols-3 gap-3">
        {/* Iteration */}
        <ProgressBar
          label="Iteration"
          value={worker.iteration}
          max={worker.maxIterations}
          display={`${worker.iteration}/${worker.maxIterations}`}
          pct={iterPct}
          color="bg-blue-600"
        />

        {/* Match */}
        <ProgressBar
          label="Match"
          value={worker.matchPct}
          max={100}
          display={`${worker.matchPct.toFixed(1)}%`}
          pct={Math.min(worker.matchPct, 100)}
          color={
            worker.matchPct >= 100
              ? "bg-green-500"
              : worker.matchPct >= 80
                ? "bg-lime-500"
                : worker.matchPct >= 50
                  ? "bg-yellow-500"
                  : "bg-orange-500"
          }
          displayClass="text-white"
        />

        {/* Token budget */}
        <ProgressBar
          label="Tokens"
          value={worker.tokens}
          max={worker.tokenBudget}
          display={
            worker.tokenBudget > 0
              ? `${formatTokens(worker.tokens)} / ${formatTokens(worker.tokenBudget)}`
              : formatTokens(worker.tokens)
          }
          pct={tokenPct}
          color={tokenPct > 90 ? "bg-red-500" : tokenPct > 70 ? "bg-amber-500" : "bg-cyan-600"}
        />
      </div>

      {/* Match history */}
      {worker.matchHistory.length > 0 && (
        <div className="mt-2 flex items-center gap-1 text-xs">
          <span className="text-gray-600">Match:</span>
          {worker.matchHistory.map((h, i) => (
            <span
              key={i}
              className={`font-mono ${
                h.matchPct >= 100 ? "text-green-400" : "text-blue-400"
              }`}
            >
              {h.matchPct.toFixed(0)}%
              {i < worker.matchHistory.length - 1 && (
                <span className="text-gray-700"> {"\u2192"} </span>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Tool calls */}
      {recentTools.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {recentTools.map((tc, i) => (
            <span
              key={i}
              className={`rounded px-1.5 py-0.5 text-xs ${
                tc.tool === "compile_and_check"
                  ? "bg-blue-900/50 text-blue-300"
                  : tc.tool === "write_function"
                    ? "bg-purple-900/50 text-purple-300"
                    : tc.tool === "get_diff"
                      ? "bg-amber-900/50 text-amber-300"
                      : "bg-gray-800 text-gray-400"
              }`}
            >
              {tc.tool}
            </span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="mt-2 flex items-center gap-3 text-xs text-gray-600">
        {worker.status === "running" && (
          <span>
            {((Date.now() / 1000 - worker.startedAt) / 60).toFixed(1)}m elapsed
          </span>
        )}
        {worker.status !== "running" && (
          <button
            onClick={() => {
              /* Would need function ID — search by name */
            }}
            className="text-blue-500 hover:text-blue-400"
          >
            View details →
          </button>
        )}
      </div>
    </div>
  );
}

function ProgressBar({
  label,
  display,
  pct,
  color,
  displayClass = "",
}: {
  label: string;
  value: number;
  max: number;
  display: string;
  pct: number;
  color: string;
  displayClass?: string;
}) {
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-500">
        <span>{label}</span>
        <span className={`font-mono ${displayClass}`}>{display}</span>
      </div>
      <div className="mt-0.5 h-1.5 w-full rounded bg-gray-800">
        <div
          className={`h-1.5 rounded transition-all duration-500 ${color}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

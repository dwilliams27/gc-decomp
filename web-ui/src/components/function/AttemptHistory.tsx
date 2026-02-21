import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { MatchHistoryChart } from "./MatchHistoryChart";

interface Props {
  functionId: number;
}

export function AttemptHistory({ functionId }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["attempts", functionId],
    queryFn: () => api.getFunctionAttempts(functionId),
  });

  if (isLoading) return <div className="text-sm text-gray-500">Loading attempts...</div>;
  if (error) return <div className="text-sm text-red-500">Error: {(error as Error).message}</div>;
  if (!data || data.attempts.length === 0) {
    return <div className="text-sm text-gray-500">No attempts yet.</div>;
  }

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Attempt History ({data.attempts.length})
      </h3>
      <div className="space-y-4">
        {data.attempts.map((attempt) => (
          <div
            key={attempt.id}
            className="rounded border border-gray-800 bg-gray-900 p-3"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 text-sm">
                <span
                  className={`font-bold ${
                    attempt.matched ? "text-green-400" : "text-yellow-400"
                  }`}
                >
                  {attempt.matched ? "MATCHED" : attempt.termination_reason}
                </span>
                <span className="text-gray-500">
                  {attempt.best_match_pct.toFixed(1)}%
                </span>
                <span className="text-gray-600">
                  {attempt.iterations} iters
                </span>
                <span className="text-gray-600">
                  {(attempt.total_tokens / 1000).toFixed(0)}K tokens
                </span>
                <span className="text-gray-600">
                  ${attempt.cost.toFixed(4)}
                </span>
                <span className="text-gray-600">
                  {attempt.elapsed_seconds.toFixed(1)}s
                </span>
              </div>
              <span className="text-xs text-gray-600">
                {attempt.model}
              </span>
            </div>

            {attempt.match_history.length > 0 && (
              <MatchHistoryChart history={attempt.match_history} />
            )}

            {Object.keys(attempt.tool_counts).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(attempt.tool_counts)
                  .sort(([, a], [, b]) => b - a)
                  .map(([tool, count]) => (
                    <span
                      key={tool}
                      className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-400"
                    >
                      {tool}: {count}
                    </span>
                  ))}
              </div>
            )}

            {attempt.error && (
              <div className="mt-2 text-xs text-red-400">Error: {attempt.error}</div>
            )}

            {attempt.final_code && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-300">
                  Final code
                </summary>
                <pre className="mt-1 max-h-64 overflow-auto rounded bg-gray-950 p-2 text-xs text-gray-300">
                  {attempt.final_code}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

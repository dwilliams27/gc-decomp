import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useSelectionStore } from "../../stores/selectionStore";
import { AttemptHistory } from "./AttemptHistory";

export function FunctionDetail() {
  const functionId = useSelectionStore((s) => s.selectedFunctionId);
  const setView = useSelectionStore((s) => s.setView);

  const { data: func, isLoading, error } = useQuery({
    queryKey: ["function", functionId],
    queryFn: () => api.getFunction(functionId!),
    enabled: functionId !== null,
  });

  if (functionId === null) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        Select a function from the treemap to view details.
      </div>
    );
  }

  if (isLoading) {
    return <div className="flex h-full items-center justify-center text-gray-500">Loading...</div>;
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-red-500">
        Error: {(error as Error).message}
      </div>
    );
  }

  if (!func) return null;

  const statusColor: Record<string, string> = {
    matched: "text-green-400",
    pending: "text-yellow-400",
    in_progress: "text-blue-400",
    failed: "text-red-400",
    skipped: "text-gray-500",
  };

  return (
    <div className="h-full overflow-y-auto p-4">
      <button
        onClick={() => setView("treemap")}
        className="mb-3 text-sm text-blue-400 hover:text-blue-300"
      >
        &larr; Back to treemap
      </button>

      <h2 className="text-xl font-bold text-white">{func.name}</h2>

      <div className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-4">
        <div>
          <span className="text-gray-500">Source:</span>{" "}
          <span className="font-mono text-gray-300">{func.source_file}</span>
        </div>
        <div>
          <span className="text-gray-500">Library:</span>{" "}
          <span className="text-gray-300">{func.library}</span>
        </div>
        <div>
          <span className="text-gray-500">Size:</span>{" "}
          <span className="font-mono text-gray-300">{func.size} bytes</span>
        </div>
        <div>
          <span className="text-gray-500">Address:</span>{" "}
          <span className="font-mono text-gray-300">0x{func.address.toString(16)}</span>
        </div>
        <div>
          <span className="text-gray-500">Status:</span>{" "}
          <span className={`font-bold ${statusColor[func.status] || "text-gray-300"}`}>
            {func.status}
          </span>
        </div>
        <div>
          <span className="text-gray-500">Match:</span>{" "}
          <span className="font-mono text-white">{func.current_match_pct.toFixed(1)}%</span>
          <span className="text-gray-600 ml-1">(initial: {func.initial_match_pct.toFixed(1)}%)</span>
        </div>
        <div>
          <span className="text-gray-500">Attempts:</span>{" "}
          <span className="font-mono text-gray-300">{func.attempts}</span>
        </div>
        {func.matched_at && (
          <div>
            <span className="text-gray-500">Matched:</span>{" "}
            <span className="text-gray-300">{new Date(func.matched_at).toLocaleString()}</span>
          </div>
        )}
      </div>

      <div className="mt-6">
        <AttemptHistory functionId={functionId} />
      </div>
    </div>
  );
}

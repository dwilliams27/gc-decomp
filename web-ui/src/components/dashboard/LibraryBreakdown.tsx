import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useSelectionStore } from "../../stores/selectionStore";

export function LibraryBreakdown() {
  const selectLibrary = useSelectionStore((s) => s.selectLibrary);
  const { data, isLoading, error } = useQuery({
    queryKey: ["by-library"],
    queryFn: api.getByLibrary,
    refetchInterval: 30_000,
  });

  if (isLoading) return null;
  if (error) return <div className="text-red-500 text-xs">Error loading libraries</div>;
  if (!data) return null;

  return (
    <div className="mt-4">
      <h2 className="mb-2 text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Libraries
      </h2>
      <div className="space-y-1 text-xs">
        {data.libraries.map((lib) => {
          const pct =
            lib.count > 0 ? ((lib.matched / lib.count) * 100).toFixed(0) : "0";
          return (
            <button
              key={lib.library}
              onClick={() => selectLibrary(lib.library)}
              className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-gray-800"
            >
              <span className="flex-1 truncate text-gray-300">{lib.library}</span>
              <span className="text-gray-500">{lib.count}</span>
              <span className="w-10 text-right font-mono text-green-400">
                {pct}%
              </span>
              {lib.cost > 0 && (
                <span className="w-14 text-right text-gray-600">
                  ${lib.cost.toFixed(2)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

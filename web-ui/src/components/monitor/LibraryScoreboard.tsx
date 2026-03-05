import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useWorkerStore } from "../../stores/workerStore";

export function LibraryScoreboard() {
  const { data } = useQuery({
    queryKey: ["by-library"],
    queryFn: api.getByLibrary,
    refetchInterval: 30_000,
  });

  const workers = useWorkerStore((s) => s.workers);

  if (!data) return null;

  // Find libraries with active workers
  const activeLibraries = new Set<string>();
  for (const w of Object.values(workers)) {
    if (w.status === "running") {
      // sourceFile is like "melee/lb/lbcommand.c", library is top-level dir
      const parts = w.sourceFile.split("/");
      if (parts.length >= 2) {
        activeLibraries.add(parts[0]);
      }
    }
  }

  const sorted = [...data.libraries].sort(
    (a, b) => b.avg_match_pct - a.avg_match_pct,
  );

  return (
    <div className="max-h-80 w-56 overflow-y-auto rounded-lg bg-gray-900/80 p-3 backdrop-blur-sm">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Libraries
      </div>
      <div className="space-y-0.5 text-xs">
        {sorted.map((lib) => {
          const pct =
            lib.count > 0
              ? ((lib.matched / lib.count) * 100).toFixed(0)
              : "0";
          const isActive = activeLibraries.has(lib.library);
          return (
            <div
              key={lib.library}
              className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 ${isActive ? "bg-blue-900/30" : ""}`}
            >
              {isActive && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
              )}
              <span
                className={`min-w-0 flex-1 truncate ${isActive ? "text-blue-300" : "text-gray-400"}`}
              >
                {lib.library}
              </span>
              {/* Inline progress bar */}
              <div className="h-1 w-12 overflow-hidden rounded-full bg-gray-800">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${pct}%`,
                    backgroundColor:
                      Number(pct) >= 80
                        ? "#22c55e"
                        : Number(pct) >= 40
                          ? "#eab308"
                          : "#ef4444",
                  }}
                />
              </div>
              <span className="w-8 text-right font-mono text-gray-300">
                {pct}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

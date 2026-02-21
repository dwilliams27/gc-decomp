import { useSelectionStore } from "../../stores/selectionStore";
import { ConnectionStatus } from "../monitor/ConnectionStatus";
import { TreemapView } from "../treemap/TreemapView";
import { StatsOverview } from "../dashboard/StatsOverview";
import { LibraryBreakdown } from "../dashboard/LibraryBreakdown";
import { FunctionDetail } from "../function/FunctionDetail";
import { BatchConfig } from "../batch/BatchConfig";
import { BatchMonitor } from "../batch/BatchMonitor";
import { WorkerView } from "../batch/WorkerView";
import { EventLog } from "../monitor/EventLog";

type View = "treemap" | "function" | "batch" | "events";

const NAV_ITEMS: { view: View; label: string }[] = [
  { view: "treemap", label: "Treemap" },
  { view: "batch", label: "Batch" },
  { view: "events", label: "Events" },
];

export function AppShell() {
  const view = useSelectionStore((s) => s.view);
  const setView = useSelectionStore((s) => s.setView);

  return (
    <div className="flex h-screen flex-col">
      <ConnectionStatus />
      <header className="flex items-center gap-4 border-b border-gray-800 bg-gray-900 px-4 py-2">
        <h1 className="text-lg font-bold text-white">decomp-agent</h1>
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.view}
              onClick={() => setView(item.view)}
              className={`rounded px-3 py-1 text-sm ${
                view === item.view
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:bg-gray-800 hover:text-gray-200"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="flex-1 overflow-hidden">
        {view === "treemap" && <TreemapPage />}
        {view === "function" && <FunctionDetail />}
        {view === "batch" && <BatchPage />}
        {view === "events" && <EventLog />}
      </main>
    </div>
  );
}

function TreemapPage() {
  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-hidden">
        <TreemapView />
      </div>
      <aside className="w-80 overflow-y-auto border-l border-gray-800 bg-gray-900 p-4">
        <StatsOverview />
        <LibraryBreakdown />
      </aside>
    </div>
  );
}

function BatchPage() {
  return (
    <div className="flex h-full gap-4 overflow-y-auto p-4">
      <div className="w-80 shrink-0 space-y-4">
        <BatchConfig />
        <BatchMonitor />
      </div>
      <div className="flex-1">
        <WorkerView />
      </div>
    </div>
  );
}

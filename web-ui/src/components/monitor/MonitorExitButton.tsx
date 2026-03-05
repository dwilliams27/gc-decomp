import { useSelectionStore } from "../../stores/selectionStore";
import { useEventStore } from "../../stores/eventStore";

export function MonitorExitButton() {
  const setView = useSelectionStore((s) => s.setView);
  const connected = useEventStore((s) => s.connected);

  return (
    <div className="flex items-center gap-3 rounded-lg bg-gray-900/80 px-4 py-2 backdrop-blur-sm">
      {/* Connection indicator */}
      <div className="flex items-center gap-1.5 text-xs">
        <span
          className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "animate-pulse bg-red-500"}`}
        />
        <span className={connected ? "text-green-400" : "text-red-400"}>
          {connected ? "Live" : "Disconnected"}
        </span>
      </div>

      <div className="h-4 w-px bg-gray-700" />

      <button
        onClick={() => setView("treemap")}
        className="text-xs text-gray-400 hover:text-white"
      >
        Exit Monitor
      </button>
    </div>
  );
}

import { useEffect, useRef } from "react";
import { useEventStore } from "../../stores/eventStore";

export function EventLog() {
  const events = useEventStore((s) => s.events);
  const clear = useEventStore((s) => s.clear);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-800 bg-gray-900 px-4 py-2">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Event Log ({events.length})
        </h2>
        <button
          onClick={clear}
          className="text-xs text-gray-500 hover:text-gray-300"
        >
          Clear
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 font-mono text-xs">
        {events.length === 0 && (
          <div className="text-gray-600 p-4 text-center">
            No events yet. Start a batch or wait for agent activity.
          </div>
        )}
        {events.map((event, i) => {
          const levelColor =
            event.level === "error"
              ? "text-red-400"
              : event.level === "warning"
                ? "text-yellow-400"
                : "text-gray-400";

          return (
            <div key={i} className="flex gap-2 border-b border-gray-900 py-0.5">
              <span className="text-gray-600 shrink-0">
                {new Date(event.ts * 1000).toLocaleTimeString()}
              </span>
              <span className={`shrink-0 w-16 ${levelColor}`}>
                {event.level || "info"}
              </span>
              <span className="text-gray-300 break-all">
                {event.event || event.type}
                {Object.entries(event)
                  .filter(
                    ([k]) => !["type", "ts", "event", "level", "logger_name", "timestamp"].includes(k),
                  )
                  .map(([k, v]) => (
                    <span key={k} className="ml-2 text-gray-500">
                      {k}=<span className="text-gray-400">{String(v)}</span>
                    </span>
                  ))}
              </span>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

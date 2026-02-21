import { useEventStore } from "../../stores/eventStore";

export function ConnectionStatus() {
  const connected = useEventStore((s) => s.connected);
  if (connected) return null;
  return (
    <div className="bg-red-800 px-4 py-1 text-center text-sm text-white">
      WebSocket disconnected — reconnecting...
    </div>
  );
}

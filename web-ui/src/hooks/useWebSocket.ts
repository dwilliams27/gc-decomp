import { useEffect, useRef, useCallback } from "react";
import { useEventStore } from "../stores/eventStore";
import { useWorkerStore } from "../stores/workerStore";
import type { AgentEvent } from "../api/types";

type Status = "connecting" | "connected" | "disconnected";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const statusRef = useRef<Status>("disconnected");
  const addEvent = useEventStore((s) => s.addEvent);
  const setConnected = useEventStore((s) => s.setConnected);
  const processWorkerEvent = useWorkerStore((s) => s.processEvent);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/events`);
    wsRef.current = ws;
    statusRef.current = "connecting";

    ws.onopen = () => {
      statusRef.current = "connected";
      setConnected(true);
    };

    ws.onmessage = (e) => {
      try {
        const data: AgentEvent = JSON.parse(e.data);
        if (data.type !== "pong") {
          addEvent(data);
          processWorkerEvent(data);
        }
      } catch {
        // Ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      statusRef.current = "disconnected";
      setConnected(false);
      wsRef.current = null;
      // Auto-reconnect after 3s
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [addEvent, setConnected, processWorkerEvent]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return statusRef;
}

import { create } from "zustand";
import type { AgentEvent } from "../api/types";

const MAX_EVENTS = 500;

interface EventState {
  events: AgentEvent[];
  connected: boolean;
  addEvent: (event: AgentEvent) => void;
  setConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useEventStore = create<EventState>((set) => ({
  events: [],
  connected: false,
  addEvent: (event) =>
    set((state) => ({
      events:
        state.events.length >= MAX_EVENTS
          ? [...state.events.slice(-MAX_EVENTS + 1), event]
          : [...state.events, event],
    })),
  setConnected: (connected) => set({ connected }),
  clear: () => set({ events: [] }),
}));

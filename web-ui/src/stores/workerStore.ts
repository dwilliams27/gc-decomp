import { create } from "zustand";
import type { AgentEvent } from "../api/types";

export interface ToolCall {
  tool: string;
  iteration: number;
  ts: number;
}

export interface FunctionWorker {
  functionName: string;
  sourceFile: string;
  iteration: number;
  maxIterations: number;
  matchPct: number;
  toolCalls: ToolCall[];
  matchHistory: { iteration: number; matchPct: number }[];
  status: "running" | "matched" | "failed" | "crashed";
  startedAt: number;
  tokens: number;
  tokenBudget: number;
  lastEvent: string;
  lastEventTs: number;
}

interface WorkerStoreState {
  workers: Record<string, FunctionWorker>;
  /** Ordered list of function names for display */
  activeOrder: string[];
  processEvent: (event: AgentEvent) => void;
  clear: () => void;
}

export const useWorkerStore = create<WorkerStoreState>((set) => ({
  workers: {},
  activeOrder: [],

  processEvent: (event: AgentEvent) => {
    const fn = event.function as string | undefined;
    if (!fn) return;

    const eventName = (event.event as string) || "";

    set((state) => {
      const workers = { ...state.workers };
      let activeOrder = [...state.activeOrder];

      // Initialize worker on function_start
      if (eventName.includes("function_start") || eventName.includes("iteration_start")) {
        if (!workers[fn]) {
          workers[fn] = {
            functionName: fn,
            sourceFile: (event.source_file as string) || "",
            iteration: 0,
            maxIterations: 30,
            matchPct: 0,
            toolCalls: [],
            matchHistory: [],
            status: "running",
            startedAt: event.ts,
            tokens: 0,
            tokenBudget: 0,
            lastEvent: eventName,
            lastEventTs: event.ts,
          };
          if (!activeOrder.includes(fn)) {
            activeOrder = [...activeOrder, fn];
          }
        }
      }

      const worker = workers[fn];
      if (!worker) return state;

      // Update based on event type
      if (eventName.includes("iteration_start")) {
        worker.iteration = (event.iteration as number) || worker.iteration;
        worker.maxIterations = (event.max as number) || worker.maxIterations;
        if (typeof event.match === "number") worker.matchPct = event.match;
        if (typeof event.tokens === "number") worker.tokens = event.tokens;
        if (typeof event.budget === "number" && event.budget > 0) worker.tokenBudget = event.budget;
      }

      if (eventName.includes("tool_call")) {
        worker.toolCalls = [
          ...worker.toolCalls,
          {
            tool: (event.tool as string) || "unknown",
            iteration: worker.iteration,
            ts: event.ts,
          },
        ];
      }

      if (eventName.includes("match_improved")) {
        const newMatch = (event.new as number) ?? worker.matchPct;
        worker.matchPct = newMatch;
        worker.matchHistory = [
          ...worker.matchHistory,
          { iteration: worker.iteration, matchPct: newMatch },
        ];
      }

      if (eventName.includes("function_matched")) {
        worker.status = "matched";
        worker.matchPct = 100;
      }

      if (eventName.includes("agent_finished")) {
        const matched = event.matched as boolean;
        worker.status = matched ? "matched" : "failed";
        if (typeof event.best_match === "number") worker.matchPct = event.best_match;
        if (typeof event.tokens === "number") worker.tokens = event.tokens;
      }

      if (eventName.includes("agent_crash")) {
        worker.status = "crashed";
      }

      worker.lastEvent = eventName;
      worker.lastEventTs = event.ts;

      return { workers, activeOrder };
    });
  },

  clear: () => set({ workers: {}, activeOrder: [] }),
}));

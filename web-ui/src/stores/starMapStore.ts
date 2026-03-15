import { create } from "zustand";
import type {
  StarPosition,
  ConstellationEdge,
  LibraryCentroid,
  Supernova,
  CampaignEvent,
} from "../api/types";

interface StarMapState {
  stars: StarPosition[];
  edges: ConstellationEdge[];
  centroids: LibraryCentroid[];
  loaded: boolean;

  supernovae: Supernova[];
  /** Star IDs currently being worked on by a worker. */
  pulsingStarIds: Set<number>;

  mode: "live" | "history";
  playbackSpeed: number;
  paused: boolean;

  setStars: (stars: StarPosition[], edges: ConstellationEdge[], centroids: LibraryCentroid[]) => void;
  addSupernova: (supernova: Supernova) => void;
  removeSupernova: (starId: number) => void;
  setPulsingStarId: (starId: number, pulsing: boolean) => void;
  setMode: (mode: "live" | "history") => void;
  setPlaybackSpeed: (speed: number) => void;
  setPaused: (paused: boolean) => void;
  processEvent: (event: CampaignEvent) => void;
}

// Function names that arrived via processEvent before stars were loaded
const pendingPulsingNames = new Set<string>();
const pendingStoppedNames = new Set<string>();

export const useStarMapStore = create<StarMapState>((set, get) => ({
  stars: [],
  edges: [],
  centroids: [],
  loaded: false,

  supernovae: [],
  pulsingStarIds: new Set(),

  mode: "live",
  playbackSpeed: 1,
  paused: false,

  setStars: (stars, edges, centroids) => {
    // Resolve any events that arrived before stars loaded
    const pulsing = new Set<number>();
    if (pendingPulsingNames.size > 0) {
      for (const s of stars) {
        if (pendingPulsingNames.has(s.name) && !pendingStoppedNames.has(s.name)) {
          pulsing.add(s.id);
        }
      }
      pendingPulsingNames.clear();
      pendingStoppedNames.clear();
    }
    set({ stars, edges, centroids, loaded: true, pulsingStarIds: pulsing });
  },

  addSupernova: (supernova) =>
    set((s) => ({ supernovae: [...s.supernovae, supernova] })),

  removeSupernova: (starId) =>
    set((s) => ({ supernovae: s.supernovae.filter((sn) => sn.starId !== starId) })),

  setPulsingStarId: (starId, pulsing) =>
    set((s) => {
      const next = new Set(s.pulsingStarIds);
      if (pulsing) next.add(starId);
      else next.delete(starId);
      return { pulsingStarIds: next };
    }),

  setMode: (mode) => set({ mode }),
  setPlaybackSpeed: (speed) => set({ playbackSpeed: speed }),
  setPaused: (paused) => set({ paused }),

  processEvent: (event) => {
    const state = get();
    const fnName = event.function_name;
    const star = fnName
      ? state.stars.find((s) => s.name === fnName)
      : null;

    // If stars haven't loaded yet, stash names to resolve later
    if (!state.loaded && fnName) {
      if (event.event_type === "worker_started") {
        pendingPulsingNames.add(fnName);
        pendingStoppedNames.delete(fnName);
      }
      if (event.event_type === "worker_completed" || event.event_type === "worker_failed" || event.event_type === "match_achieved") {
        pendingStoppedNames.add(fnName);
      }
      return;
    }

    if (event.event_type === "worker_started" && star) {
      set((s) => ({
        pulsingStarIds: new Set([...s.pulsingStarIds, star.id]),
      }));
    }

    if (
      (event.event_type === "worker_completed" ||
        event.event_type === "worker_failed") &&
      star
    ) {
      set((s) => {
        const next = new Set(s.pulsingStarIds);
        next.delete(star.id);
        return { pulsingStarIds: next };
      });
    }

    if (event.event_type === "match_achieved" && star) {
      const matchPct = (event.data?.best_match_pct as number) ?? 100;
      set((s) => ({
        stars: s.stars.map((st) =>
          st.id === star.id ? { ...st, matchPct } : st
        ),
        pulsingStarIds: (() => {
          const next = new Set(s.pulsingStarIds);
          next.delete(star.id);
          return next;
        })(),
        supernovae: [
          ...s.supernovae,
          { starId: star.id, startTime: Date.now(), duration: 3000 },
        ],
      }));
    }
  },
}));

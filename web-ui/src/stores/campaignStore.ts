import { create } from "zustand";
import type {
  CampaignSummary,
  CampaignDetail,
  CampaignEvent,
  CampaignMessage,
  CampaignTimelineResponse,
} from "../api/types";

interface CampaignState {
  campaigns: CampaignSummary[];
  selectedCampaignId: number | null;
  selectedCampaign: CampaignDetail | null;
  messages: CampaignMessage[];
  events: CampaignEvent[];
  timeline: CampaignTimelineResponse | null;
  loading: boolean;

  setCampaigns: (campaigns: CampaignSummary[]) => void;
  setSelectedCampaignId: (id: number | null) => void;
  setSelectedCampaign: (campaign: CampaignDetail | null) => void;
  addMessage: (message: CampaignMessage) => void;
  addMessages: (messages: CampaignMessage[]) => void;
  addEvent: (event: CampaignEvent) => void;
  addEvents: (events: CampaignEvent[]) => void;
  clearMessages: () => void;
  setTimeline: (timeline: CampaignTimelineResponse | null) => void;
  setLoading: (loading: boolean) => void;
}

export const useCampaignStore = create<CampaignState>((set) => ({
  campaigns: [],
  selectedCampaignId: null,
  selectedCampaign: null,
  messages: [],
  events: [],
  timeline: null,
  loading: false,

  setCampaigns: (campaigns) => set({ campaigns }),
  setSelectedCampaignId: (id) => set({ selectedCampaignId: id }),
  setSelectedCampaign: (campaign) => set({ selectedCampaign: campaign }),
  addMessage: (message) =>
    set((state) => {
      if (state.messages.some((m) => m.id === message.id)) return state;
      return { messages: [...state.messages, message] };
    }),
  addMessages: (messages) =>
    set((state) => {
      const seen = new Set(state.messages.map((m) => m.id));
      const fresh = messages.filter((m) => !seen.has(m.id));
      return fresh.length ? { messages: [...state.messages, ...fresh] } : state;
    }),
  addEvent: (event) =>
    set((state) => {
      if (state.events.some((e) => e.id === event.id)) return state;
      return { events: [...state.events, event] };
    }),
  addEvents: (events) =>
    set((state) => {
      const seen = new Set(state.events.map((e) => e.id));
      const fresh = events.filter((e) => !seen.has(e.id));
      return fresh.length ? { events: [...state.events, ...fresh] } : state;
    }),
  clearMessages: () => set({ messages: [], events: [] }),
  setTimeline: (timeline) => set({ timeline }),
  setLoading: (loading) => set({ loading }),
}));

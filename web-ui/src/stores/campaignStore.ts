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
    set((state) => ({ messages: [...state.messages, message] })),
  addMessages: (messages) =>
    set((state) => ({ messages: [...state.messages, ...messages] })),
  addEvent: (event) =>
    set((state) => ({ events: [...state.events, event] })),
  addEvents: (events) =>
    set((state) => ({ events: [...state.events, ...events] })),
  clearMessages: () => set({ messages: [], events: [] }),
  setTimeline: (timeline) => set({ timeline }),
  setLoading: (loading) => set({ loading }),
}));

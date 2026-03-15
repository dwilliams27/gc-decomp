/** API client for campaign endpoints. */

import type {
  CampaignDetail,
  CampaignEventsResponse,
  CampaignListResponse,
  CampaignMessagesResponse,
  CampaignTimelineResponse,
  StarmapResponse,
} from "./types";

const BASE = "";

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

export const campaignApi = {
  listCampaigns: (status?: string, page = 1) => {
    const params = new URLSearchParams({ page: String(page) });
    if (status) params.set("status", status);
    return request<CampaignListResponse>(`/api/campaigns?${params}`);
  },

  getCampaign: (id: number) =>
    request<CampaignDetail>(`/api/campaigns/${id}`),

  getCampaignEvents: (id: number, afterId = 0, limit = 100) =>
    request<CampaignEventsResponse>(
      `/api/campaigns/${id}/events?after_id=${afterId}&limit=${limit}`
    ),

  getCampaignMessages: (id: number, afterId = 0, limit = 100) =>
    request<CampaignMessagesResponse>(
      `/api/campaigns/${id}/messages?after_id=${afterId}&limit=${limit}`
    ),

  getCampaignTimeline: (id: number) =>
    request<CampaignTimelineResponse>(`/api/campaigns/${id}/timeline`),

  getStarmap: () => request<StarmapResponse>("/api/functions/starmap"),
};

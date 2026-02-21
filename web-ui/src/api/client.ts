/** Fetch wrapper for API calls. */

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

export const api = {
  // Functions
  getTreemap: () => request<import("./types").TreemapNode>("/api/functions/treemap"),

  getFunctions: (params?: Record<string, string | number>) => {
    const qs = params ? "?" + new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)])
    ).toString() : "";
    return request<import("./types").FunctionListResponse>(`/api/functions${qs}`);
  },

  getFunction: (id: number) =>
    request<import("./types").FunctionEntry>(`/api/functions/${id}`),

  getFunctionAttempts: (id: number) =>
    request<import("./types").AttemptsResponse>(`/api/functions/${id}/attempts`),

  // Stats
  getOverview: () => request<import("./types").OverviewStats>("/api/stats/overview"),

  getByLibrary: () =>
    request<{ libraries: import("./types").LibraryStats[] }>("/api/stats/by-library"),

  // Batch
  startBatch: (params: import("./types").BatchParams) =>
    request<{ status: string }>("/api/batch/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),

  getBatchStatus: () =>
    request<import("./types").BatchStatus>("/api/batch/current"),

  cancelBatch: () =>
    request<{ status: string }>("/api/batch/cancel", { method: "POST" }),

  // Config
  getConfig: () => request<import("./types").ConfigResponse>("/api/config"),
};

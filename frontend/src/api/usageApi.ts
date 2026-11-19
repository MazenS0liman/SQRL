import { authHeaders, notifyAuthExpired } from "@/lib/auth";
import type {
  UsageEventListResponse,
  UsageOverviewResponse,
  UsagePricingResponse,
} from "@/types";

const API_BASE = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, "");

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}/usage${path}`, {
    headers: authHeaders(),
  });
  if (response.status === 401) notifyAuthExpired();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `Usage request failed (${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}

export const usageApi = {
  overview: (days = 30) => request<UsageOverviewResponse>(`/overview?days=${days}`),
  events: (params: {
    limit?: number;
    offset?: number;
    provider?: string;
    source?: string;
    days?: number;
  } = {}) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        search.set(key, String(value));
      }
    });
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request<UsageEventListResponse>(`/events${suffix}`);
  },
  pricing: () => request<UsagePricingResponse>("/pricing"),
};

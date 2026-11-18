import { authHeaders, notifyAuthExpired } from "@/lib/auth";
import type {
  MarkAllReadResponse,
  NotificationItem,
  NotificationListResponse,
  UnreadCountResponse,
} from "@/types";

const API_BASE = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, "");

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}/notifications${path}`, {
    ...init,
    headers: authHeaders(init.headers),
  });
  if (response.status === 401) notifyAuthExpired();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Notification request failed (${response.status}).`,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const notificationsApi = {
  list: (params: { unreadOnly?: boolean; limit?: number; offset?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.unreadOnly) search.set("unread_only", "true");
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request<NotificationListResponse>(suffix);
  },
  unreadCount: () => request<UnreadCountResponse>("/unread-count"),
  markRead: (notificationId: string) =>
    request<NotificationItem>(`/${notificationId}/read`, { method: "POST" }),
  markAllRead: () => request<MarkAllReadResponse>("/read-all", { method: "POST" }),
};

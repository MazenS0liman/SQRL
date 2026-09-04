import type { NotebookStatus } from '@/types';
import { authHeaders } from '@/lib/auth';

// ——————————————————————————————————————————————————————————————
// API client

export const API_BASE = import.meta.env.VITE_BACKEND_API_BASE_URL || "/api";

export class ApiError extends Error {}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(options.headers || {}),
      ...authHeaders(),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* response wasn't JSON */
    }
    throw new ApiError(detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ——————————————————————————————————————————————————————————————
// Display helpers

export function statusMeta(status: NotebookStatus): { label: string; tabClass: string; dotClass: string } {
  switch (status) {
    case "ready":
      return { label: "Ready", tabClass: "bg-primary-gradient", dotClass: "bg-emerald-500" };
    case "error":
      return { label: "Error", tabClass: "bg-destructive", dotClass: "bg-destructive" };
    default:
      return { label: "No data", tabClass: "bg-muted", dotClass: "bg-muted-foreground" };
  }
}

export function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
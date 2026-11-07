import type { NotebookStatus } from '@/types';

// ——————————————————————————————————————————————————————————————
// API client (single shared implementation in @/lib/utils)

export { API_BASE, ApiError, apiFetch } from '@/lib/utils';

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
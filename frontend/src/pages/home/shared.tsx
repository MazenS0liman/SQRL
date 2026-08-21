import type { Notebook, ConnectorSummary } from '@/types';
import { authHeaders } from '@/lib/auth';

// ——————————————————————————————————————————————————————————————
// API client

export const API_BASE = `${import.meta.env.VITE_BACKEND_API_BASE_URL}`;

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
// Connector API

const CONNECTORS_API_BASE = `${import.meta.env.VITE_BACKEND_API_BASE_URL}/connector`;

async function connectorsFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${CONNECTORS_API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const connectorsApi = {
  list: () => connectorsFetch<ConnectorSummary[]>(""),
};

// ——————————————————————————————————————————————————————————————
// Display helpers

export function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function getNotebookStatus(notebook: Notebook): 'ready' | 'running' | 'draft' {
  // Map actual notebook status to display status
  switch (notebook.status) {
    case 'ready':
      return 'ready';
    case 'error':
      return 'running'; // Show as running for now, can be updated based on actual status
    default:
      return 'draft';
  }
}

export function getConnectorStatus(status: string): 'connected' | 'syncing' | 'error' {
  switch (status) {
    case 'connected':
      return 'connected';
    case 'syncing':
      return 'syncing';
    case 'error':
      return 'error';
    default:
      return 'connected';
  }
}

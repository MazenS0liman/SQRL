import type { Notebook, ConnectorSummary } from '@/types';
import { apiFetch } from '@/lib/utils';

// ——————————————————————————————————————————————————————————————
// API client (single shared implementation in @/lib/utils)

export { API_BASE, ApiError, apiFetch } from '@/lib/utils';

// ——————————————————————————————————————————————————————————————
// Connector API

export const connectorsApi = {
  list: () => apiFetch<ConnectorSummary[]>("/connector"),
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

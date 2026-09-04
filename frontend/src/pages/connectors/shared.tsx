// ————————————————————————————————————————————————————————————
// Connector type registry — the source of truth for which fields each
// data source needs. Adding a new source type is just adding an entry
// here; the form and cards are fully generic over this list.

export type ConnectorTypeId =
  | "postgres";

export interface ConnectorField {
  key: string;
  label: string;
  type: "text" | "password" | "number" | "textarea";
  placeholder?: string;
  required?: boolean;
}

export interface ConnectorTypeDef {
  id: ConnectorTypeId;
  label: string;
  description: string;
  badgeClass: string;
  logo: string;
  fields: ConnectorField[];
}

export const CONNECTOR_TYPES: ConnectorTypeDef[] = [
  {
    id: "postgres",
    label: "PostgreSQL",
    description: "Connect to a Postgres database.",
    badgeClass: "bg-secondary text-foreground border border-border",
    logo: "/imgs/postgres.png",
    fields: [
      { key: "host", label: "Host", type: "text", placeholder: "db.internal.example.com", required: true },
      { key: "port", label: "Port", type: "number", placeholder: "5432", required: true },
      { key: "database", label: "Database", type: "text", placeholder: "analytics", required: true },
      { key: "username", label: "Username", type: "text", required: true },
      { key: "password", label: "Password", type: "password", required: true },
    ],
  }
];

export function connectorTypeDef(id: ConnectorTypeId): ConnectorTypeDef {
  const def = CONNECTOR_TYPES.find((t) => t.id === id);
  if (!def) throw new Error(`Unknown connector type: ${id}`);
  return def;
}

// ————————————————————————————————————————————————————————————
// Connection record + API helpers

export type ConnectionStatus = "untested" | "connected" | "error";

export interface DataConnection {
  connector_id: string;
  name: string;
  type: ConnectorTypeId;
  // Secret fields (password, access_token, secret_access_key,
  // service_account_json) should come back redacted/omitted from the
  // backend — this is a config *summary*, not a credential store.
  config: Record<string, string>;
  status: ConnectionStatus;
  error: string | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
}

import { authHeaders } from "@/lib/auth";

const CONNECTORS_API_BASE = `${import.meta.env.VITE_BACKEND_API_BASE_URL || "/api"}/connector`;

/**
 * Thrown by `connectorsFetch` for any non-OK response, carrying the HTTP
 * status alongside the backend's `detail` message so callers can branch on
 * *why* a request failed (e.g. 409 "still in use" vs. any other failure)
 * instead of only having a flat error string to work with.
 */
export class ConnectorApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ConnectorApiError";
    this.status = status;
  }
}

async function connectorsFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${CONNECTORS_API_BASE}${path}`, {
    headers: authHeaders({ "Content-Type": "application/json" }),
    ...init,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ConnectorApiError(body.detail || `Request failed (${res.status})`, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const connectorsApi = {
  list: () => connectorsFetch<DataConnection[]>(""),
  create: (payload: { name: string; type: ConnectorTypeId; config: Record<string, string> }) =>
    connectorsFetch<DataConnection>("", { method: "POST", body: JSON.stringify(payload) }),
  update: (id: string, payload: { name: string; config: Record<string, string> }) =>
    connectorsFetch<DataConnection>(`/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  test: (id: string) => connectorsFetch<DataConnection>(`/${id}/test`, { method: "POST" }),
  remove: (id: string) => connectorsFetch<void>(`/${id}`, { method: "DELETE" }),
  listTables: (connectorId: string) =>
    connectorsFetch<string[]>(`/${connectorId}/tables`),
  previewAllTables: (connectorId: string, limit = 5) =>
    connectorsFetch<{ tables: { table: string; columns: string[]; preview: Record<string, unknown>[]; error?: string }[] }>(
      `/${connectorId}/tables/preview?limit=${limit}`
    ),
};

export function statusBadge(status: ConnectionStatus): { label: string; className: string } {
  switch (status) {
    case "connected":
      return { label: "Connected", className: "bg-primary-gradient text-primary-foreground" };
    case "error":
      return { label: "Error", className: "bg-destructive text-destructive-foreground" };
    default:
      return { label: "Untested", className: "bg-muted text-muted-foreground" };
  }
}
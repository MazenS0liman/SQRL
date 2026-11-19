// ————————————————————————————————————————————————————————————
// Connector type registry — the source of truth for which fields each
// data source needs. Adding a new source type is just adding an entry
// here; the form and cards are fully generic over this list.

import { ApiError, apiFetch } from "@/lib/utils";

export type ConnectorTypeId =
  | "postgres"
  | "databricks"
  | "lakebase";

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
  },
  {
    id: "databricks",
    label: "Databricks",
    description: "Connect to a Databricks SQL warehouse.",
    badgeClass: "bg-secondary text-foreground border border-border",
    logo: "/imgs/databricks.png",
    fields: [
      { key: "server_hostname", label: "Server hostname", type: "text", required: true },
      { key: "http_path", label: "HTTP path", type: "text", required: true },
      { key: "token", label: "Access token", type: "password", required: true },
      { key: "catalog", label: "Catalog", type: "text", placeholder: "main (optional)" },
      { key: "schema", label: "Schema", type: "text", placeholder: "default (optional)" },
    ],
  },
  {
    id: "lakebase",
    label: "Lakebase",
    description: "Connect to a Databricks Lakebase database.",
    badgeClass: "bg-secondary text-foreground border border-border",
    logo: "/imgs/lakebase.png",
    fields: [
      { key: "host", label: "Host", type: "text", required: true },
      { key: "database", label: "Database", type: "text", required: true },
      { key: "username", label: "Username", type: "text", required: true },
      { key: "password", label: "Password", type: "password", required: true },
      { key: "port", label: "Port", type: "number", placeholder: "5432" },
    ],
  },
];

/** Types the backend can run bounded read-only SQL against (POST /connector/{id}/query). */
export const QUERYABLE_TYPES: ConnectorTypeId[] = ["postgres", "databricks", "lakebase"];

export function isQueryable(type: ConnectorTypeId): boolean {
  return QUERYABLE_TYPES.includes(type);
}

/** Starter SQL for the query modal, per connector type. */
export const DEFAULT_QUERY: Record<ConnectorTypeId, string> = {
  postgres: "SELECT * FROM public.table_name",
  lakebase: "SELECT * FROM public.table_name",
  databricks: "SELECT * FROM catalog.schema.table",
};

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
  try {
    return await apiFetch<T>(`/connector${path}`, init);
  } catch (err) {
    if (err instanceof ApiError) {
      throw new ConnectorApiError(err.message, err.status);
    }
    throw err;
  }
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
  describeTable: (connectorId: string, table: string) =>
    connectorsFetch<{ column_name: string; data_type: string }[]>(
      `/${connectorId}/tables/${encodeURIComponent(table)}/columns`
    ),
  previewAllTables: (connectorId: string, limit = 5) =>
    connectorsFetch<{ tables: { table: string; columns: string[]; preview: Record<string, unknown>[]; error?: string }[] }>(
      `/${connectorId}/tables/preview?limit=${limit}`
    ),
  query: (connectorId: string, query: string, limit = 100) =>
    connectorsFetch<{ columns: string[]; rows: Record<string, unknown>[]; row_count: number }>(
      `/${connectorId}/query`,
      { method: "POST", body: JSON.stringify({ query, limit }) }
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
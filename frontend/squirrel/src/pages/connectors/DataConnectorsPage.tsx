import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Database,
  Cloud,
  Server,
  Boxes,
  Webhook,
  HardDrive,
  FileSpreadsheet,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  CircleDashed,
} from "lucide-react";
import type { ConnectorField, ConnectorTypeDef, ConnectorTypeId, DataConnection } from "./shared.tsx";
import { CONNECTOR_TYPES, connectorTypeDef, connectorsApi, statusBadge, ConnectorApiError } from "./shared.tsx";

// Dotted background, same component used elsewhere in the app.
import DotField from "@/components/background/DotField";

/**
 * Per-connector-type visual treatment.
 * ======================================
 * We don't ship actual third-party brand logos (trademark/licensing, and
 * they'd need network access to fetch) — instead each connector type gets
 * a distinct icon + color pairing derived from its id/label, so the tiles
 * and rows still read as visually distinct "sources" at a glance rather
 * than a flat list of identical gray boxes.
 */
type ConnectorVisual = {
  Icon: typeof Database;
  tile: string; // classes for the icon badge background/border/text
  glow: string; // classes for the hover glow ring on the "add source" tile
};

function connectorVisual(id: string, label: string): ConnectorVisual {
  const key = `${id} ${label}`.toLowerCase();

  if (/(postgres|mysql|maria|sql\b|oracle|sqlite)/.test(key)) {
    return {
      Icon: Database,
      tile: "border-sky-500/30 bg-sky-500/10 text-sky-500",
      glow: "group-hover:shadow-sky-500/20",
    };
  }
  if (/(mongo|nosql|dynamo|firestore)/.test(key)) {
    return {
      Icon: Boxes,
      tile: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
      glow: "group-hover:shadow-emerald-500/20",
    };
  }
  if (/(s3|storage|blob|gcs|azure|bucket)/.test(key)) {
    return {
      Icon: Cloud,
      tile: "border-violet-500/30 bg-violet-500/10 text-violet-500",
      glow: "group-hover:shadow-violet-500/20",
    };
  }
  if (/(bigquery|snowflake|redshift|warehouse|databricks)/.test(key)) {
    return {
      Icon: Server,
      tile: "border-indigo-500/30 bg-indigo-500/10 text-indigo-500",
      glow: "group-hover:shadow-indigo-500/20",
    };
  }
  if (/(csv|sheet|excel|spreadsheet)/.test(key)) {
    return {
      Icon: FileSpreadsheet,
      tile: "border-amber-500/30 bg-amber-500/10 text-amber-500",
      glow: "group-hover:shadow-amber-500/20",
    };
  }
  if (/(api|rest|webhook|http|graphql)/.test(key)) {
    return {
      Icon: Webhook,
      tile: "border-rose-500/30 bg-rose-500/10 text-rose-500",
      glow: "group-hover:shadow-rose-500/20",
    };
  }
  if (/(ftp|sftp|file)/.test(key)) {
    return {
      Icon: HardDrive,
      tile: "border-slate-500/30 bg-slate-500/10 text-slate-500",
      glow: "group-hover:shadow-slate-500/20",
    };
  }
  return {
    Icon: Database,
    tile: "border-primary/30 bg-primary/10 text-primary",
    glow: "group-hover:shadow-primary/20",
  };
}

/**
 * Data Connectors Page
 * ====================
 *
 * Two zones:
 *   - "Add a source": one tile per supported connector type. Clicking a
 *     tile opens a form built from that type's field list.
 *   - "Connected sources": every configured connection, with its live
 *     status (untested / connected / error), a Test action, and Delete.
 *
 * The connector type is the fixed vocabulary here (same role the four
 * pipeline stages play on the Library page) — so each source-type tile
 * carries an icon/color treatment styled after its category rather than
 * a generic flat box (see connectorVisual above).
 */
export default function DataConnectorsPage(): JSX.Element {
  const [connections, setConnections] = useState<DataConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [formTypeId, setFormTypeId] = useState<ConnectorTypeId | null>(null);
  const [editingConnection, setEditingConnection] = useState<DataConnection | null>(null);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);

  // Set whenever a delete fails (for any reason, not just the "still in
  // use" 409) — surfaced as a blocking popup rather than an inline row
  // message, since it's easy to miss a small red line under a row you've
  // already moved on from.
  const [deleteBlocked, setDeleteBlocked] = useState<{
    name: string;
    message: string;
    conflict: boolean;
  } | null>(null);

  const refresh = async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await connectorsApi.list();
      setConnections(data);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load connections.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return connections;
    return connections.filter(
      (c) => c.name.toLowerCase().includes(q) || c.type.toLowerCase().includes(q)
    );
  }, [connections, query]);

  const handleCreated = (conn: DataConnection) => {
    setConnections((prev) => [conn, ...prev]);
    setFormTypeId(null);
  };

  const handleUpdated = (conn: DataConnection) => {
    setConnections((prev) => prev.map((c) => (c.connector_id === conn.connector_id ? conn : c)));
    setEditingConnection(null);
  };

  const handleTest = async (conn: DataConnection) => {
    setRowError(null);
    setTestingId(conn.connector_id);
    try {
      const updated = await connectorsApi.test(conn.connector_id);
      setConnections((prev) => prev.map((c) => (c.connector_id === updated.connector_id ? updated : c)));
    } catch (err) {
      setRowError({ id: conn.connector_id, message: err instanceof Error ? err.message : "Test failed." });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (conn: DataConnection) => {
    setDeletingId(conn.connector_id);
    try {
      await connectorsApi.remove(conn.connector_id);
      setConnections((prev) => prev.filter((c) => c.connector_id !== conn.connector_id));
    } catch (err) {
      const isConflict = err instanceof ConnectorApiError && err.status === 409;
      const message = err instanceof Error ? err.message : "Delete failed.";
      setDeleteBlocked({ name: conn.name, message, conflict: isConflict });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
      <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <DotField
            dotRadius={1.5}
            dotSpacing={14}
            bulgeStrength={50}
            glowRadius={0}
            sparkle={false}
            waveAmplitude={0}
            cursorRadius={100}
            cursorForce={0}
            bulgeOnly={true}
            gradientFrom="#592603"
            gradientTo="#613508"
            glowColor="#af4d08"
          />
        </div>

        <div className="relative min-h-full w-full mx-auto max-w-6xl px-6 py-10">
          <header className="relative mb-8 border-b border-border pb-6">
            <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
              Data Connectors
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Connect the databases and stores you'll pull datasets from.
            </p>
          </header>

          {/* Add a source */}
          <section className="relative mb-10">
            <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-foreground">
              Add a source
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {CONNECTOR_TYPES.map((def) => {
                const visual = connectorVisual(def.id, def.label);
                return (
                  <button
                    key={def.id}
                    onClick={() => setFormTypeId(def.id)}
                    className={`group relative flex flex-col items-start gap-2.5 overflow-hidden rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-all duration-200 ease-out hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-lg ${visual.glow}`}
                  >
                    {/* Faint corner glow, tinted per connector category */}
                    <div
                      className={`pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full opacity-0 blur-xl transition-opacity duration-300 group-hover:opacity-100 ${visual.tile}`}
                      aria-hidden
                    />

                    <span
                      className={`flex h-9 w-9 shrink-0 items-center justify-center`}
                    >
                      <img className="h-5.5 w-5.5" src={def.logo} />
                    </span>

                    <div className="relative min-w-0">
                      <p className="truncate text-sm font-semibold text-foreground">{def.label}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{def.description}</p>
                    </div>

                    <span className="relative mt-auto inline-flex items-center gap-1 pt-1 text-[11px] font-medium text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-hover:text-primary">
                      Connect
                      <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Connected sources */}
          <section className="relative">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-[13px] font-semibold uppercase tracking-wide text-foreground">
                Connected sources
              </h2>
              <SearchInput value={query} onChange={setQuery} />
            </div>

            {loading && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-border border-t-primary" />
                Loading connections…
              </div>
            )}
            {listError && <p className="text-sm text-destructive">{listError}</p>}

            {!loading && !listError && connections.length === 0 && (
              <div className="flex h-32 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-border text-center">
                <Database className="mb-1 h-4 w-4 text-muted-foreground/60" />
                <p className="text-sm text-muted-foreground">No sources connected yet — add one above.</p>
              </div>
            )}

            {!loading && !listError && connections.length > 0 && filtered.length === 0 && (
              <div className="flex h-24 items-center justify-center rounded-xl border border-dashed border-border text-center">
                <p className="text-sm text-muted-foreground">No sources match “{query}”.</p>
              </div>
            )}

            <ul className="space-y-2">
              {filtered.map((conn) => (
                <ConnectionRow
                  key={conn.connector_id}
                  connection={conn}
                  testing={testingId === conn.connector_id}
                  deleting={deletingId === conn.connector_id}
                  error={rowError?.id === conn.connector_id ? rowError.message : null}
                  onTest={() => handleTest(conn)}
                  onDelete={() => handleDelete(conn)}
                  onEdit={() => setEditingConnection(conn)}
                />
              ))}
            </ul>
          </section>
        </div>
      </div>

      {formTypeId && (
        <ConnectorFormModal
          typeDef={connectorTypeDef(formTypeId)}
          onClose={() => setFormTypeId(null)}
          onSaved={handleCreated}
        />
      )}
      {editingConnection && (
        <ConnectorFormModal
          typeDef={connectorTypeDef(editingConnection.type)}
          existing={editingConnection}
          onClose={() => setEditingConnection(null)}
          onSaved={handleUpdated}
        />
      )}
      {deleteBlocked && (
        <DeleteBlockedModal
          connectionName={deleteBlocked.name}
          message={deleteBlocked.message}
          conflict={deleteBlocked.conflict}
          onClose={() => setDeleteBlocked(null)}
        />
      )}
    </div>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (v: string) => void }): JSX.Element {
  return (
    <div className="relative">
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
      >
        <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
        <path d="M21 21l-4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search sources…"
        className="w-56 rounded-md border border-input bg-secondary py-1.5 pl-8 pr-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
      />
    </div>
  );
}

/** Small status icon to pair with the existing text badge, so connected
 * vs. error vs. untested reads at a glance even before you read the label. */
function StatusIcon({ status }: { status: DataConnection["status"] }): JSX.Element {
  if (status === "connected") return <CheckCircle2 className="h-3 w-3" />;
  if (status === "error") return <AlertTriangle className="h-3 w-3" />;
  return <CircleDashed className="h-3 w-3" />;
}

function ConnectionRow({
  connection,
  testing,
  deleting,
  error,
  onTest,
  onDelete,
  onEdit,
}: {
  connection: DataConnection;
  testing: boolean;
  deleting: boolean;
  error: string | null;
  onTest: () => void;
  onDelete: () => void;
  onEdit: () => void;
}): JSX.Element {
  const badge = statusBadge(connection.status);
  const typeDef = connectorTypeDef(connection.type);
  const visual = connectorVisual(typeDef.id, typeDef.label);
  const { Icon } = visual;

  return (
    <li className="group rounded-xl border border-border bg-card px-4 py-3 shadow-sm transition-colors hover:border-primary/20">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${visual.tile}`}>
            <Icon className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-medium text-foreground">{connection.name}</span>
              <span
                className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide ${badge.className}`}
              >
                <StatusIcon status={connection.status} />
                {badge.label}
              </span>
            </div>
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{typeDef.label}</span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={onTest}
            disabled={testing}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40"
          >
            {testing ? "Testing…" : "Test"}
          </button>
          <button
            onClick={onEdit}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            disabled={deleting}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
          >
            {deleting ? "Removing…" : "Remove"}
          </button>
        </div>
      </div>

      {connection.status === "error" && connection.error && (
        <p className="mt-2 text-xs text-destructive">{connection.error}</p>
      )}
      {connection.last_tested_at && connection.status !== "error" && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Last tested {new Date(connection.last_tested_at).toLocaleString()}
        </p>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </li>
  );
}

function ConnectorFormModal({
  typeDef,
  existing,
  onClose,
  onSaved,
}: {
  typeDef: ConnectorTypeDef;
  existing?: DataConnection;
  onClose: () => void;
  onSaved: (conn: DataConnection) => void;
}): JSX.Element {
  const isEditing = Boolean(existing);
  const visual = connectorVisual(typeDef.id, typeDef.label);
  const { Icon } = visual;
  const [name, setName] = useState(existing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const f of typeDef.fields) {
      // Secret fields are redacted by the backend, so never prefill them —
      // leaving one blank on edit means "keep the existing value."
      initial[f.key] = f.type === "password" ? "" : existing?.config?.[f.key] ?? "";
    }
    return initial;
  });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstFieldRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const missingRequired = typeDef.fields.some(
    (f) => f.required && !isEditing && !values[f.key]?.trim()
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || missingRequired) return;
    setSaving(true);
    setFormError(null);
    try {
      // Drop blank secret fields on edit so we don't overwrite a stored
      // credential with an empty string.
      const config: Record<string, string> = {};
      for (const [k, v] of Object.entries(values)) {
        if (v.trim() || !isEditing) config[k] = v;
      }
      const conn = isEditing
        ? await connectorsApi.update(existing!.connector_id, { name: name.trim(), config })
        : await connectorsApi.create({ name: name.trim(), type: typeDef.id, config });
      onSaved(conn);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save connection.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-card shadow-lg">
        <form onSubmit={handleSubmit} className="max-h-[85vh] overflow-y-auto p-5">
          <div className="mb-4 flex items-start justify-between">
            <div className="flex items-center gap-3">
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${visual.tile}`}>
                <Icon className="h-4.5 w-4.5" />
              </span>
              <div>
                <span className="mb-0.5 block text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {typeDef.label}
                </span>
                <h2 className="text-sm font-semibold text-foreground">
                  {isEditing ? `Edit ${existing?.name}` : `Connect ${typeDef.label}`}
                </h2>
              </div>
            </div>
            <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          <div className="space-y-3">
            <Field label="Name">
              <input
                ref={firstFieldRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Production Postgres"
                className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
              />
            </Field>

            {typeDef.fields.map((f) => (
              <ConnectorFieldInput
                key={f.key}
                field={f}
                value={values[f.key] ?? ""}
                onChange={(v) => setValues((prev) => ({ ...prev, [f.key]: v }))}
                editingPlaceholder={isEditing ? "Leave blank to keep current value" : undefined}
              />
            ))}
          </div>

          {formError && <p className="mt-3 text-xs text-destructive">{formError}</p>}

          <div className="mt-5 flex justify-end gap-2 border-t border-border/60 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim() || missingRequired}
              className="rounded-md bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {saving ? "Saving…" : isEditing ? "Save changes" : "Connect"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Blocking-error popup for a failed delete.
 * ===========================================
 * Used for both the "still referenced by a notebook/workspace" 409 conflict
 * (the common, actionable case — amber "in use" tone, with the blocking
 * notebooks/workspaces broken out as labeled lists) and any other
 * unexpected delete failure (generic destructive-tone message). Either way
 * this is a deliberate, must-acknowledge modal rather than an inline row
 * message, since a failed delete is easy to miss as a small red line if the
 * user has already moved on.
 */
function DeleteBlockedModal({
  connectionName,
  message,
  conflict,
  onClose,
}: {
  connectionName: string;
  message: string;
  conflict: boolean;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Backend message shape (see DataConnectorService.delete_connection):
  // "Can't remove this connection — it's still used by notebook(s) [Churn
  // EDA, Onboarding funnel] and workspace(s) [Churn model v1]. Remove it
  // from those first." -> pull each bracketed group into its own list.
  const usageGroups = conflict
    ? Array.from(message.matchAll(/(notebook|workspace)\(s\)\s*\[([^\]]*)\]/g)).map((m) => ({
        kind: m[1] as "notebook" | "workspace",
        items: m[2]
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      }))
    : [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-card shadow-lg">
        <div className="p-5">
          <div className="mb-4 flex items-start gap-3">
            <span
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${
                conflict
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-500"
                  : "border-destructive/30 bg-destructive/10 text-destructive"
              }`}
            >
              <AlertTriangle className="h-4.5 w-4.5" />
            </span>
            <div className="min-w-0 pt-0.5">
              <h2 className="text-sm font-semibold text-foreground">
                {conflict ? "Connection still in use" : "Couldn't remove connection"}
              </h2>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{connectionName}</p>
            </div>
          </div>

          {usageGroups.length > 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-foreground">
                This connection is still attached to the following — remove it there first:
              </p>
              {usageGroups.map((group) => (
                <div key={group.kind}>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {group.kind === "notebook" ? "Notebooks" : "Workspaces"}
                  </p>
                  <ul className="space-y-1">
                    {group.items.map((item) => (
                      <li
                        key={item}
                        className="rounded-md border border-border bg-secondary px-2.5 py-1.5 text-sm text-foreground"
                      >
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-foreground">{message}</p>
          )}

          <div className="mt-5 flex justify-end border-t border-border/60 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90"
            >
              Got it
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

function ConnectorFieldInput({
  field,
  value,
  onChange,
  editingPlaceholder,
}: {
  field: ConnectorField;
  value: string;
  onChange: (v: string) => void;
  editingPlaceholder?: string;
}): JSX.Element {
  const placeholder = field.type === "password" && editingPlaceholder ? editingPlaceholder : field.placeholder;
  return (
    <Field label={field.required ? `${field.label} *` : field.label}>
      {field.type === "textarea" ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={4}
          className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 font-mono text-xs text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
        />
      ) : (
        <input
          type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
        />
      )}
    </Field>
  );
}
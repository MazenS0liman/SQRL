import React, { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Database,
  UploadCloud,
  Sparkles,
  Loader2,
  AlertCircle,
  FileSpreadsheet,
  FileText,
  Plug,
  Pencil,
  RotateCcw,
  Trash2,
  Check,
  X,
  EyeOff,
  Eye,
  Search,
  Plus,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Square,
  Play,
  FileDown,
} from "lucide-react";

import type {
  ChatMessage,
  Notebook,
  NotebookChart,
  NotebookCell,
  NotebookDataSource,
  ConnectorSummary,
  DashboardLayout,
  DashboardVersion,
} from "@/types";

import { Chat } from "@/components/chat/Chat";
import AnalyticsCard from "@/components/visual/AnalyticsCard";
import { DashboardModal, type DashboardLayoutPayload } from "@/components/visual/DashboardModal";
import { apiFetch, statusMeta, timeAgo } from "./shared";
import { authHeaders } from '@/lib/auth';

// ── Types ─────────────────────────────────────────────────────────────────
interface DataSourcePreview {
  table_name: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count?: number | null;
}

export interface PendingCellRun {
  kind: "eda" | "question" | "dashboard";
  promise: Promise<NotebookCell>;
  controller: AbortController;
}

const pendingRuns = new Map<string, PendingCellRun>();

export function getPendingRun(id: string) { return pendingRuns.get(id); }
export function setPendingRun(id: string, run: PendingCellRun) { pendingRuns.set(id, run); }
export function clearPendingRun(id: string, run: PendingCellRun) {
  if (pendingRuns.get(id) === run) pendingRuns.delete(id);
}

// ── ConfirmDialog ─────────────────────────────────────────────────────────
function ConfirmDialog({
  open, title, message, confirmLabel = "Confirm",
  destructive = false, alertOnly = false, onConfirm, onCancel,
  alertActionLabel
}: {
  open: boolean; title: string; message: string; confirmLabel?: string;
  destructive?: boolean; alertOnly?: boolean; alertActionLabel?: string;
  onConfirm: () => void; onCancel: () => void;
}): JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <AlertCircle className={`mt-0.5 h-5 w-5 shrink-0 ${destructive ? "text-destructive" : "text-primary"}`} />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{message}</p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          {!alertOnly && (
            <button onClick={onCancel}
              className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-secondary">
              Cancel
            </button>
          )}
          {alertOnly && (
            <button onClick={onCancel}
              className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-secondary">
              Dismiss
            </button>
          )}
          <button onClick={onConfirm}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:opacity-90 ${destructive ? "bg-destructive" : "bg-primary-gradient"}`}>
            {alertOnly ? (alertActionLabel ?? "Got it") : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── DataSourcesModal ──────────────────────────────────────────────────────
function DataSourcesModal({
  notebook, onClose, onNotebookUpdated, onRequestAddSource,
}: {
  notebook: Notebook; onClose: () => void;
  onNotebookUpdated: (nb: Notebook) => void; onRequestAddSource: () => void;
}): JSX.Element {
  const [selectedId, setSelectedId] = useState(notebook.data_sources[0]?.id ?? "");
  const [preview, setPreview] = useState<DataSourcePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(new Set());
  const [columnFilter, setColumnFilter] = useState("");
  const [showColumnPicker, setShowColumnPicker] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  const [renaming, setRenaming] = useState(false);

  const selectedSource = notebook.data_sources.find((d) => d.id === selectedId);

  useEffect(() => {
    if (!selectedId) { setPreview(null); setLoading(false); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    apiFetch<DataSourcePreview>(
      `/notebook/${notebook.id}/data-source/preview?source_id=${encodeURIComponent(selectedId)}`
    ).then((data) => {
      if (cancelled) return;
      setPreview(data); setVisibleColumns(new Set(data.columns));
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load preview.");
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [notebook.id, selectedId]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const allColumns = preview?.columns ?? [];
  const filteredColumns = allColumns.filter((c) => c.toLowerCase().includes(columnFilter.trim().toLowerCase()));
  const allFilteredVisible = filteredColumns.length > 0 && filteredColumns.every((c) => visibleColumns.has(c));
  const displayColumns = allColumns.filter((c) => visibleColumns.has(c));

  const toggleColumn = (col: string) => setVisibleColumns((prev) => {
    const next = new Set(prev);
    if (next.has(col)) { if (next.size === 1) return prev; next.delete(col); } else next.add(col);
    return next;
  });
  const toggleAllFiltered = () => setVisibleColumns((prev) => {
    const next = new Set(prev);
    if (allFilteredVisible) { filteredColumns.forEach((c) => { if (next.size > 1) next.delete(c); }); }
    else filteredColumns.forEach((c) => next.add(c));
    return next;
  });

  const requestRemove = (id: string) => {
    setConfirmRemoveId(id);
  };
  const performRemove = async () => {
    if (!confirmRemoveId) return;
    const id = confirmRemoveId;
    const wasLastSource = notebook.data_sources.length <= 1;
    setConfirmRemoveId(null); setRemovingId(id); setError(null);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebook.id}/data-source/${id}`, { method: "DELETE" });
      onNotebookUpdated(nb);
      if (wasLastSource || nb.data_sources.length === 0) {
        onClose();
        return;
      }
      if (selectedId === id) setSelectedId(nb.data_sources[0]?.id ?? "");
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to remove data source."); }
    finally { setRemovingId(null); }
  };
  const saveRename = async (id: string) => {
    const label = draftLabel.trim(); if (!label) return;
    setRenaming(true);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebook.id}/data-source/${id}`,
        { method: "PATCH", body: JSON.stringify({ label }) });
      onNotebookUpdated(nb); setEditingId(null);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to rename."); }
    finally { setRenaming(false); }
  };

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="flex w-full max-w-4xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl" style={{ maxHeight: "82vh" }}>
          {/* Sidebar */}
          <div className="flex w-56 shrink-0 flex-col border-r border-border bg-secondary/30">
            <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
              <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Sources</span>
              <button onClick={onRequestAddSource}
                className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                title="Add data source"><Plus className="h-3.5 w-3.5" /></button>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
              {notebook.data_sources.map((ds) => (
                <div key={ds.id}
                  className={`group flex items-center gap-1 px-2 py-1.5 ${ds.id === selectedId ? "bg-primary/10 border-l-2 border-primary" : "hover:bg-secondary/60 border-l-2 border-transparent"}`}>
                  {editingId === ds.id ? (
                    <form className="flex min-w-0 flex-1 items-center gap-1"
                      onSubmit={(e) => { e.preventDefault(); saveRename(ds.id); }}>
                      <input autoFocus value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Escape") setEditingId(null); }}
                        className="min-w-0 flex-1 rounded border border-input bg-background px-1.5 py-0.5 text-xs text-foreground outline-none focus:ring-1 focus:ring-ring" />
                      <button type="submit" disabled={renaming || !draftLabel.trim()}
                        className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40">
                        {renaming ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3 text-primary" />}
                      </button>
                      <button type="button" onClick={() => setEditingId(null)}
                        className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground">
                        <X className="h-3 w-3" />
                      </button>
                    </form>
                  ) : (
                    <>
                      <button onClick={() => setSelectedId(ds.id)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
                        {ds.kind === "upload"
                          ? <FileSpreadsheet className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                          : <Plug className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                        <span className="truncate text-xs text-foreground">{ds.label || ds.table_name}</span>
                      </button>
                      <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100">
                        <button onClick={() => { setEditingId(ds.id); setDraftLabel(ds.label || ds.table_name); }}
                          className="rounded p-1 text-muted-foreground hover:text-foreground" title="Rename">
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button onClick={() => requestRemove(ds.id)} disabled={removingId === ds.id}
                          className="rounded p-1 text-muted-foreground hover:text-destructive disabled:opacity-40" title="Remove">
                          {removingId === ds.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
          {/* Preview */}
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden p-5">
            <div className="mb-4 flex items-center justify-between">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-foreground">
                  {selectedSource?.label || selectedSource?.table_name || "No source selected"}
                </h3>
                {preview?.row_count != null && <p className="text-xs text-muted-foreground">{preview.row_count.toLocaleString()} rows total</p>}
              </div>
              <button onClick={onClose} className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
            </div>
            {loading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading preview…</div>}
            {error && <div className="flex items-center gap-2 text-sm text-destructive"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}
            {!loading && !error && preview && (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="mb-3 flex items-center gap-2">
                  <button onClick={() => setShowColumnPicker((v) => !v)}
                    className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-secondary">
                    <Pencil className="h-3 w-3" />Columns ({visibleColumns.size}/{allColumns.length})
                  </button>
                </div>
                {showColumnPicker && (
                  <div className="mb-3 rounded-lg border border-border bg-secondary/30 p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <div className="relative flex-1">
                        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                        <input value={columnFilter} onChange={(e) => setColumnFilter(e.target.value)} placeholder="Filter columns…"
                          className="w-full rounded-md border border-input bg-background py-1.5 pl-7 pr-2 text-xs text-foreground outline-none focus:ring-2 focus:ring-ring" />
                      </div>
                      <button onClick={toggleAllFiltered}
                        className="shrink-0 rounded-md border border-border px-2 py-1.5 text-xs font-medium text-foreground hover:bg-secondary">
                        {allFilteredVisible ? "Deselect all" : "Select all"}
                      </button>
                    </div>
                    <div className="flex max-h-32 flex-wrap content-start gap-1.5 overflow-y-auto">
                      {filteredColumns.map((col) => {
                        const isOn = visibleColumns.has(col);
                        return (
                          <button key={col} onClick={() => toggleColumn(col)}
                            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] transition-colors ${isOn ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"}`}>
                            {isOn && <Check className="h-2.5 w-2.5" />}{col}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {preview.rows.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No rows in this table yet.</p>
                ) : (
                  <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-border">
                    <table className="min-w-full text-left font-mono text-xs">
                      <thead className="sticky top-0 bg-secondary text-muted-foreground">
                        <tr>{displayColumns.map((c) => <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">{c}</th>)}</tr>
                      </thead>
                      <tbody>
                        {preview.rows.map((row, i) => (
                          <tr key={i} className={`border-t border-border ${i % 2 === 0 ? "" : "bg-secondary/20"}`}>
                            {displayColumns.map((c) => <td key={c} className="whitespace-nowrap px-3 py-1.5 text-foreground">{String(row[c] ?? "")}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="mt-2 shrink-0 text-xs text-muted-foreground">Showing first {preview.rows.length} rows</p>
              </div>
            )}
          </div>
        </div>
      </div>
      <ConfirmDialog open={confirmRemoveId !== null} title="Remove data source?" destructive
        message={
          notebook.data_sources.length <= 1
            ? "This is the notebook's only data source!"
            : "Cells referencing it may stop working. This can't be undone."
        }
        confirmLabel="Remove"
        onConfirm={performRemove} onCancel={() => setConfirmRemoveId(null)} />
    </>
  );
}

// ── TabButton ─────────────────────────────────────────────────────────────
function TabButton({
  active, onClick, icon: Icon, children,
}: {
  active: boolean; onClick: () => void;
  icon: React.ComponentType<{ className?: string }>; children: React.ReactNode;
}): JSX.Element {
  return (
    <button onClick={onClick}
      className={`flex flex-1 items-center justify-center gap-1.5 rounded-sm py-1.5 text-xs font-medium transition-colors ${active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
      <Icon className="h-3.5 w-3.5" />{children}
    </button>
  );
}

// ── UploadCsvPanel ────────────────────────────────────────────────────────
function UploadCsvPanel({ notebookId, onBound }: { notebookId: string; onBound: (nb: Notebook) => void }): JSX.Element {
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setUploading(true); setError(null);
    try {
      const fd = new FormData(); fd.append("file", file);
      const nb = await apiFetch<Notebook>(`/notebook/${notebookId}/data-source/upload`, { method: "POST", body: fd });
      onBound(nb);
    } catch (err) { setError(err instanceof Error ? err.message : "Upload failed."); }
    finally { setUploading(false); }
  };

  return (
    <div>
      <div onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => { e.preventDefault(); setDragActive(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
        onClick={() => inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${dragActive ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-secondary/30"}`}>
        {uploading ? (
          <><Loader2 className="h-7 w-7 animate-spin text-primary" /><p className="text-sm text-muted-foreground">Uploading…</p></>
        ) : (
          <><UploadCloud className="h-7 w-7 text-muted-foreground" />
            <div><p className="text-sm font-medium text-foreground">Drop a CSV here, or click to browse</p>
              <p className="mt-0.5 text-xs text-muted-foreground">.csv files only</p></div></>
        )}
        <input ref={inputRef} type="file" accept=".csv" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}

// ── ExistingFilePanel ─────────────────────────────────────────────────────
function ExistingFilePanel({ notebookId, onBound }: { notebookId: string; onBound: (nb: Notebook) => void }): JSX.Element {
  const [files, setFiles] = useState<{ fileUrl: string; fileName: string }[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [binding, setBinding] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    apiFetch<{ success: boolean; files: { fileUrl: string; fileName: string }[] }>("/file/")
      .then((res) => setFiles(res.files ?? []))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load files."))
      .finally(() => setLoadingFiles(false));
  }, []);

  const filtered = files.filter((f) => f.fileName.toLowerCase().includes(query.trim().toLowerCase()));

  const bind = async (file: { fileUrl: string; fileName: string }) => {
    setBinding(file.fileUrl); setError(null);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebookId}/data-source/existing-file`,
        { method: "POST", body: JSON.stringify({ file_url: file.fileUrl, file_name: file.fileName }) });
      onBound(nb);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to attach file."); }
    finally { setBinding(null); }
  };

  if (loadingFiles) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading files…</div>;

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search files…"
          className="w-full rounded-lg border border-input bg-secondary py-1.5 pl-8 pr-3 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="max-h-60 overflow-y-auto rounded-lg border border-border">
        {filtered.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {files.length === 0 ? "No files uploaded yet." : `No files match "${query}".`}
          </p>
        ) : filtered.map((f) => (
          <button key={f.fileUrl} onClick={() => bind(f)} disabled={binding !== null}
            className="flex w-full items-center justify-between gap-2 border-b border-border px-3 py-2.5 text-left text-sm last:border-b-0 hover:bg-secondary disabled:opacity-50">
            <span className="flex min-w-0 items-center gap-2">
              <FileSpreadsheet className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate text-sm text-foreground">{f.fileName}</span>
            </span>
            {binding === f.fileUrl && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── ConnectorPanel ────────────────────────────────────────────────────────
function ConnectorPanel({ notebookId, onBound }: { notebookId: string; onBound: (nb: Notebook) => void }): JSX.Element {
  const [connectors, setConnectors] = useState<ConnectorSummary[]>([]);
  const [loadingConn, setLoadingConn] = useState(true);
  const [selectedConnector, setSelectedConnector] = useState("");
  const [tables, setTables] = useState<string[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);
  const [selectedTable, setSelectedTable] = useState("");
  const [binding, setBinding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<ConnectorSummary[]>("/connector")
      .then(setConnectors)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load connectors."))
      .finally(() => setLoadingConn(false));
  }, []);

  useEffect(() => {
    if (!selectedConnector) { setTables([]); return; }
    setLoadingTables(true); setSelectedTable("");
    apiFetch<string[]>(`/connector/${selectedConnector}/tables`)
      .then(setTables)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load tables."))
      .finally(() => setLoadingTables(false));
  }, [selectedConnector]);

  const handleBind = async () => {
    if (!selectedConnector || !selectedTable) return;
    setBinding(true); setError(null);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebookId}/data-source/connector`,
        { method: "POST", body: JSON.stringify({ connector_id: selectedConnector, table_name: selectedTable }) });
      onBound(nb);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to connect table."); }
    finally { setBinding(false); }
  };

  if (loadingConn) return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading connectors…</div>;
  if (connectors.length === 0) return (
    <div className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
      No connectors configured yet. Add one on the Data Connectors page first.
    </div>
  );

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Connector</label>
        <select value={selectedConnector} onChange={(e) => setSelectedConnector(e.target.value)}
          className="w-full rounded-lg border border-input bg-secondary px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring">
          <option value="">Select a connector…</option>
          {connectors.map((c) => <option key={c.connector_id} value={c.connector_id}>{c.name} ({c.type})</option>)}
        </select>
      </div>
      {selectedConnector && (
        <div>
          <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Table</label>
          <select value={selectedTable} onChange={(e) => setSelectedTable(e.target.value)} disabled={loadingTables}
            className="w-full rounded-lg border border-input bg-secondary px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring disabled:opacity-50">
            <option value="">{loadingTables ? "Loading tables…" : "Select a table…"}</option>
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <button onClick={handleBind} disabled={!selectedConnector || !selectedTable || binding}
        className="w-full rounded-lg bg-primary-gradient px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
        {binding ? "Connecting…" : "Connect table"}
      </button>
    </div>
  );
}

// ── AddDataSourceModal ────────────────────────────────────────────────────
function AddDataSourceModal({
  notebookId, onClose, onAdded,
}: { notebookId: string; onClose: () => void; onAdded: (nb: Notebook) => void }): JSX.Element {
  const [tab, setTab] = useState<"upload" | "existing" | "connector">("upload");

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const handleBound = (nb: Notebook) => { onAdded(nb); onClose(); };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="text-base font-semibold text-foreground">Add a data source</h3>
            <p className="mt-1 text-xs text-muted-foreground">Upload a CSV, pick from your files, or connect a table.</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>
        <div className="mb-4 flex gap-1 rounded-lg bg-secondary p-1">
          <TabButton active={tab === "upload"} onClick={() => setTab("upload")} icon={UploadCloud}>Upload CSV</TabButton>
          <TabButton active={tab === "existing"} onClick={() => setTab("existing")} icon={FileText}>From Files</TabButton>
          <TabButton active={tab === "connector"} onClick={() => setTab("connector")} icon={Plug}>Connector</TabButton>
        </div>
        {tab === "upload" ? <UploadCsvPanel notebookId={notebookId} onBound={handleBound} />
          : tab === "existing" ? <ExistingFilePanel notebookId={notebookId} onBound={handleBound} />
          : <ConnectorPanel notebookId={notebookId} onBound={handleBound} />}
      </div>
    </div>
  );
}

// ── DataSourceSetup (empty state) ─────────────────────────────────────────
function DataSourceSetup({ notebook, onBound }: { notebook: Notebook; onBound: (nb: Notebook) => void }): JSX.Element {
  const [tab, setTab] = useState<"upload" | "existing" | "connector">("upload");
  return (
    <div className="flex flex-1 items-center justify-center px-6 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
            <Database className="h-7 w-7 text-muted-foreground" />
          </div>
          <h2 className="text-xl font-semibold text-foreground">Connect a dataset</h2>
          <p className="mt-2 text-sm text-muted-foreground">Upload a CSV, pick from your files, or connect an existing table to start exploring.</p>
        </div>
        <div className="mb-4 flex gap-1 rounded-lg bg-secondary p-1">
          <TabButton active={tab === "upload"} onClick={() => setTab("upload")} icon={UploadCloud}>Upload CSV</TabButton>
          <TabButton active={tab === "existing"} onClick={() => setTab("existing")} icon={FileText}>From Files</TabButton>
          <TabButton active={tab === "connector"} onClick={() => setTab("connector")} icon={Plug}>Connector</TabButton>
        </div>
        {tab === "upload" ? <UploadCsvPanel notebookId={notebook.id} onBound={onBound} />
          : tab === "existing" ? <ExistingFilePanel notebookId={notebook.id} onBound={onBound} />
          : <ConnectorPanel notebookId={notebook.id} onBound={onBound} />}
      </div>
    </div>
  );
}

// ── toChartDef ────────────────────────────────────────────────────────────
async function downloadNotebookFile(path: string, filename: string) {
  const baseUrl = import.meta.env.VITE_BACKEND_API_BASE_URL?.replace(/\/$/, '');
  const response = await fetch(`${baseUrl}${path}`, { headers: authHeaders() });
  if (!response.ok) throw new Error(`Download failed: ${response.statusText}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function toChartDef(chart: NotebookChart) {
  return {
    id: chart.id, title: chart.title, description: "",
    plot_type: chart.plot_type, analytical_type: "",
    x: chart.x, y: chart.y, group_by: null,
    observation: chart.observation || "", data: chart.data || [],
    total_row_count: chart.total_row_count ?? null,
    truncated: chart.truncated ?? false,
  };
}

// ── SourceChip ────────────────────────────────────────────────────────────
function SourceChip({ source }: { source: NotebookDataSource }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
      {source.kind === "upload" ? <FileSpreadsheet className="h-2.5 w-2.5" /> : <Plug className="h-2.5 w-2.5" />}
      {source.label || source.table_name}
    </span>
  );
}

function MarkdownCellCard({
  cell, index, onUpdated, onDeleted, onMove, canMoveUp, canMoveDown
}: {
  cell: NotebookCell; index: number;
  onUpdated: (cell: NotebookCell) => void;
  onDeleted: (cellId: string) => void;
  onMove?: (direction: 'up' | 'down') => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
}): JSX.Element {
  const [isEditing, setIsEditing] = useState(!cell.query.trim());
  const [draft, setDraft] = useState(cell.query);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const save = async () => {
    const content = draft.trim();
    if (!content) return;
    setBusy(true); setError(null);
    try {
      const updated = await apiFetch<NotebookCell>(`/notebook/${cell.notebook_id}/cells/${cell.id}`,
        { method: "PUT", body: JSON.stringify({ query: content }) });
      onUpdated(updated);
      setIsEditing(false);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to save."); }
    finally { setBusy(false); }
  };

  const remove = async () => {
    setConfirmDeleteOpen(false); setBusy(true); setError(null);
    try {
      await apiFetch<void>(`/notebook/${cell.notebook_id}/cells/${cell.id}`, { method: "DELETE" });
      onDeleted(cell.id);
    } catch (err) { setError(err instanceof Error ? err.message : "Delete failed."); setBusy(false); }
  };

  function escapeHtmlMd(s: string): string {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function mdInline(s: string): string {
    return s
      .replace(/`([^`]+)`/g, '<code class="rounded bg-secondary px-1 py-0.5 font-mono text-[0.85em]">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer" class="text-primary underline underline-offset-2">$1</a>');
  }

  /** Minimal markdown renderer — headings, bold/italic/code/links, bullet lists,
   * paragraphs. Not a full CommonMark implementation, but enough for Jupyter-
   * style notebook notes without pulling in a markdown dependency. Input is
   * HTML-escaped before any tags are added, so raw HTML in a cell can't inject. */
  function simpleMarkdownToHtml(md: string): string {
    const lines = md.split("\n");
    const out: string[] = [];
    let inList = false;
    const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };

    for (const raw of lines) {
      const heading = raw.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        out.push(`<h${level} class="mt-3 first:mt-0 font-semibold text-foreground">${mdInline(escapeHtmlMd(heading[2]))}</h${level}>`);
        continue;
      }
      const item = raw.match(/^\s*[-*]\s+(.*)$/);
      if (item) {
        if (!inList) { out.push('<ul class="list-disc pl-5 space-y-0.5">'); inList = true; }
        out.push(`<li>${mdInline(escapeHtmlMd(item[1]))}</li>`);
        continue;
      }
      closeList();
      if (raw.trim() === "") { out.push("<div class='h-2'></div>"); continue; }
      out.push(`<p class="leading-relaxed">${mdInline(escapeHtmlMd(raw))}</p>`);
    }
    closeList();
    return out.join("\n");
  }

  return (
    <div className="group relative flex gap-0 rounded-xl border border-border bg-card shadow-sm hover:border-border/80">
      <div className="flex w-12 shrink-0 flex-col items-center pt-3 pb-2 select-none">
        <span className="font-mono text-[10px] text-muted-foreground/50">[{index}]</span>
        <div className="mt-2 w-0.5 flex-1 rounded-full bg-sky-500/30" />
      </div>
      <div className="min-w-0 flex-1 py-3 pr-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-600 dark:text-sky-400">
            Markdown
          </span>
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            {!isEditing && (
              <button onClick={() => { setDraft(cell.query); setIsEditing(true); }} title="Edit" aria-label="Edit"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                <Pencil className="h-3.5 w-3.5" />
              </button>
            )}
            {canMoveUp && (
              <button onClick={() => onMove?.('up')}
                title="Move up" aria-label="Move up"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                <ChevronUp className="h-3 w-3" />
              </button>
            )}
            {canMoveDown && (
              <button onClick={() => onMove?.('down')}
                title="Move down" aria-label="Move down"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                <ChevronDown className="h-3 w-3" />
              </button>
            )}
            <button onClick={() => setConfirmDeleteOpen(true)} disabled={busy} title="Delete" aria-label="Delete"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40">
              {busy && !isEditing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{error}
          </div>
        )}

        {isEditing ? (
          <div className="space-y-2">
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} autoFocus
              placeholder="# Heading, **bold**, *italic*, `code`, - list item, [link](url)…"
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
            <div className="flex items-center gap-2">
              <button onClick={save} disabled={busy || !draft.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save
              </button>
              <button onClick={() => { setIsEditing(false); setDraft(cell.query); setError(null); }} disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
                <X className="h-3.5 w-3.5" />Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="text-sm text-foreground" dangerouslySetInnerHTML={{ __html: simpleMarkdownToHtml(cell.query) }} />
        )}
      </div>

      <ConfirmDialog open={confirmDeleteOpen} title="Delete this markdown cell?" message="This can't be undone."
        confirmLabel="Delete" destructive onConfirm={remove} onCancel={() => setConfirmDeleteOpen(false)} />
    </div>
  );
}

function MarkdownDraftEditor({
  value, onChange, onSave, onCancel, busy, error,
}: {
  value: string; onChange: (v: string) => void; onSave: () => void; onCancel: () => void;
  busy: boolean; error: string | null;
}): JSX.Element {
  return (
    <div className="flex gap-0 rounded-xl border border-dashed border-primary/40 bg-card shadow-sm">
      <div className="flex w-12 shrink-0 flex-col items-center pt-3 pb-2 select-none">
        <span className="font-mono text-[10px] text-muted-foreground/50">[·]</span>
        <div className="mt-2 w-0.5 flex-1 rounded-full bg-sky-500/30" />
      </div>
      <div className="min-w-0 flex-1 py-3 pr-3">
        <span className="mb-2 inline-block rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-600 dark:text-sky-400">
          New markdown cell
        </span>
        {error && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{error}
          </div>
        )}
        <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={6} autoFocus
          placeholder="# Heading, **bold**, *italic*, `code`, - list item, [link](url)…"
          className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
        <div className="mt-2 flex items-center gap-2">
          <button onClick={onSave} disabled={busy || !value.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}Add cell
          </button>
          <button onClick={onCancel} disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
            <X className="h-3.5 w-3.5" />Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ── CellCard — Jupyter-style numbered cell ────────────────────────────────
function CellCard({
  cell, index, dataSources, onUpdated, onDeleted, canRerun, onMove, canMoveUp, canMoveDown
}: {
  cell: NotebookCell; index: number;
  dataSources: NotebookDataSource[];
  onUpdated: (cell: NotebookCell) => void;
  onDeleted: (cellId: string) => void;
  canRerun: boolean;
  onMove?: (direction: 'up' | 'down') => void;
  canMoveUp?: boolean;
  canMoveDown?: boolean;
}): JSX.Element {
  const [isEditing, setIsEditing] = useState(false);
  const [draftQuery, setDraftQuery] = useState(cell.query);
  const [draftSourceIds, setDraftSourceIds] = useState<Set<string>>(
    new Set(cell.data_source_ids.length > 0 ? cell.data_source_ids : dataSources.map((d) => d.id))
  );
  const [busy, setBusy] = useState<"regenerate" | "delete" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [showAnalysis, setShowAnalysis] = useState(true);

  const isQuestion = cell.type === "question";
  const isDashboard = cell.type === "dashboard";

  const hasMultipleSources = dataSources.length > 1;
  const resolvedCellSources = cell.data_source_ids
    .map((id) => dataSources.find((d) => d.id === id))
    .filter((d): d is NotebookDataSource => Boolean(d));

  const regenerate = async (query?: string, dataSourceIds?: string[]) => {
    setBusy("regenerate"); setActionError(null);
    try {
      const updated = await apiFetch<NotebookCell>(`/notebook/${cell.notebook_id}/cells/${cell.id}`,
        { method: "PUT", body: JSON.stringify({ query, data_source_ids: dataSourceIds }) });
      onUpdated(updated); setIsEditing(false);
    } catch (err) { setActionError(err instanceof Error ? err.message : "Regeneration failed."); }
    finally { setBusy(null); }
  };

  const remove = async () => {
    setConfirmDeleteOpen(false); setBusy("delete"); setActionError(null);
    try {
      await apiFetch<void>(`/notebook/${cell.notebook_id}/cells/${cell.id}`, { method: "DELETE" });
      onDeleted(cell.id);
    } catch (err) { setActionError(err instanceof Error ? err.message : "Delete failed."); setBusy(null); }
  };

  const toggleDraftSource = (id: string) => setDraftSourceIds((prev) => {
    const next = new Set(prev);
    if (next.has(id)) { if (next.size === 1) return prev; next.delete(id); } else next.add(id);
    return next;
  });

  const validCharts = (cell.charts ?? []).filter((c) => !c.error);
  const body = validCharts.length > 0 ? { charts: validCharts.map(toChartDef) } : null;
  const isBusy = busy !== null;
  const isEda = cell.type === "eda";

  return (
    <div className={`group relative flex gap-0 rounded-xl border transition-colors ${cell.status === "error" ? "border-destructive/40" : "border-border hover:border-border/80"} bg-card shadow-sm`}>
      {/* Left gutter — Jupyter-style execution bracket */}
      <div className="flex w-12 shrink-0 flex-col items-center pt-3 pb-2 select-none">
        <span className="font-mono text-[10px] text-muted-foreground/50">[{index}]</span>
        <div className={`mt-2 w-0.5 flex-1 rounded-full ${busy === "regenerate" ? "bg-primary animate-pulse" : cell.status === "error" ? "bg-destructive/40" : isEda ? "bg-primary/30" : "bg-border"}`} />
      </div>

      {/* Cell body */}
      <div className="min-w-0 flex-1 py-3 pr-3">
        {/* Cell header row */}
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button onClick={() => setCollapsed((v) => !v)}
              className="flex items-center gap-1.5 text-left">
              {collapsed
                ? <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />}
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                  isEda || isDashboard ? "bg-primary/15 text-primary"
                  : "bg-secondary text-muted-foreground"
                }`}>
                  {isDashboard ? "Dashboard" : isEda ? "EDA" : "Question"}
                </span>
            </button>
            <span className="text-[11px] text-muted-foreground">{timeAgo(cell.created_at)}</span>
          </div>

          {/* Action buttons — visible on hover */}
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            {!isEditing && cell.reply && (
              <button onClick={() => setShowAnalysis((v) => !v)}
                title={showAnalysis ? "Hide analysis" : "Show analysis"} aria-label="Toggle analysis"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                {showAnalysis ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            )}
            {(isQuestion || hasMultipleSources) && !isEditing && (
              <button onClick={() => { setDraftQuery(cell.query); setDraftSourceIds(new Set(cell.data_source_ids.length > 0 ? cell.data_source_ids : dataSources.map((d) => d.id))); setActionError(null); setIsEditing(true); }}
                disabled={isBusy} title="Edit" aria-label="Edit"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40">
                <Pencil className="h-3.5 w-3.5" />
              </button>
            )}
            {!isEditing && (
              <button onClick={() => regenerate(isQuestion ? cell.query : undefined, cell.data_source_ids.length > 0 ? cell.data_source_ids : undefined)}
                disabled={isBusy || !canRerun} title={canRerun ? "Re-run" : "Add a data source to re-run"} aria-label="Re-run"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40">
                {busy === "regenerate" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
              </button>
            )}
            {canMoveUp && (
              <button onClick={() => onMove?.('up')}
                title="Move up" aria-label="Move up"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                <ChevronUp className="h-3 w-3" />
              </button>
            )}
            {canMoveDown && (
              <button onClick={() => onMove?.('down')}
                title="Move down" aria-label="Move down"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
                <ChevronDown className="h-3 w-3" />
              </button>
            )}
            <button onClick={() => setConfirmDeleteOpen(true)} disabled={isBusy} title="Delete" aria-label="Delete"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40">
              {busy === "delete" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {!collapsed && (
          <>
            {isEditing ? (
              <div className="space-y-3 rounded-lg border border-input bg-secondary/30 p-3">
                {isQuestion && (
                  <textarea value={draftQuery} onChange={(e) => setDraftQuery(e.target.value)} rows={2} autoFocus
                    className="w-full resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
                )}
                {(
                  <div>
                    <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Scoped to</p>
                    <div className="flex flex-wrap gap-1.5">
                      {dataSources.map((ds) => {
                        const isOn = draftSourceIds.has(ds.id);
                        return (
                          <button key={ds.id} type="button" onClick={() => toggleDraftSource(ds.id)}
                            className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 font-mono text-[11px] transition-colors ${isOn ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"}`}>
                            {isOn && <Check className="h-3 w-3" />}{ds.label || ds.table_name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <button onClick={() => regenerate(isQuestion ? draftQuery.trim() : undefined, Array.from(draftSourceIds))}
                    disabled={(isQuestion && !draftQuery.trim()) || busy === "regenerate" || !canRerun}
                    title={canRerun ? undefined : "Add a data source to re-run"}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
                    {busy === "regenerate" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                    Save &amp; re-run
                  </button>
                  <button onClick={() => { setIsEditing(false); setDraftQuery(cell.query); }}
                    disabled={busy === "regenerate"}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
                    <X className="h-3.5 w-3.5" />Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                {cell.query && (
                  <div className="mb-3 inline-block max-w-full rounded-xl border border-border bg-secondary/50 px-3 py-2 text-sm font-medium text-foreground">
                    {cell.query}
                  </div>
                )}
                {hasMultipleSources && resolvedCellSources.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-1">
                    {resolvedCellSources.map((ds) => <SourceChip key={ds.id} source={ds} />)}
                  </div>
                )}
              </>
            )}

            {actionError && (
              <div className="my-2 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" />{actionError}
              </div>
            )}

            {!isEditing && (
              busy === "regenerate" ? (
                <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />Re-running analysis…
                </div>
              ) : cell.status === "error" ? (
                <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{cell.error || "This request failed."}
                </div>
              ) : (
                <div>
                  {/* {cell.reply && showAnalysis && (
                    <div className="mb-3 whitespace-pre-line rounded-xl border border-border bg-secondary/20 px-4 py-3 text-sm leading-relaxed text-foreground">
                      {cell.reply}
                    </div>
                  )} */}
                  {body && <AnalyticsCard body={body} layout={isDashboard ? "dashboard" : "tabs"} />}
                </div>
              )
            )}
          </>
        )}
      </div>

      <ConfirmDialog open={confirmDeleteOpen} title="Delete this cell?" message="This can't be undone."
        confirmLabel="Delete" destructive onConfirm={remove} onCancel={() => setConfirmDeleteOpen(false)} />
    </div>
  );
}

function PrepScriptModal({
  loading, error, content, onClose, onDownload,
}: { loading: boolean; error: string | null; content: string | null; onClose: () => void; onDownload: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="flex w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl" style={{ maxHeight: "82vh" }}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <h3 className="text-sm font-semibold text-foreground">Data prep script</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Reproduces this notebook's cleaning &amp; feature-engineering steps on the raw data.
            </p>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {loading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Generating…</div>}
          {error && <div className="flex items-center gap-2 text-sm text-destructive"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}
          {!loading && !error && content && (
            <pre className="overflow-x-auto rounded-lg border border-border bg-secondary/30 p-3 font-mono text-xs text-foreground">{content}</pre>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <button onClick={onDownload} disabled={!content}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
            <FileDown className="h-3.5 w-3.5" />Download .py
          </button>
        </div>
      </div>
    </div>
  );
}

// ── NotebookWorkspace ─────────────────────────────────────────────────────
function NotebookWorkspace({
  notebook, cells, setCells, onAddDataSource
}: {
  notebook: Notebook; cells: NotebookCell[];
  setCells: React.Dispatch<React.SetStateAction<NotebookCell[]>>;
  onAddDataSource: () => void;
}): JSX.Element {

  const [running, setRunning] = useState<"eda" | "question" | "dashboard" | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [noSourceWarning, setNoSourceWarning] = useState(false);
  const feedEndRef = useRef<HTMLDivElement>(null);

  const hasDataSource = notebook.data_sources.length > 0;

  const [dashboardOpen, setDashboardOpen] = useState(false);
  const [dashboardCellId, setDashboardCellId] = useState<string | null>(null);
  const [dashboardCharts, setDashboardCharts] = useState<NotebookChart[]>([]);
  const [dashboardLayout, setDashboardLayout] = useState<DashboardLayout | null>(null);
  const [dashboardVersions, setDashboardVersions] = useState<DashboardVersion[]>([]);
  const [dashboardSaveStatus, setDashboardSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const layoutSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [dashboardSummary, setDashboardSummary] = useState<
    { dataset_description: string; key_findings: never[]; recommended_next_steps: never[] } | undefined
  >(undefined);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState<string | null>(null);

  const loadDashboardVersions = useCallback(async (cellId: string) => {
    try {
      const versions = await apiFetch<DashboardVersion[]>(
        `/notebook/${notebook.id}/cells/${cellId}/dashboard/versions`
      );
      setDashboardVersions(versions);
    } catch {
      setDashboardVersions([]);
    }
  }, [notebook.id]);

  // NotebookWorkspace — new state + handler, alongside exportBusy/exportError
  const [scriptOpen, setScriptOpen] = useState(false);
  const [scriptContent, setScriptContent] = useState<string | null>(null);
  const [scriptLoading, setScriptLoading] = useState(false);
  const [scriptError, setScriptError] = useState<string | null>(null);

  const openPrepScript = useCallback(async () => {
    setScriptOpen(true);
    if (scriptContent) return; // cached from a prior open this session
    setScriptLoading(true); setScriptError(null);
    try {
      const baseUrl = import.meta.env.VITE_BACKEND_API_BASE_URL?.replace(/\/$/, "");
      const res = await fetch(`${baseUrl}/notebook/${notebook.id}/export/prep-script`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`Failed: ${res.statusText}`);
      setScriptContent(await res.text());
    } catch (err) {
      setScriptError(err instanceof Error ? err.message : "Failed to generate the script.");
    } finally {
      setScriptLoading(false);
    }
  }, [notebook.id, scriptContent]);

  const downloadPrepScript = () => {
    if (!scriptContent) return;
    const blob = new Blob([scriptContent], { type: "text/x-python" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "prepare_data.py"; a.click();
    URL.revokeObjectURL(url);
  };

  const [draftMarkdown, setDraftMarkdown] = useState<string | null>(null);
  const [savingMarkdown, setSavingMarkdown] = useState(false);
  const [markdownError, setMarkdownError] = useState<string | null>(null);

  const addMarkdownCell = () => { setDraftMarkdown(""); setMarkdownError(null); };

  const saveMarkdownDraft = useCallback(async () => {
    if (draftMarkdown === null || !draftMarkdown.trim()) return;
    setSavingMarkdown(true); setMarkdownError(null);
    try {
      const cell = await apiFetch<NotebookCell>(`/notebook/${notebook.id}/cells`,
        { method: "POST", body: JSON.stringify({ type: "markdown", query: draftMarkdown }) });
      setCells((prev) => [...prev, cell]);
      setDraftMarkdown(null);
    } catch (err) {
      setMarkdownError(err instanceof Error ? err.message : "Failed to add markdown cell.");
    } finally {
      setSavingMarkdown(false);
    }
  }, [notebook.id, draftMarkdown, setCells]);

  // A notebook has at most one dashboard cell (regenerating PUTs the same
  // id rather than creating a new one). Hydrate from it whenever the
  // notebook changes so a previously generated dashboard survives page
  // reloads and notebook switches instead of being silently discarded.
  // Deliberately NOT re-run on every `cells` change — applyDashboardCell
  // below writes into `cells` itself, and we don't want that write to
  // bounce back and reset the state we just set.
  useEffect(() => {
    const persisted = cells.find((c) => c.type === "dashboard") ?? null;
    setDashboardCellId(persisted?.id ?? null);
    setDashboardCharts((persisted?.charts ?? []).filter((c) => !c.error));
    setDashboardLayout(persisted?.dashboard_layout ?? null);
    setDashboardSummary(
      persisted ? { dataset_description: persisted.reply, key_findings: [], recommended_next_steps: [] } : undefined
    );
    if (persisted?.id) {
      loadDashboardVersions(persisted.id);
    } else {
      setDashboardVersions([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notebook.id]);

  const saveDashboardLayout = useCallback(async (cellId: string, layout: DashboardLayoutPayload) => {
    setDashboardSaveStatus("saving");
    try {
      const updated = await apiFetch<NotebookCell>(
        `/notebook/${notebook.id}/cells/${cellId}/dashboard/layout`,
        { method: "PATCH", body: JSON.stringify(layout) }
      );
      setDashboardLayout(updated.dashboard_layout ?? null);
      setCells((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
      setDashboardSaveStatus("saved");
    } catch {
      setDashboardSaveStatus("error");
    }
  }, [notebook.id, setCells]);

  const handleDashboardLayoutChange = useCallback((layout: DashboardLayoutPayload) => {
    if (!dashboardCellId) return;
    if (layoutSaveTimer.current) clearTimeout(layoutSaveTimer.current);
    layoutSaveTimer.current = setTimeout(() => {
      saveDashboardLayout(dashboardCellId, layout);
    }, 800);
  }, [dashboardCellId, saveDashboardLayout]);

  useEffect(() => () => {
    if (layoutSaveTimer.current) clearTimeout(layoutSaveTimer.current);
  }, []);

  // Applies a dashboard cell to local UI state AND persists it into the
  // notebook's cell list — without this, the dashboard only ever lived in
  // this component's local state and vanished on remount/reload even
  // though the backend already had it saved.
  const applyDashboardCell = useCallback((cell: NotebookCell) => {
    setDashboardCellId(cell.id);
    setDashboardCharts((cell.charts ?? []).filter((c) => !c.error));
    setDashboardLayout(cell.dashboard_layout ?? null);
    setDashboardSummary({ dataset_description: cell.reply, key_findings: [], recommended_next_steps: [] });
    setCells((prev) => {
      const idx = prev.findIndex((c) => c.id === cell.id);
      if (idx === -1) return [...prev, cell];
      const next = [...prev];
      next[idx] = cell;
      return next;
    });
    loadDashboardVersions(cell.id);
  }, [setCells, loadDashboardVersions]);

  const openDashboard = useCallback(async () => {
    if (!hasDataSource) { setNoSourceWarning(true); return; }
    setDashboardOpen(true);
    // Already have a persisted dashboard for this notebook — show it as-is.
    // "Regenerate" inside the modal is how the user asks for a fresh run.
    if (dashboardCellId) return;
    setDashboardLoading(true); setDashboardError(null);
    try {
      const cell = await apiFetch<NotebookCell>(`/notebook/${notebook.id}/cells`,
        { method: "POST", body: JSON.stringify({ type: "dashboard" }) });
      applyDashboardCell(cell);
    } catch (err) {
      setDashboardError(err instanceof Error ? err.message : "Failed to generate the dashboard.");
    } finally {
      setDashboardLoading(false);
    }
  }, [notebook.id, hasDataSource, dashboardCellId, applyDashboardCell]);

  const regenerateDashboard = useCallback(async () => {
    setDashboardLoading(true); setDashboardError(null);
    try {
      const cell = dashboardCellId
        ? await apiFetch<NotebookCell>(`/notebook/${notebook.id}/cells/${dashboardCellId}`, { method: "PUT", body: JSON.stringify({}) })
        : await apiFetch<NotebookCell>(`/notebook/${notebook.id}/cells`, { method: "POST", body: JSON.stringify({ type: "dashboard" }) });
      applyDashboardCell(cell);
    } catch (err) {
      setDashboardError(err instanceof Error ? err.message : "Failed to regenerate the dashboard.");
    } finally {
      setDashboardLoading(false);
    }
  }, [notebook.id, dashboardCellId, applyDashboardCell]);

  // Fetches a raw sample of rows (and their columns) directly from one of
  // the notebook's data sources — same preview endpoint the "View data"
  // modal uses. Lets the dashboard's "Add chart" panel build a chart
  // straight from a table instead of only re-mapping an existing chart's
  // already-fetched data.
  const fetchSourcePreview = useCallback(async (sourceId: string) => {
    const preview = await apiFetch<DataSourcePreview>(
      `/notebook/${notebook.id}/data-source/preview?source_id=${encodeURIComponent(sourceId)}&limit=500`
    );
    return { columns: preview.columns, rows: preview.rows };
  }, [notebook.id]);

  const [exportBusy, setExportBusy] = useState<"report" | "notebook" | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleExportNotebook = useCallback(async () => {
    if (exportBusy) return;
    setExportBusy("notebook"); setExportError(null);
    try {
      await downloadNotebookFile(`/notebook/${notebook.id}/export/ipynb`, `${notebook.name || "notebook"}.ipynb`);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Failed to export the notebook.");
    } finally {
      setExportBusy(null);
    }
  }, [notebook.id, notebook.name, exportBusy]);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [cells.length, running]);

  const attachToRun = useCallback((run: PendingCellRun) => {
    setRunning(run.kind); setRunError(null);
    run.promise
      .then((cell) => setCells((prev) => [...prev, cell]))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") { setRunError("Stopped."); return; }
        setRunError(err instanceof Error ? err.message : "That request failed.");
      })
      .finally(() => { setRunning(null); clearPendingRun(notebook.id, run); });
  }, [notebook.id, setCells]);

  useEffect(() => {
    const pending = getPendingRun(notebook.id);
    if (pending) attachToRun(pending);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notebook.id]);

  const runCell = useCallback((type: "eda" | "question" | "dashboard", query?: string, dataSourceIds?: string[]) => {
    if (!hasDataSource) { setNoSourceWarning(true); return; }
    const controller = new AbortController();
    const promise = apiFetch<NotebookCell>(`/notebook/${notebook.id}/cells`,
      { method: "POST", body: JSON.stringify({ type, query, data_source_ids: dataSourceIds }), signal: controller.signal });
    const run: PendingCellRun = { kind: type, promise, controller };
    setPendingRun(notebook.id, run);
    attachToRun(run);
  }, [notebook.id, attachToRun, hasDataSource]);

  const stopRunning = useCallback(() => { getPendingRun(notebook.id)?.controller.abort(); }, [notebook.id]);

  const handleChatMessage = useCallback((message: ChatMessage) => {
    const text = message.content.trim();
    if (!text || running) return;
    if (!hasDataSource) { setNoSourceWarning(true); return; }
    const dataSourceIds = (message.data as { dataSourceIds?: string[] } | null)?.dataSourceIds;
    runCell("question", text, dataSourceIds);
  }, [running, runCell, hasDataSource]);

  const handleCellUpdated = useCallback((updated: NotebookCell) => {
    setCells((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  }, [setCells]);

  const handleCellDeleted = useCallback((cellId: string) => {
    setCells((prev) => prev.filter((c) => c.id !== cellId));
  }, [setCells]);

  // Reorders non-dashboard cells (dashboard cells always stay appended at
  // the end — they're not part of the manual ordering) and persists the
  // full new order via PATCH /cells/reorder. Optimistically updates local
  // state first, then reverts to the pre-move snapshot if the request fails.
  const handleMoveCell = useCallback((cellId: string, direction: 'up' | 'down') => {
    const nonDashboardCells = cells.filter((c) => c.type !== "dashboard");
    const currentIndex = nonDashboardCells.findIndex(c => c.id === cellId);
    if (currentIndex === -1) return;

    let newIndex = currentIndex;
    if (direction === 'up' && currentIndex > 0) {
      newIndex = currentIndex - 1;
    }
    if (direction === 'down' && currentIndex < nonDashboardCells.length - 1) {
      newIndex = currentIndex + 1;
    }
    if (newIndex === currentIndex) return;

    const newNonDashboardCells = [...nonDashboardCells];
    const [movedCell] = newNonDashboardCells.splice(currentIndex, 1);
    newNonDashboardCells.splice(newIndex, 0, movedCell);

    const dashboardCells = cells.filter(c => c.type === "dashboard");
    const previousCells = cells; // snapshot for revert on failure
    const newCells = [...newNonDashboardCells, ...dashboardCells];
    setCells(newCells);

    apiFetch<void>(`/notebook/${notebook.id}/cells/reorder`, {
      method: "PATCH",
      body: JSON.stringify(newCells.map(c => c.id)),
    }).catch(() => {
      setCells(previousCells);
    });
  }, [cells, setCells, notebook.id]);

  const primarySource = notebook.data_sources[0];
  const isEmpty = cells.length === 0 && !running;

  const dashboardChartDefs = useMemo(
    () => dashboardCharts.map(toChartDef),
    [dashboardCharts]
  );

  const nonDashboardCells = useMemo(
    () => cells.filter((c) => c.type !== "dashboard"),
    [cells]
  );

  return (
    <div className="relative flex flex-1 min-h-0 flex-col">
      {/* ── Notebook toolbar ── */}
      <div className="shrink-0 border-b border-border bg-secondary/30 px-4 py-1.5">
        <div className="mx-auto flex max-w-4xl items-center gap-1">
          <span className="font-mono text-[10px] text-muted-foreground">
            {notebook.data_sources.length === 1
              ? primarySource?.label || primarySource?.table_name
              : `${notebook.data_sources.length} sources`}
          </span>
          <div className="h-4 w-px bg-border mx-1" />
          <div className="flex-1" />
          <button onClick={addMarkdownCell} disabled={draftMarkdown !== null}
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
            <FileText className="h-3.5 w-3.5" />Add markdown
          </button>
          <button onClick={onAddDataSource}
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-secondary">
            <Plus className="h-3.5 w-3.5" />Add source
          </button>
            <button onClick={handleExportNotebook} disabled={exportBusy !== null}
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
            {exportBusy === "notebook" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDown className="h-3.5 w-3.5" />}
              Export notebook
          </button>
        </div>
      </div>

      {/* ── Cell feed ── */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin px-4 py-6">
        <div className="mx-auto max-w-4xl space-y-3">
          {isEmpty && hasDataSource && (
            <div className="flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-border py-20 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-card">
                <Sparkles className="h-5 w-5 text-muted-foreground" />
              </div>
              <div>
                <p className="text-sm font-medium text-foreground">
                  Ready to explore{" "}
                  <span className="text-primary">{notebook.data_sources.map((s) => s.label || s.table_name).join(", ")}</span>
                </p>
                <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                  Run quick analysis, or type a question below — e.g. "What drives churn in the last quarter?"
                </p>
              </div>
              <button onClick={() => runCell("eda")}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90">
                <Sparkles className="h-3.5 w-3.5" />Generate Quick Analysis
              </button>
            </div>
          )}
          {isEmpty && !hasDataSource && (
            <div className="flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-border py-20 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-card">
                <Database className="h-5 w-5 text-muted-foreground" />
              </div>
              <div>
                <p className="text-sm font-medium text-foreground">No data source attached</p>
                <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                  Add a CSV or connect a table to run EDA or ask questions.
                </p>
              </div>
              <button onClick={onAddDataSource}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-gradient px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90">
                <Plus className="h-3.5 w-3.5" />Add a data source
              </button>
            </div>
          )}

          {nonDashboardCells.map((cell, i) => {
            const canMoveUp = i > 0;
            const canMoveDown = i < nonDashboardCells.length - 1;
            const moveThisCell = (direction: 'up' | 'down') => handleMoveCell(cell.id, direction);
            return cell.type === "markdown" ? (
              <MarkdownCellCard
                key={cell.id}
                cell={cell}
                index={i + 1}
                onUpdated={handleCellUpdated}
                onDeleted={handleCellDeleted}
                onMove={moveThisCell}
                canMoveUp={canMoveUp}
                canMoveDown={canMoveDown}
              />
            ) : (
              <CellCard
                key={cell.id}
                cell={cell}
                index={i + 1}
                dataSources={notebook.data_sources}
                onUpdated={handleCellUpdated}
                onDeleted={handleCellDeleted}
                canRerun={hasDataSource}
                onMove={moveThisCell}
                canMoveUp={canMoveUp}
                canMoveDown={canMoveDown}
              />
            );
          })}

          {draftMarkdown !== null && (
            <MarkdownDraftEditor
              value={draftMarkdown}
              onChange={setDraftMarkdown}
              onSave={saveMarkdownDraft}
              onCancel={() => { setDraftMarkdown(null); setMarkdownError(null); }}
              busy={savingMarkdown}
              error={markdownError}
            />
          )}
          {running && (
            <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3">
              <div className="flex w-12 shrink-0 justify-center">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
              </div>
              <span className="text-sm text-muted-foreground">
                {running === "eda"
                  ? "Running exploratory analysis…"
                  : running === "dashboard"
                  ? "Building your dashboard…"
                  : "Analyzing your question…"}
              </span>
              <button onClick={stopRunning}
                className="ml-auto inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-foreground">
                <Square className="h-3 w-3" />Stop
              </button>
            </div>
          )}

          {runError && (
            <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />{runError}
            </div>
          )}
          <div ref={feedEndRef} />
        </div>
      </div>

      {/* ── Docked input ── */}
      <div className="shrink-0 border-t border-border bg-card px-4 py-3">
        <div className="mx-auto max-w-4xl">
          {exportBusy && (
            <p className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {exportBusy === "report" ? "Generating report…" : "Exporting notebook…"}
            </p>
          )}
          <Chat
            handleAddMessage={handleChatMessage}
            isProcessing={running !== null}
            onStop={stopRunning}
            dataSources={notebook.data_sources.map((s) => ({
              id: s.id, label: s.label ?? s.table_name, kind: s.kind, table_name: s.table_name,
            }))}
            onRunEda={() => (hasDataSource ? runCell("eda") : setNoSourceWarning(true))}
            onGenerateDashboard={openDashboard}
          />
        </div>
      </div>

      {dashboardOpen && (
        <DashboardModal
          charts={dashboardChartDefs}
          summary={dashboardSummary}
          loading={dashboardLoading}
          error={dashboardError}
          onClose={() => setDashboardOpen(false)}
          onRegenerate={regenerateDashboard}
          regenerating={dashboardLoading}
          initialLayout={dashboardLayout}
          onLayoutChange={handleDashboardLayoutChange}
          saveStatus={dashboardSaveStatus}
          versions={dashboardVersions}
          dataSources={notebook.data_sources.map((s) => ({
            id: s.id, label: s.label || s.table_name, table_name: s.table_name,
          }))}
          onFetchSourcePreview={fetchSourcePreview}
        />
      )}

      {scriptOpen && (
        <PrepScriptModal
          loading={scriptLoading}
          error={scriptError}
          content={scriptContent}
          onClose={() => setScriptOpen(false)}
          onDownload={downloadPrepScript}
        />
      )}

      <ConfirmDialog
        open={exportError !== null}
        title="Export failed"
        message={exportError ?? ""}
        alertOnly
        onConfirm={() => setExportError(null)}
        onCancel={() => setExportError(null)}
      />
      <ConfirmDialog
        open={noSourceWarning}
        title="No data source attached"
        message="This notebook doesn't have a data source. Add one before running EDA or asking questions — existing cells are still visible below."
        alertOnly
        onConfirm={() => { setNoSourceWarning(false); onAddDataSource(); }}
        onCancel={() => setNoSourceWarning(false)}
      />
    </div>
  );
}

// ── NotebookHeader ────────────────────────────────────────────────────────
function NotebookHeader({
  notebook, onBack, onViewData,
}: { notebook: Notebook; onBack: () => void; onViewData: () => void }): JSX.Element {
  const meta = statusMeta(notebook.status);
  return (
    <header className="flex shrink-0 items-center justify-between border-b border-border bg-card px-5 py-3">
      <div className="flex items-center gap-3 min-w-0">
        <button onClick={onBack}
          className="shrink-0 rounded-lg border border-border p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
          title="Back to notebooks">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold text-foreground">{notebook.name}</h1>
          {notebook.description && <p className="truncate text-xs text-muted-foreground">{notebook.description}</p>}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {notebook.data_sources.length > 0 && (
          <button onClick={onViewData}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-secondary">
            <Eye className="h-3.5 w-3.5" />View data
          </button>
        )}
        <div className="flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1">
          <span className={`h-1.5 w-1.5 rounded-full ${meta.dotClass}`} />
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{meta.label}</span>
        </div>
      </div>
    </header>
  );
}

// ── NotebookDetailPage ────────────────────────────────────────────────────
export default function NotebookDetailPage(): JSX.Element {
  const { notebookId } = useParams<{ notebookId: string }>();
  const navigate = useNavigate();

  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [cells, setCells] = useState<NotebookCell[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [showDataSources, setShowDataSources] = useState(false);
  const [showAddSource, setShowAddSource] = useState(false);

  const load = useCallback(async () => {
    if (!notebookId) return;
    setLoading(true); setPageError(null);
    try {
      const [nb, cellList] = await Promise.all([
        apiFetch<Notebook>(`/notebook/${notebookId}`),
        apiFetch<NotebookCell[]>(`/notebook/${notebookId}/cells`).catch(() => []),
      ]);
      setNotebook(nb); setCells(cellList);
    } catch (err) { setPageError(err instanceof Error ? err.message : "Failed to load notebook."); }
    finally { setLoading(false); }
  }, [notebookId]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />Loading notebook…
      </div>
    );
  }
  if (pageError || !notebook) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
        <p className="text-sm text-destructive">{pageError || "Notebook not found."}</p>
        <button onClick={() => navigate("/notebooks")} className="text-sm text-primary underline underline-offset-2">
          Back to notebooks
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <NotebookHeader notebook={notebook} onBack={() => navigate("/notebooks")} onViewData={() => setShowDataSources(true)} />

      {notebook.status === "empty" ? (
        cells.length === 0 ?
        (
          <DataSourceSetup notebook={notebook} onBound={(nb) => setNotebook(nb)} />
        ) : (
          <NotebookWorkspace notebook={notebook} cells={cells} setCells={setCells} onAddDataSource={() => setShowAddSource(true)} />
        )
      ) : (
        <NotebookWorkspace notebook={notebook} cells={cells} setCells={setCells} onAddDataSource={() => setShowAddSource(true)} />
      )}

      {showDataSources && notebook.data_sources.length > 0 && (
        <DataSourcesModal
          notebook={notebook}
          onClose={() => setShowDataSources(false)}
          onNotebookUpdated={(nb) => setNotebook(nb)}
          onRequestAddSource={() => { setShowDataSources(false); setShowAddSource(true); }}
        />
      )}
      {showAddSource && (
        <AddDataSourceModal notebookId={notebook.id} onClose={() => setShowAddSource(false)} onAdded={(nb) => setNotebook(nb)} />
      )}
    </div>
  );
}
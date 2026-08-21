import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Trash,
  Plus,
  Pencil,
  Search,
  Check,
  X,
  ArrowLeft,
  GripVertical,
  Sparkles,
  Rocket,
  Table2,
  Link2,
  Download,
  Trophy,
  Database,
  FileWarning,
} from "lucide-react";
import type {
  BuildResponse,
  ModelFile,
  ModelMetric,
  ModelsResponse,
  ModelSummary,
  DataSource,
  UploadResponse,
  Workspace,
} from "./shared";
import type { DataConnection } from "../connectors/shared";
import {
  API_BASE,
  DATA_TYPE_OPTIONS,
  PipelineRail,
  StepLabel,
  apiFetch,
  downloadOutputFile,
  formatPercent,
  SummaryKeyValueList,
} from "./shared";
import { connectorsApi } from "../connectors/shared";
import { authHeaders } from '@/lib/auth';

type TablePreviewEntry = {
  table: string;
  columns: string[];
  preview: Record<string, unknown>[];
  error?: string;
};

// Statuses for which the pipeline is actively progressing server-side and
// the frontend should keep polling for updates rather than wait for a
// single long-lived request to resolve.
const BUSY_STATUSES = new Set(["uploaded", "preprocessing", "modeling"]);

// Left panel width is user-resizable (see the drag handle between the two
// panels) and persisted across visits so it doesn't reset every time the
// user opens a workspace.
const LEFT_PANEL_WIDTH_KEY = "workspace-detail:left-panel-width";
const LEFT_PANEL_MIN = 300;
const LEFT_PANEL_MAX = 640;
const LEFT_PANEL_DEFAULT = 360;

/**
 * Workspace Detail Page
 * ======================
 * Two-panel layout: the left panel is the running record of every choice
 * the user has made (name, sources, target column, relate/optimize query)
 * and is where all of those choices are edited. The right panel is purely
 * the system's output — pipeline status, preprocessing summary, model
 * comparison, downloads — and never contains input controls.
 *
 * The panels are split by a draggable divider (see `PanelResizeHandle`)
 * so the left panel's width is under the user's control instead of fixed.
 *
 * NAVBAR NOTE: the little header bars inside each panel below use
 * `position: sticky` scoped to that panel's own `overflow-y-auto`
 * container — never `position: fixed`. Sticky respects normal document
 * flow, so it can only ever sit within this component's own box, which
 * itself renders in normal flow beneath whatever app-level navbar wraps
 * this route. `fixed` would anchor to the viewport instead and could
 * climb on top of that navbar, so it's intentionally avoided here except
 * for true modal overlays (which are supposed to cover everything).
 *
 * Data type is fixed at creation (see WorkspacePage) and shown here as a
 * read-only badge; changing the shape of a workspace's data after sources
 * have been attached isn't something the backend supports safely.
 *
 * Connector sources are attached in two decoupled steps:
 *   1. Pick which tables to attach (ConnectorListModal -> TableSelectModal).
 *      This step is table-only — no column fiddling — so attaching a
 *      source is a quick, low-friction action.
 *   2. Once attached, each table gets its own row in the left panel with
 *      an "Edit columns" action that opens `ColumnEditModal`, a focused,
 *      searchable, single-table column picker with a live preview.
 * This mirrors how the rest of the page works: attach broadly, refine
 * narrowly. Both save paths still go through remove-then-reattach under
 * the hood (see handleSaveTableSelection / handleSaveColumnEdit) since the
 * backend only supports attach/remove per table, not an in-place update.
 *
 * Pipeline status (the "Ingest / Prep / Train / Compare" rail) is kept in
 * sync by polling GET /workspace/{id} while a build is in flight or the
 * workspace's last-known status is a busy one — see the polling effect
 * near the bottom of the component. Without this, the rail only ever
 * jumps straight from "uploaded" to "completed" once the single /build
 * request resolves, since the backend updates status in the DB as it
 * progresses but nothing was reading it back mid-flight.
 */
export default function WorkspaceDetailPage(): JSX.Element {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const navigate = useNavigate();

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Workspace settings: rename, delete. Data type is read-only here.
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Input sources: uploads + connectors.
  const [sources, setSources] = useState<DataSource[]>([]);
  const [sourceColumns, setSourceColumns] = useState<Record<string, string[]>>({});
  const [previewsBySource, setPreviewsBySource] = useState<Record<string, Record<string, unknown>[]>>({});
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [removingSourceId, setRemovingSourceId] = useState<string | null>(null);

  // "+" add-source popover: choose upload vs. connector.
  const [showSourcePicker, setShowSourcePicker] = useState(false);

  // Connector picker (step 1) and table selector (step 2) are two fully
  // separate modals. The table selector is now table-only (no columns);
  // columns are edited afterwards, per-table, from the left panel.
  const [showConnectorListModal, setShowConnectorListModal] = useState(false);
  const [availableConnectors, setAvailableConnectors] = useState<DataConnection[]>([]);
  const [connectorsById, setConnectorsById] = useState<Record<string, DataConnection>>({});
  const [connectorsLoading, setConnectorsLoading] = useState(false);
  const [connectorError, setConnectorError] = useState<string | null>(null);

  const [showTableSelectModal, setShowTableSelectModal] = useState(false);
  const [pendingConnector, setPendingConnector] = useState<DataConnection | null>(null);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesError, setTablesError] = useState<string | null>(null);
  const [tablePreviews, setTablePreviews] = useState<TablePreviewEntry[]>([]);
  // Table-selection-only state: which tables (no column-level detail) are
  // checked in the "add tables" modal.
  const [selectedTables, setSelectedTables] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  // Per-table column editor (opened from a left-panel row's "Edit
  // columns" button), fully decoupled from the add-tables flow above.
  const [columnEditTarget, setColumnEditTarget] = useState<{
    connectorId: string;
    connectorName: string;
    table: string;
  } | null>(null);
  const [columnEditPreview, setColumnEditPreview] = useState<TablePreviewEntry | null>(null);
  const [columnEditLoading, setColumnEditLoading] = useState(false);
  const [columnEditError, setColumnEditError] = useState<string | null>(null);
  const [columnEditSaving, setColumnEditSaving] = useState(false);

  const [targetColumn, setTargetColumn] = useState("");
  const [buildQuery, setBuildQuery] = useState("");
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);
  const [conflictColumns, setConflictColumns] = useState<string[] | null>(null);

  const [preprocessingSummary, setPreprocessingSummary] = useState<Record<string, unknown> | null>(null);
  const [modelSummary, setModelSummary] = useState<ModelSummary | null>(null);
  const [modelComparison, setModelComparison] = useState<ModelMetric[]>([]);
  const [bestModel, setBestModel] = useState<string | null>(null);
  const [modelFiles, setModelFiles] = useState<ModelFile[]>([]);

  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadingUrl, setDownloadingUrl] = useState<string | null>(null);

  const [uploadColumnEditTarget, setUploadColumnEditTarget] = useState<{
    sourceId: string;
    sourceName: string;
  } | null>(null);
  const [uploadColumnEditPreview, setUploadColumnEditPreview] = useState<TablePreviewEntry | null>(null);
  const [uploadColumnEditLoading, setUploadColumnEditLoading] = useState(false);
  const [uploadColumnEditError, setUploadColumnEditError] = useState<string | null>(null);
  const [uploadColumnEditSaving, setUploadColumnEditSaving] = useState(false);

  // ---- Draggable left panel -------------------------------------------
  const [leftWidth, setLeftWidth] = useState<number>(() => {
    if (typeof window === "undefined") return LEFT_PANEL_DEFAULT;
    const stored = Number(window.localStorage.getItem(LEFT_PANEL_WIDTH_KEY));
    return Number.isFinite(stored) && stored >= LEFT_PANEL_MIN && stored <= LEFT_PANEL_MAX
      ? stored
      : LEFT_PANEL_DEFAULT;
  });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(LEFT_PANEL_DEFAULT);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // ---- Load ------------------------------------------------------------

  const loadWorkspace = async () => {
    if (!workspaceId) {
      setLoading(false);
      setLoadError(
        "No workspace id found in the URL. Check that the route is defined as " +
          "'/workspace/:workspaceId' (not ':projectId') wherever this page is routed."
      );
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      const ws = await apiFetch<Workspace>(`/workspace/${workspaceId}`);
      setWorkspace(ws);
      setNameDraft(ws.name);
      setTargetColumn(ws.target_column ?? "");
      setSources(ws.input_sources ?? []);
      if (ws.status === "completed") {
        const data = await apiFetch<ModelsResponse>(`/workspace/${workspaceId}/models`);
        setPreprocessingSummary(data.preprocessing_summary ?? null);
        setModelSummary(data.model_summary ?? null);
        setModelComparison(data.model_comparison);
        setBestModel(data.best_model ?? null);
        setModelFiles(data.model_files);
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load workspace.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  // ---- Live status polling ----------------------------------------------
  //
  // A single POST /build/structured call runs ingest -> preprocess -> train
  // synchronously and only resolves once everything is done, but the
  // backend writes intermediate status (preprocessing/modeling) to the DB
  // as it goes. Without polling, the rail never shows those in-between
  // states — it jumps straight from "uploaded" to "completed" when the
  // fetch finally resolves. This polls a lightweight status/read endpoint
  // while a build is in flight (or the workspace was left mid-pipeline,
  // e.g. after a page reload) and stops as soon as it settles.
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatusOnly = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const ws = await apiFetch<Workspace>(`/workspace/${workspaceId}`);
      setWorkspace((prev) => (prev ? { ...prev, status: ws.status, error: ws.error } : ws));
      if (ws.status === "completed") {
        const data = await apiFetch<ModelsResponse>(`/workspace/${workspaceId}/models`);
        setPreprocessingSummary(data.preprocessing_summary ?? null);
        setModelSummary(data.model_summary ?? null);
        setModelComparison(data.model_comparison);
        setBestModel(data.best_model ?? null);
        setModelFiles(data.model_files);
      }
    } catch {
      // Silent — a transient poll failure shouldn't disrupt the page, the
      // next tick (or the build request's own resolution) will catch up.
    }
  }, [workspaceId]);

  useEffect(() => {
    const shouldPoll = building || (workspace ? BUSY_STATUSES.has(workspace.status) : false);
    if (shouldPoll) {
      pollTimer.current = setInterval(refreshStatusOnly, 2000);
    }
    return () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [building, workspace?.status, refreshStatusOnly]);

  // ---- Draggable left panel ----------------------------------------------

  const handleDragStart = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    dragStartX.current = e.clientX;
    dragStartWidth.current = leftWidth;
  };

  useEffect(() => {
    if (!isDragging) return;

    const onMove = (e: MouseEvent) => {
      const delta = e.clientX - dragStartX.current;
      const next = Math.min(LEFT_PANEL_MAX, Math.max(LEFT_PANEL_MIN, dragStartWidth.current + delta));
      setLeftWidth(next);
    };
    const onUp = () => setIsDragging(false);

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [isDragging]);

  useEffect(() => {
    if (!isDragging) {
      window.localStorage.setItem(LEFT_PANEL_WIDTH_KEY, String(leftWidth));
    }
  }, [isDragging, leftWidth]);

  const handleDragHandleKeyDown = (e: React.KeyboardEvent) => {
    // Keyboard-accessible resize: arrow keys nudge by 16px.
    if (e.key === "ArrowLeft") {
      setLeftWidth((w) => Math.max(LEFT_PANEL_MIN, w - 16));
    } else if (e.key === "ArrowRight") {
      setLeftWidth((w) => Math.min(LEFT_PANEL_MAX, w + 16));
    }
  };

  // ---- Workspace settings: rename ----------------------------------------

  const handleSaveName = async () => {
    if (!workspace || !nameDraft.trim() || nameDraft.trim() === workspace.name) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    setSettingsError(null);
    try {
      const updated = await apiFetch<Workspace>(`/workspace/${workspace.workspace_id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: nameDraft.trim() }),
      });
      setWorkspace(updated);
      setEditingName(false);
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "Failed to rename workspace.");
    } finally {
      setSavingName(false);
    }
  };

  // ---- Workspace settings: delete ------------------------------------------

  const handleDelete = async () => {
    if (!workspace) return;
    setDeleting(true);
    setSettingsError(null);
    try {
      await apiFetch(`/workspace/${workspace.workspace_id}`, { method: "DELETE" });
      navigate("/workspace");
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : "Failed to delete workspace.");
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  // ---- Uploads (single or multiple files, any supported type) -------------

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0 || !workspace) return;

    setUploading(true);
    setUploadError(null);
    try {
      const formData = new FormData();
      Array.from(files).forEach((f) => formData.append("files", f));

      const res = await fetch(`${API_BASE}/workspace/${workspace.workspace_id}/upload`, {
        method: "POST",
        body: formData,
        headers: authHeaders(),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Upload failed.");
      }
      const data: UploadResponse = await res.json();

      setSources(data.sources);
      setSourceColumns((prev) => {
        const next = { ...prev };
        for (const u of data.uploaded) next[u.source_id] = u.columns ?? [];
        return next;
      });
      setPreviewsBySource((prev) => {
        const next = { ...prev };
        for (const u of data.uploaded) {
          if (u.preview) next[u.source_id] = u.preview;
        }
        return next;
      });

      if (!targetColumn) {
        const firstCols = data.uploaded.find((u) => u.columns && u.columns.length > 0)?.columns ?? [];
        if (firstCols.length > 0) setTargetColumn(firstCols[0]);
      }

      setWorkspace((prev) => (prev ? { ...prev, status: "uploaded" } : prev));
      setPreprocessingSummary(null);
      setModelSummary(null);
      setModelComparison([]);
      setModelFiles([]);
      setBestModel(null);
      setConflictColumns(null);
      setBuildError(null);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleRemoveSource = async (sourceId: string) => {
    if (!workspace) return;
    setRemovingSourceId(sourceId);
    try {
      const updatedSources = await apiFetch<DataSource[]>(
        `/workspace/${workspace.workspace_id}/sources/${sourceId}`,
        { method: "DELETE" }
      );
      setSources(updatedSources);
      setSourceColumns((prev) => {
        const { [sourceId]: _drop, ...rest } = prev;
        return rest;
      });
      setConflictColumns(null);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Failed to remove source.");
    } finally {
      setRemovingSourceId(null);
    }
  };

  // ---- Connector list modal -------------------------------------------------

  const ensureConnectorsLoaded = async (): Promise<Record<string, DataConnection>> => {
    if (Object.keys(connectorsById).length > 0) return connectorsById;
    const all = await connectorsApi.list();
    const map: Record<string, DataConnection> = {};
    for (const c of all) map[c.connector_id] = c;
    setAvailableConnectors(all);
    setConnectorsById(map);
    return map;
  };

  const openConnectorListModal = async () => {
    setShowConnectorListModal(true);
    setConnectorError(null);
    setConnectorsLoading(true);
    try {
      await ensureConnectorsLoaded();
    } catch (err) {
      setConnectorError(err instanceof Error ? err.message : "Failed to load connectors.");
    } finally {
      setConnectorsLoading(false);
    }
  };

  // ---- Table select modal: pick tables only, no column detail --------------
  // This is deliberately a lightweight, table-only step now. Column
  // selection happens afterwards, per table, from the left panel — see
  // openColumnEditor below. This keeps "add a source" fast, and makes
  // "customize what I'm sending" a separate, more deliberate action.

  const openTableSelectModalForAdd = async (conn: DataConnection) => {
    setShowConnectorListModal(false);
    setPendingConnector(conn);
    setShowTableSelectModal(true);
    setSelectedTables(new Set());
    setTablePreviews([]);
    setTablesError(null);
    setTablesLoading(true);
    try {
      const result = await connectorsApi.previewAllTables(conn.connector_id);
      setTablePreviews(result.tables);
    } catch (err) {
      setTablesError(err instanceof Error ? err.message : "Couldn't list tables for this connector.");
    } finally {
      setTablesLoading(false);
    }
  };

  const closeTableSelectModal = () => {
    setShowTableSelectModal(false);
    setPendingConnector(null);
    setSelectedTables(new Set());
    setTablePreviews([]);
    setTablesError(null);
  };

  const toggleTableSelected = (table: string) => {
    setSelectedTables((prev) => {
      const next = new Set(prev);
      if (next.has(table)) next.delete(table);
      else next.add(table);
      return next;
    });
  };

  const handleSaveTableSelection = async () => {
    if (!workspace || !pendingConnector || selectedTables.size === 0) return;
    setSaving(true);
    setTablesError(null);
    try {
      const updatedSources = await apiFetch<DataSource[]>(
        `/workspace/${workspace.workspace_id}/sources/connector`,
        {
          method: "POST",
          body: JSON.stringify({
            connector_id: pendingConnector.connector_id,
            // No columns object => backend attaches every column; the
            // user narrows this later via "Edit columns" if they want to.
            tables: Array.from(selectedTables).map((table) => ({ table, columns: null })),
          }),
        }
      );
      setSources(updatedSources);
      setConflictColumns(null);
      closeTableSelectModal();
    } catch (err) {
      setTablesError(err instanceof Error ? err.message : "Failed to attach tables.");
    } finally {
      setSaving(false);
    }
  };

  // ---- Per-table column editor (opened from the left panel) ----------------

  const openColumnEditor = async (connectorId: string, table: string) => {
    const connectorName = connectorsById[connectorId]?.name ?? connectorId;
    setColumnEditTarget({ connectorId, connectorName, table });
    setColumnEditPreview(null);
    setColumnEditError(null);
    setColumnEditLoading(true);
    try {
      const map = await ensureConnectorsLoaded();
      const conn = map[connectorId];
      if (!conn) throw new Error("This connector is no longer available.");
      const result = await connectorsApi.previewAllTables(conn.connector_id);
      const entry = result.tables.find((t) => t.table === table);
      if (!entry) throw new Error(`Table '${table}' is no longer available on this connector.`);
      setColumnEditPreview(entry);
    } catch (err) {
      setColumnEditError(err instanceof Error ? err.message : "Failed to load table.");
    } finally {
      setColumnEditLoading(false);
    }
  };

  const closeColumnEditor = () => {
    setColumnEditTarget(null);
    setColumnEditPreview(null);
    setColumnEditError(null);
  };

  const handleSaveColumnEdit = async (newColumns: Set<string>) => {
    if (!workspace || !columnEditTarget || !columnEditPreview) return;
    const { connectorId, table } = columnEditTarget;
    const existing = sources.find(
      (s) => s.kind === "connector" && s.connector_id === connectorId && s.name === table
    );
    if (!existing) {
      setColumnEditError("Couldn't find this table's source entry — try reopening it.");
      return;
    }

    setColumnEditSaving(true);
    setColumnEditError(null);
    try {
      // Backend only supports attach/remove per table, not an in-place
      // column update, so this is implemented as remove-then-reattach —
      // same pattern used for the whole-connector edit this replaces.
      await apiFetch<DataSource[]>(`/workspace/${workspace.workspace_id}/sources/${existing.source_id}`, {
        method: "DELETE",
      });

      const allColumns = columnEditPreview.columns;
      const columnsPayload = newColumns.size === allColumns.length ? null : Array.from(newColumns);

      const updatedSources = await apiFetch<DataSource[]>(
        `/workspace/${workspace.workspace_id}/sources/connector`,
        {
          method: "POST",
          body: JSON.stringify({
            connector_id: connectorId,
            tables: [{ table, columns: columnsPayload }],
          }),
        }
      );
      setSources(updatedSources);
      setConflictColumns(null);
      closeColumnEditor();
    } catch (err) {
      setColumnEditError(err instanceof Error ? err.message : "Failed to save columns.");
    } finally {
      setColumnEditSaving(false);
    }
  };

  const openUploadColumnEditor = async (sourceId: string, sourceName: string) => {
      if (!workspace) return;
      setUploadColumnEditTarget({ sourceId, sourceName });
      setUploadColumnEditPreview(null);
      setUploadColumnEditError(null);
      setUploadColumnEditLoading(true);
      try {
        const entry = await apiFetch<TablePreviewEntry>(
          `/workspace/${workspace.workspace_id}/sources/${sourceId}/preview`
        );
        setUploadColumnEditPreview(entry);
      } catch (err) {
        setUploadColumnEditError(err instanceof Error ? err.message : "Failed to load columns.");
      } finally {
        setUploadColumnEditLoading(false);
      }
    };

  const closeUploadColumnEditor = () => {
    setUploadColumnEditTarget(null);
    setUploadColumnEditPreview(null);
    setUploadColumnEditError(null);
  };

  const handleSaveUploadColumnEdit = async (newColumns: Set<string>) => {
    if (!workspace || !uploadColumnEditTarget || !uploadColumnEditPreview) return;
    setUploadColumnEditSaving(true);
    setUploadColumnEditError(null);
    try {
      const allCols = uploadColumnEditPreview.columns;
      const columnsPayload = newColumns.size === allCols.length ? null : Array.from(newColumns);
      const updatedSources = await apiFetch<DataSource[]>(
        `/workspace/${workspace.workspace_id}/sources/${uploadColumnEditTarget.sourceId}/columns`,
        { method: "PATCH", body: JSON.stringify({ columns: columnsPayload }) }
      );
      setSources(updatedSources);
      setConflictColumns(null);
      closeUploadColumnEditor();
    } catch (err) {
      setUploadColumnEditError(err instanceof Error ? err.message : "Failed to save columns.");
    } finally {
      setUploadColumnEditSaving(false);
    }
  };

  // ---- Column union + client-side duplicate detection ----------------------

  const columnsBySource = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const s of sources) {
      map[s.source_id] = s.columns ?? sourceColumns[s.source_id] ?? [];
    }
    return map;
  }, [sources, sourceColumns]);

  const allColumns = useMemo(() => {
    const seen = new Set<string>();
    const ordered: string[] = [];
    for (const cols of Object.values(columnsBySource)) {
      for (const c of cols) {
        if (!seen.has(c)) {
          seen.add(c);
          ordered.push(c);
        }
      }
    }
    return ordered;
  }, [columnsBySource]);

  const clientSideConflicts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const cols of Object.values(columnsBySource)) {
      for (const c of cols) {
        if (c === targetColumn) continue;
        counts[c] = (counts[c] ?? 0) + 1;
      }
    }
    return Object.keys(counts).filter((c) => counts[c] > 1).sort();
  }, [columnsBySource, targetColumn]);

  const effectiveConflicts = conflictColumns ?? (clientSideConflicts.length > 0 ? clientSideConflicts : null);

  const schemasLookDifferent = useMemo(() => {
    const columnSets = Object.values(columnsBySource).filter((c) => c.length > 0);
    if (columnSets.length < 2) return false;
    const asKeys = columnSets.map((cols) => [...cols].sort().join("|"));
    const identical = new Set(asKeys).size === 1;
    const rowCounts = new Set(sources.map((s) => s.row_count ?? -1));
    return !identical && rowCounts.size > 1;
  }, [columnsBySource, sources]);

  // ---- Group connector sources by connector so the left panel shows one
  // row per connector, with one sub-row per table and an "Edit columns"
  // action on each table. ---------------------------------------------------

  const connectorGroups = useMemo(() => {
    const groups: Record<string, DataSource[]> = {};
    for (const s of sources) {
      if (s.kind === "connector" && s.connector_id) {
        (groups[s.connector_id] ??= []).push(s);
      }
    }
    return groups;
  }, [sources]);

  const uploadSources = useMemo(() => sources.filter((s) => s.kind === "upload"), [sources]);

  // ---- Build ---------------------------------------------------------------

  const handleBuild = async () => {
    if (!targetColumn || !workspace) return;
    if (clientSideConflicts.length > 0) {
      setConflictColumns(clientSideConflicts);
      return;
    }
    setBuilding(true);
    setBuildError(null);
    setConflictColumns(null);
    try {
      const data = await apiFetch<BuildResponse>(`/workspace/${workspace.workspace_id}/build/structured`, {
        method: "POST",
        body: JSON.stringify({
          target_column: targetColumn,
          query: buildQuery.trim() || undefined,
        }),
      });
      setPreprocessingSummary(data.preprocessing_summary);
      setModelSummary(data.model_summary);
      setModelComparison(data.model_comparison);
      setBestModel(data.best_model);
      setModelFiles(data.model_files);
      setWorkspace((prev) => (prev ? { ...prev, status: data.status, target_column: targetColumn } : prev));
    } catch (err) {
      const conflictCols = (err as { conflicting_columns?: string[] })?.conflicting_columns;
      if (conflictCols && conflictCols.length > 0) {
        setConflictColumns(conflictCols);
      } else {
        setBuildError(err instanceof Error ? err.message : "Model building failed.");
      }
      setWorkspace((prev) => (prev ? { ...prev, status: "failed" } : prev));
    } finally {
      setBuilding(false);
    }
  };

  const handleDownload = async (modelKey: string, fileUrl: string) => {
    if (!workspace) return;
    setDownloadError(null);
    setDownloadingUrl(fileUrl);
    try {
      await downloadOutputFile(workspace.workspace_id, fileUrl, `${modelKey}.joblib`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed.");
    } finally {
      setDownloadingUrl(null);
    }
  };

  if (loading) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-background text-foreground">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading workspace…</p>
        </div>
      </div>
    );
  }

  if (loadError || !workspace) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-background text-foreground">
        <div className="flex max-w-md flex-col items-center gap-3 rounded-xl border border-dashed border-border px-8 py-10 text-center">
          <FileWarning className="h-8 w-8 text-destructive" />
          <p className="text-sm text-destructive">{loadError ?? "Workspace not found."}</p>
          <button
            onClick={() => navigate("/workspace")}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to Workspace
          </button>
        </div>
      </div>
    );
  }

  const hasAnySource = sources.length > 0;
  const canBuild = hasAnySource && Boolean(targetColumn) && !building && clientSideConflicts.length === 0;
  const isBusy = building || workspace.status === "preprocessing" || workspace.status === "modeling";
  const isStructured = workspace.data_type === "structured";
  const dataTypeLabel = DATA_TYPE_OPTIONS.find((o) => o.value === workspace.data_type)?.label ?? workspace.data_type;

  return (
    // Normal-flow container (no fixed/absolute on the root), so this page
    // always renders beneath whatever app navbar wraps this route.
    <div className="flex h-full min-h-0 bg-background text-foreground">
      {/* ————————————————————— LEFT PANEL: every user choice ————————————————————— */}
      <aside
        style={{ width: leftWidth, flexBasis: leftWidth }}
        className="relative flex shrink-0 flex-col overflow-y-auto border-r border-border/70 bg-secondary/20"
      >
        {/* Sticky (NOT fixed) in-panel header — scoped to this aside's own
            scroll container, so it only ever floats within this box. */}
        <div className="sticky top-0 z-10 border-b border-border/70 bg-background/95 px-5 pb-4 pt-5 backdrop-blur">
          {/* Empty div */}
          <div className="mb-10 flex items-center justify-between gap-2">
          </div>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              {editingName ? (
                <div className="flex flex-col gap-2">
                  <input
                    autoFocus
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveName();
                      if (e.key === "Escape") {
                        setNameDraft(workspace.name);
                        setEditingName(false);
                      }
                    }}
                    className="w-full rounded-md border border-input bg-secondary px-2.5 py-1 text-base font-semibold tracking-tight text-foreground outline-none focus:ring-2 focus:ring-ring"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleSaveName}
                      disabled={savingName || !nameDraft.trim()}
                      className="rounded-md bg-primary-gradient px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:opacity-40"
                    >
                      {savingName ? "Saving…" : "Save"}
                    </button>
                    <button
                      onClick={() => {
                        setNameDraft(workspace.name);
                        setEditingName(false);
                      }}
                      className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-secondary"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <h1 className="group flex items-center gap-1.5 text-base font-semibold tracking-tight text-foreground">
                  <span className="truncate">{workspace.name}</span>
                  <button
                    onClick={() => setEditingName(true)}
                    aria-label="Rename workspace"
                    className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                </h1>
              )}
            </div>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="shrink-0 rounded-md border border-border p-1.5 text-destructive transition-colors hover:bg-destructive/10"
              aria-label="Delete workspace"
            >
              <Trash className="h-4 w-4" />
            </button>
          </div>

          <span className="mt-3 inline-flex w-fit items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1 font-mono text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            <Database className="h-3 w-3" />
            {dataTypeLabel}
          </span>

          {settingsError && <p className="mt-2 text-xs text-destructive">{settingsError}</p>}
        </div>

        <div className="flex-1 px-5 pb-8 pt-5">
          {!isStructured && (
            <div className="mb-5 rounded-lg border border-border bg-secondary/60 px-3 py-2.5 text-xs text-muted-foreground">
              Only <span className="font-medium text-foreground">structured</span> (tabular) workspaces support
              preprocessing and model building today. You can still upload files here.
            </div>
          )}

          <div className="mb-6 rounded-xl border border-border bg-card p-3.5 shadow-sm">
            <PipelineRail status={workspace.status} />
            {workspace.error && <p className="mt-2 text-sm text-destructive">{workspace.error}</p>}
          </div>

          {/* ---- Sources ---- */}
          <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <StepLabel n="01">Input data</StepLabel>
              <div className="relative">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={isStructured ? ".csv" : undefined}
                  multiple
                  onChange={handleFileChange}
                  className="hidden"
                />
                <button
                  onClick={() => !uploading && setShowSourcePicker((v) => !v)}
                  disabled={uploading}
                  aria-haspopup="menu"
                  aria-expanded={showSourcePicker}
                  aria-label="Add a data source"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {uploading ? <span className="text-xs">…</span> : <Plus className="h-4 w-4" />}
                </button>

                {showSourcePicker && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowSourcePicker(false)} />
                    <div
                      role="menu"
                      className="absolute right-0 top-full z-50 mt-1.5 w-56 overflow-hidden rounded-md border border-border bg-card p-1 shadow-lg"
                    >
                      <button
                        role="menuitem"
                        onClick={() => {
                          setShowSourcePicker(false);
                          fileInputRef.current?.click();
                        }}
                        className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-secondary"
                      >
                        <Table2 className="h-3.5 w-3.5 text-muted-foreground" />
                        Upload file(s)
                      </button>
                      <button
                        role="menuitem"
                        onClick={() => {
                          setShowSourcePicker(false);
                          openConnectorListModal();
                        }}
                        className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-secondary"
                      >
                        <Link2 className="h-3.5 w-3.5 text-muted-foreground" />
                        Connect a data source
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>

            {uploadError && <p className="mb-2 text-sm text-destructive">{uploadError}</p>}

            {!hasAnySource && (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-6 text-center">
                <Database className="h-5 w-5 text-muted-foreground/60" />
                <p className="text-xs text-muted-foreground">No sources yet. Use the + button to add one.</p>
              </div>
            )}

            <ul className="space-y-2">
              {uploadSources.map((s) => (
                <SourceRow
                  key={s.source_id}
                  source={s}
                  columns={columnsBySource[s.source_id] ?? []}
                  preview={previewsBySource[s.source_id]}
                  onRemove={() => handleRemoveSource(s.source_id)}
                  onEditColumns={() => openUploadColumnEditor(s.source_id, s.name)}
                  removing={removingSourceId === s.source_id}
                />
              ))}

              {Object.entries(connectorGroups).map(([connectorId, groupSources]) => (
                <li key={connectorId} className="rounded-lg border border-border px-3 py-2.5 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="inline-flex shrink-0 items-center gap-1 rounded bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-primary">
                        <Link2 className="h-2.5 w-2.5" />
                        Connector
                      </span>
                      <span className="truncate font-mono text-foreground">
                        {connectorsById[connectorId]?.name ?? connectorId}
                      </span>
                    </div>
                    <button
                      onClick={() => openConnectorListModal()}
                      className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
                    >
                      + Add table
                    </button>
                  </div>
                  <ul className="mt-1.5 space-y-1 pl-1">
                    {groupSources.map((s) => {
                      const selectedCount = (s.columns ?? []).length;
                      return (
                        <li
                          key={s.source_id}
                          className="flex items-center justify-between gap-2 rounded px-1.5 py-1 text-xs text-muted-foreground hover:bg-secondary/50"
                        >
                          <span className="min-w-0 truncate font-mono">
                            {s.name}
                            {typeof s.row_count === "number" && ` · ${s.row_count} rows`}
                            {s.columns && s.columns.length > 0 && ` · ${selectedCount} cols`}
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            <button
                              onClick={() => openColumnEditor(connectorId, s.name)}
                              className="inline-flex items-center gap-1 text-primary hover:underline"
                            >
                              <Pencil className="h-3 w-3" />
                              Columns
                            </button>
                            <button
                              onClick={() => handleRemoveSource(s.source_id)}
                              disabled={removingSourceId === s.source_id}
                              className="text-destructive hover:underline disabled:opacity-40"
                            >
                              {removingSourceId === s.source_id ? "Removing…" : "Remove"}
                            </button>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>

            {effectiveConflicts && effectiveConflicts.length > 0 && (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2.5">
                <p className="text-sm font-medium text-destructive">Can't build: overlapping columns</p>
                <p className="mt-1 text-xs text-destructive/90">
                  These columns appear in more than one source:{" "}
                  <span className="font-mono">{effectiveConflicts.join(", ")}</span>. Rename or remove the
                  duplicate column in one of the sources before building.
                </p>
              </div>
            )}
          </div>

          {/* ---- Target column + query + build ---- */}
          {hasAnySource && isStructured && (
            <div className="mt-6 rounded-xl border border-primary/20 bg-gradient-to-b from-primary/5 to-transparent p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-primary" />
                <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Build a model
                </span>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Predict
                  </label>
                  {allColumns.length > 0 ? (
                    <select
                      value={targetColumn}
                      onChange={(e) => setTargetColumn(e.target.value)}
                      className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
                    >
                      {allColumns.map((col) => (
                        <option key={col} value={col}>
                          {col}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      {workspace.target_column || "Add a data source to choose a column."}
                    </span>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="build-query"
                    className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted-foreground"
                  >
                    How should sources relate / what to optimize for (optional)
                  </label>
                  <textarea
                    id="build-query"
                    value={buildQuery}
                    onChange={(e) => setBuildQuery(e.target.value)}
                    placeholder={
                      schemasLookDifferent
                        ? 'e.g. "join the two files on customer_id, though it\'s called cust_id in the second one" — these sources don\'t share a schema, so this really helps.'
                        : "e.g. what the model should prioritize, or how multiple sources should be combined."
                    }
                    rows={3}
                    className="w-full rounded-md border border-input bg-secondary px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                  />
                  {schemasLookDifferent && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      These sources don't share an identical schema or row count — describing how they relate
                      helps the preprocessing agent merge them correctly instead of guessing.
                    </p>
                  )}
                </div>

                <button
                  onClick={handleBuild}
                  disabled={!canBuild || isBusy}
                  className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary-gradient px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  {isBusy ? (
                    <>
                      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-foreground/40 border-t-primary-foreground" />
                      Building…
                    </>
                  ) : (
                    <>
                      <Rocket className="h-3.5 w-3.5" />
                      Build models
                    </>
                  )}
                </button>
                {buildError && <p className="text-sm text-destructive">{buildError}</p>}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* ————————————————————— Drag handle ————————————————————— */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize left panel"
        tabIndex={0}
        onMouseDown={handleDragStart}
        onKeyDown={handleDragHandleKeyDown}
        onDoubleClick={() => setLeftWidth(LEFT_PANEL_DEFAULT)}
        title="Drag to resize (double-click to reset)"
        className="group relative flex w-2.5 shrink-0 cursor-col-resize select-none items-center justify-center"
      >
        <div
          className={`h-full w-px transition-colors ${isDragging ? "bg-primary/60" : "bg-border group-hover:bg-primary/40"}`}
        />
        <div
          className={`absolute flex h-8 w-4 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-opacity ${
            isDragging ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
        >
          <GripVertical className="h-3 w-3" />
        </div>
      </div>

      {/* ————————————————————— RIGHT PANEL: system output only ————————————————————— */}
      <main className="relative flex-1 overflow-y-auto">
        {/* Sticky (NOT fixed) results header — same reasoning as the left
            panel's header: scoped to this main's own scroll container. */}
        <div className="sticky top-0 z-10 border-b border-border/70 bg-background/95 px-8 py-4 backdrop-blur">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Results</h2>
              <p className="text-xs text-muted-foreground">
                Everything below is generated by the pipeline — nothing here is editable.
              </p>
            </div>
          </div>
        </div>

        <div className="mx-auto max-w-3xl space-y-6 px-8 py-8">
          {preprocessingSummary && Object.keys(preprocessingSummary).length > 0 && (
            <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
              <StepLabel n="02">Preprocessed data</StepLabel>
              <div className="mt-3">
                <SummaryKeyValueList data={preprocessingSummary} />
              </div>
            </section>
          )}

          {modelComparison.length > 0 || modelSummary ? (
            <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
              <StepLabel n="03">Available models</StepLabel>

              {modelSummary?.overall_assessment && (
                <p className="mb-3 mt-3 text-sm text-muted-foreground">{modelSummary.overall_assessment}</p>
              )}

              {modelComparison.length > 0 ? (
                <div className="mt-3 overflow-hidden rounded-lg border border-border">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-secondary font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 font-medium">Model</th>
                        <th className="px-3 py-2 font-medium">Metric</th>
                        <th className="px-3 py-2 text-right font-medium">Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {modelComparison.map((m, i) => {
                        const isBest = m.model_key === bestModel;
                        return (
                          <tr
                            key={`${m.model_key}-${m.metric_name}-${i}`}
                            className={`border-t border-border transition-colors ${
                              isBest ? "relative bg-primary/10" : "hover:bg-secondary/40"
                            }`}
                          >
                            <td className="relative px-3 py-2 font-medium text-foreground">
                              {isBest && (
                                <span className="absolute inset-y-0 left-0 w-0.5 bg-primary-gradient" aria-hidden />
                              )}
                              <span className="inline-flex items-center gap-2">
                                {isBest && <Trophy className="h-3.5 w-3.5 text-primary" />}
                                {m.model_key}
                                {isBest && (
                                  <span className="rounded bg-primary-gradient px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                                    Best
                                  </span>
                                )}
                              </span>
                            </td>
                            <td className="px-3 py-2 font-mono text-muted-foreground">{m.metric_name}</td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-foreground">
                              {formatPercent(m.mean)}
                              {m.std != null && <span className="text-muted-foreground"> ± {formatPercent(m.std)}</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">No comparable metrics were returned for this run.</p>
              )}

              {modelSummary?.best_model_rationale && (
                <p className="mt-3 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Why {bestModel}: </span>
                  {modelSummary.best_model_rationale}
                </p>
              )}

              {modelSummary?.recommendations && modelSummary.recommendations.length > 0 && (
                <div className="mt-4">
                  <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                    Recommendations
                  </h4>
                  <ul className="list-inside list-disc space-y-0.5 text-sm text-muted-foreground">
                    {modelSummary.recommendations.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

              {modelFiles.length > 0 && (
                <div className="mt-5">
                  <h4 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                    Download models
                  </h4>
                  <ul className="space-y-1.5">
                    {modelFiles.map((mf) => {
                      const isBest = mf.model_key === bestModel;
                      return (
                        <li
                          key={mf.file_url}
                          className={`flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm ${
                            isBest ? "border-primary/30 bg-primary/10" : "border-border"
                          }`}
                        >
                          <span className="flex items-center gap-2 font-mono text-foreground">
                            {mf.model_key}
                            {isBest && (
                              <span className="rounded bg-primary-gradient px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                                Best
                              </span>
                            )}
                          </span>
                          <button
                            onClick={() => handleDownload(mf.model_key, mf.file_url)}
                            disabled={downloadingUrl === mf.file_url}
                            className="flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40"
                          >
                            <Download className="h-3 w-3" />
                            {downloadingUrl === mf.file_url ? "Downloading…" : "Download"}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {downloadError && <p className="mt-1.5 text-xs text-destructive">{downloadError}</p>}
                </div>
              )}
            </section>
          ) : (
            <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border text-center">
              <Sparkles className="h-5 w-5 text-muted-foreground/60" />
              <p className="text-sm text-muted-foreground">
                Build results will appear here once you run "Build models" on the left.
              </p>
            </div>
          )}
        </div>
      </main>

      {/* Delete confirmation */}
      {showDeleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget && !deleting) setShowDeleteConfirm(false);
          }}
        >
          <div className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-lg">
            <h2 className="text-sm font-semibold text-foreground">Delete "{workspace.name}"?</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              This permanently deletes the workspace, every uploaded file, and every model it built. This can't
              be undone.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={deleting}
                className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="rounded-md bg-destructive px-3 py-1.5 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {deleting ? "Deleting…" : "Delete workspace"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal 1: pick a connector */}
      {showConnectorListModal && (
        <ConnectorListModal
          connectors={availableConnectors}
          loading={connectorsLoading}
          error={connectorError}
          onClose={() => setShowConnectorListModal(false)}
          onSelect={openTableSelectModalForAdd}
        />
      )}

      {/* Modal 2: pick which tables to attach (table-only, no columns) */}
      {showTableSelectModal && pendingConnector && (
        <TableSelectModal
          connectorName={pendingConnector.name}
          loading={tablesLoading}
          error={tablesError}
          previews={tablePreviews}
          selectedTables={selectedTables}
          alreadyAttached={new Set(
            sources
              .filter((s) => s.kind === "connector" && s.connector_id === pendingConnector.connector_id)
              .map((s) => s.name)
          )}
          saving={saving}
          onToggleTable={toggleTableSelected}
          onBack={() => {
            closeTableSelectModal();
            openConnectorListModal();
          }}
          onClose={closeTableSelectModal}
          onSave={handleSaveTableSelection}
        />
      )}

      {/* Modal 3: edit columns for a single already-attached table */}
      {columnEditTarget && (
        <ColumnEditModal
          subtitle={columnEditTarget.connectorName}
          table={columnEditTarget.table}
          loading={columnEditLoading}
          error={columnEditError}
          saving={columnEditSaving}
          preview={columnEditPreview}
          initialSelected={
            new Set(
              sources.find(
                (s) =>
                  s.kind === "connector" &&
                  s.connector_id === columnEditTarget.connectorId &&
                  s.name === columnEditTarget.table
              )?.columns ?? columnEditPreview?.columns ?? []
            )
          }
          onClose={closeColumnEditor}
          onSave={handleSaveColumnEdit}
        />
      )}

      {/* Modal 3b: edit columns for a single already-attached upload source */}
      {uploadColumnEditTarget && (
        <ColumnEditModal
          subtitle={uploadColumnEditTarget.sourceName}
          table={uploadColumnEditTarget.sourceName}
          loading={uploadColumnEditLoading}
          error={uploadColumnEditError}
          saving={uploadColumnEditSaving}
          preview={uploadColumnEditPreview}
          initialSelected={
            new Set(
              sources.find((s) => s.source_id === uploadColumnEditTarget.sourceId)?.columns ??
                uploadColumnEditPreview?.columns ??
                []
            )
          }
          onClose={closeUploadColumnEditor}
          onSave={handleSaveUploadColumnEdit}
        />
      )}
    </div>
  );
}

// ————————————————————————————————————————————————————————————
// Sub-components

function SourceRow({
  source,
  columns,
  preview,
  onRemove,
  onEditColumns,
  removing,
}: {
  source: DataSource;
  columns: string[];
  preview?: Record<string, unknown>[];
  onRemove: () => void;
  onEditColumns: () => void;
  removing: boolean;
}): JSX.Element {
  const allColumnsCount = source.all_columns?.length ?? columns.length;   // ← add
  const selectedCount = columns.length;                                   // ← add

  return (
    <li className="rounded-lg border border-border px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {(source.file_type ?? "file").toUpperCase()}
          </span>
          <span className="truncate font-mono text-foreground">{source.name}</span>
          {typeof source.row_count === "number" && (
            <span className="shrink-0 text-xs text-muted-foreground">· {source.row_count} rows</span>
          )}
          {selectedCount > 0 && selectedCount < allColumnsCount && (
            <span className="shrink-0 text-xs text-muted-foreground">
              · {selectedCount}/{allColumnsCount} cols
            </span>
          )}
        </div>
        <span className="flex shrink-0 items-center gap-2">
          <button
            onClick={onEditColumns}
            className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
          >
            <Pencil className="h-3 w-3" />
            Columns
          </button>
          <button
            onClick={onRemove}
            disabled={removing}
            className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
          >
            {removing ? "Removing…" : "Remove"}
          </button>
        </span>
      </div>

      {preview && preview.length > 0 && (
        <div className="mt-2 max-h-32 overflow-auto rounded-md border border-border scrollbar-thin">
          <table className="min-w-full text-left font-mono text-xs">
            <thead className="bg-secondary text-muted-foreground">
              <tr>
                {columns.map((col) => (
                  <th key={col} className="whitespace-nowrap px-2 py-1 font-medium">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.slice(0, 5).map((row, i) => (
                <tr key={i} className="border-t border-border">
                  {columns.map((col) => (
                    <td key={col} className="whitespace-nowrap px-2 py-1 text-foreground">
                      {String(row[col] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </li>
  );
}

function ConnectorListModal({
  connectors,
  loading,
  error,
  onClose,
  onSelect,
}: {
  connectors: DataConnection[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onSelect: (conn: DataConnection) => void;
}): JSX.Element {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">Connect a data source</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading && <p className="text-sm text-muted-foreground">Loading connectors…</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {!loading && !error && connectors.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No available connectors. Add one on the Data Connectors page first.
          </p>
        )}

        <ul className="space-y-2">
          {connectors.map((conn) => (
            <li
              key={conn.connector_id}
              className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{conn.name}</p>
                <p className="text-xs text-muted-foreground">{conn.type}</p>
              </div>
              <button
                onClick={() => onSelect(conn)}
                className="shrink-0 rounded-md bg-primary-gradient px-2.5 py-1 text-xs font-medium text-primary-foreground"
              >
                Choose
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * Step 1 of connector attachment: choose *which tables* to bring in. No
 * column selection here — that happens later, per table, via
 * ColumnEditModal. Supports a name filter and select-all/none for
 * connectors with many tables, and clicking a row (outside the checkbox)
 * opens a read-only preview so the user can confirm they've got the right
 * table before attaching it.
 */
function TableSelectModal({
  connectorName,
  loading,
  error,
  previews,
  selectedTables,
  alreadyAttached,
  saving,
  onToggleTable,
  onBack,
  onClose,
  onSave,
}: {
  connectorName: string;
  loading: boolean;
  error: string | null;
  previews: TablePreviewEntry[];
  selectedTables: Set<string>;
  alreadyAttached: Set<string>;
  saving: boolean;
  onToggleTable: (table: string) => void;
  onBack: () => void;
  onClose: () => void;
  onSave: () => void;
}): JSX.Element {
  const [filter, setFilter] = useState("");
  const [previewTable, setPreviewTable] = useState<TablePreviewEntry | null>(null);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return previews;
    return previews.filter((t) => t.table.toLowerCase().includes(q));
  }, [previews, filter]);

  const selectableTables = filtered.filter((t) => !t.error && !alreadyAttached.has(t.table));
  const allFilteredSelected =
    selectableTables.length > 0 && selectableTables.every((t) => selectedTables.has(t.table));

  const toggleAll = () => {
    selectableTables.forEach((t) => {
      const isSelected = selectedTables.has(t.table);
      if (allFilteredSelected && isSelected) onToggleTable(t.table);
      if (!allFilteredSelected && !isSelected) onToggleTable(t.table);
    });
  };

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div className="flex w-full max-w-lg flex-col rounded-lg border border-border bg-card p-5 shadow-lg">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">Add tables from {connectorName}</h2>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </div>
          <p className="mb-3 text-xs text-muted-foreground">
            Pick the tables to attach — every column comes along by default. You can narrow columns per table
            afterwards from the left panel.
          </p>

          {loading && <p className="text-xs text-muted-foreground">Loading tables…</p>}
          {error && <p className="text-xs text-destructive">{error}</p>}

          {!loading && !error && previews.length === 0 && (
            <p className="text-xs text-muted-foreground">No tables found for this connector.</p>
          )}

          {!loading && previews.length > 0 && (
            <>
              <div className="mb-2 flex items-center gap-2">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Filter tables…"
                    className="w-full rounded-md border border-input bg-secondary py-1.5 pl-7 pr-2 text-xs text-foreground outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <button
                  onClick={toggleAll}
                  disabled={selectableTables.length === 0}
                  className="shrink-0 rounded-md border border-border px-2 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40"
                >
                  {allFilteredSelected ? "Deselect all" : "Select all"}
                </button>
              </div>

              <div className="max-h-80 space-y-1 overflow-y-auto pr-1">
                {filtered.map((t) => {
                  const attached = alreadyAttached.has(t.table);
                  const isSelected = selectedTables.has(t.table);
                  return (
                    <div
                      key={t.table}
                      className={`flex items-center justify-between gap-2 rounded-md border px-2.5 py-2 text-sm transition-colors ${
                        attached
                          ? "border-border/60 bg-secondary/40 text-muted-foreground"
                          : isSelected
                          ? "border-primary/40 bg-primary/5"
                          : "border-border hover:bg-secondary/40"
                      }`}
                    >
                      <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          disabled={attached || Boolean(t.error)}
                          onChange={() => onToggleTable(t.table)}
                          className="shrink-0"
                        />
                        <span className="truncate font-mono">{t.table}</span>
                        {attached && (
                          <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                            Already added
                          </span>
                        )}
                      </label>
                      {t.error ? (
                        <span className="shrink-0 text-xs text-destructive">{t.error}</span>
                      ) : (
                        <button
                          onClick={() => setPreviewTable(t)}
                          className="shrink-0 text-xs font-medium text-primary hover:underline"
                        >
                          Preview
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}

          <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-3">
            <button onClick={onBack} className="text-xs font-medium text-muted-foreground hover:text-foreground">
              ← Back
            </button>
            <button
              onClick={onSave}
              disabled={selectedTables.size === 0 || saving}
              className="rounded-md bg-primary-gradient px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              {saving
                ? "Attaching…"
                : `Attach ${selectedTables.size || ""} table${selectedTables.size === 1 ? "" : "s"}`.trim()}
            </button>
          </div>
        </div>
      </div>

      {previewTable && <TablePreviewOnlyModal entry={previewTable} onClose={() => setPreviewTable(null)} />}
    </>
  );
}

/** Read-only preview of a table's shape, used from TableSelectModal. */
function TablePreviewOnlyModal({ entry, onClose }: { entry: TablePreviewEntry; onClose: () => void }): JSX.Element {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-2xl rounded-lg border border-border bg-card p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-foreground">{entry.table}</h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-80 overflow-auto rounded-md border border-border">
          <table className="min-w-full text-left font-mono text-xs">
            <thead className="sticky top-0 bg-secondary text-muted-foreground">
              <tr>
                {entry.columns.map((c) => (
                  <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entry.preview.map((row, i) => (
                <tr key={i} className="border-t border-border">
                  {entry.columns.map((c) => (
                    <td key={c} className="whitespace-nowrap px-3 py-2 text-foreground">
                      {String(row[c] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">Showing first {entry.preview.length} rows</p>
      </div>
    </div>
  );
}

/**
 * Step 2, per table: a focused column picker for one already-attached
 * table. Search, select-all/none, and a live preview whose columns dim
 * out as they're deselected so the effect of the choice is visible
 * immediately, rather than just toggling checkboxes against a flat list.
 */
function ColumnEditModal({
  subtitle,          // was: connectorName
  table,
  loading,
  error,
  saving,
  preview,
  initialSelected,
  onClose,
  onSave,
}: {
  subtitle: string;  // was: connectorName: string;
  table: string;
  loading: boolean;
  error: string | null;
  saving: boolean;
  preview: TablePreviewEntry | null;
  initialSelected: Set<string>;
  onClose: () => void;
  onSave: (columns: Set<string>) => void;
}): JSX.Element {
  const [selected, setSelected] = useState<Set<string>>(initialSelected);
  const [filter, setFilter] = useState("");
  const initializedFor = useRef<string | null>(null);

  // Seed local selection once the preview (and therefore the full column
  // list) has loaded, without clobbering the user's in-progress edits on
  // re-renders.
  useEffect(() => {
    if (preview && initializedFor.current !== preview.table) {
      setSelected(new Set(initialSelected.size > 0 ? initialSelected : preview.columns));
      initializedFor.current = preview.table;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview]);

  const allColumns = preview?.columns ?? [];
  const filteredColumns = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return allColumns;
    return allColumns.filter((c) => c.toLowerCase().includes(q));
  }, [allColumns, filter]);

  const toggle = (col: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(col)) {
        if (next.size === 1) return prev; // keep at least one column
        next.delete(col);
      } else {
        next.add(col);
      }
      return next;
    });
  };

  const allFilteredSelected = filteredColumns.length > 0 && filteredColumns.every((c) => selected.has(c));
  const toggleAllFiltered = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        filteredColumns.forEach((c) => {
          if (next.size > 1) next.delete(c);
        });
      } else {
        filteredColumns.forEach((c) => next.add(c));
      }
      return next;
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex w-full max-w-2xl flex-col rounded-lg border border-border bg-card p-5 shadow-xl">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">
            Columns for <span className="font-mono">{table}</span>
          </h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">{subtitle}</p>

        {loading && <p className="text-xs text-muted-foreground">Loading columns…</p>}
        {error && <p className="text-xs text-destructive">{error}</p>}

        {!loading && !error && preview && (
          <>
            <div className="mb-2 flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter columns…"
                  className="w-full rounded-md border border-input bg-secondary py-1.5 pl-7 pr-2 text-xs text-foreground outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <button
                onClick={toggleAllFiltered}
                className="shrink-0 rounded-md border border-border px-2 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
              >
                {allFilteredSelected ? "Deselect all" : "Select all"}
              </button>
              <span className="shrink-0 text-xs text-muted-foreground">
                {selected.size}/{allColumns.length}
              </span>
            </div>

            <div className="mb-3 flex max-h-40 flex-wrap content-start gap-1.5 overflow-y-auto rounded-md border border-border p-2">
              {filteredColumns.map((col) => {
                const isOn = selected.has(col);
                return (
                  <button
                    key={col}
                    onClick={() => toggle(col)}
                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 font-mono text-[11px] transition-colors ${
                      isOn
                        ? "border-primary/50 bg-primary/10 text-foreground"
                        : "border-border text-muted-foreground hover:bg-secondary"
                    }`}
                  >
                    {isOn && <Check className="h-3 w-3" />}
                    {col}
                  </button>
                );
              })}
              {filteredColumns.length === 0 && (
                <span className="text-xs text-muted-foreground">No columns match "{filter}".</span>
              )}
            </div>

            {/* Live preview — deselected columns are dimmed rather than
                removed, so the effect of a toggle is immediately visible
                against real data. */}
            {preview.preview.length > 0 && (
              <div className="max-h-48 overflow-auto rounded-md border border-border">
                <table className="min-w-full text-left font-mono text-xs">
                  <thead className="sticky top-0 bg-secondary text-muted-foreground">
                    <tr>
                      {allColumns.map((c) => (
                        <th
                          key={c}
                          className={`whitespace-nowrap px-2.5 py-1.5 font-medium transition-opacity ${
                            selected.has(c) ? "" : "opacity-30"
                          }`}
                        >
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.preview.slice(0, 5).map((row, i) => (
                      <tr key={i} className="border-t border-border">
                        {allColumns.map((c) => (
                          <td
                            key={c}
                            className={`whitespace-nowrap px-2.5 py-1.5 text-foreground transition-opacity ${
                              selected.has(c) ? "" : "opacity-30"
                            }`}
                          >
                            {String(row[c] ?? "")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        <div className="mt-4 flex justify-end gap-2 border-t border-border/60 pt-3">
          <button
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
          >
            Cancel
          </button>
          <button
            onClick={() => onSave(selected)}
            disabled={saving || loading || !preview || selected.size === 0}
            className="rounded-md bg-primary-gradient px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save columns"}
          </button>
        </div>
      </div>
    </div>
  );
}
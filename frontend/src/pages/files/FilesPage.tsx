import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FileText,
  Search,
  FolderOpen,
  Calendar,
  Tag,
  X,
  Upload,
  Loader2,
  Pencil,
  Check,
  Download,
  Trash2,
  FileSpreadsheet,
  FileJson,
  FileImage,
  FileCode2,
  LayoutGrid,
  List as ListIcon,
  ArrowUpDown,
  CheckSquare,
  Square,
  Sparkles,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import DotField from '@/components/background/DotField';
import { API_BASE, apiFetch } from '@/pages/workspace/shared';
import { authHeaders } from '@/lib/auth';

type ListedFile = {
  fileUrl: string;
  fileName: string;
  fileType?: string | null;
  size?: number | null;
  uploadedAt?: string | null;
  workspaceId?: string | null;
  sourceId?: string | null;
  notebookId?: string | null;
};

type FilesListResponse = {
  success: boolean;
  files: ListedFile[];
};

type FileEntry = {
  key: string;
  fileName: string;
  fileUrl: string;
  fileType?: string | null;
  size?: number | null;
  updatedAt: string; // ISO string used for sorting/display
  workspaceId?: string | null;
  sourceId?: string | null;
  notebookId?: string | null;
};

type SourcePreview = {
  table: string;
  columns: string[];
  preview: Record<string, unknown>[];
};

type ViewMode = 'grid' | 'list';
type SortBy = 'recent' | 'name' | 'size';
type FilterChip = 'all' | 'connected' | 'standalone';
type ToastKind = 'success' | 'error';
type ToastItem = { id: string; kind: ToastKind; text: string };

// A pending delete confirmation — either one card's delete button, or the
// bulk-select toolbar's Delete action. `bulk` controls copy + which entries
// get filtered out as "attached, can't delete" inside the dialog itself.
type PendingDelete = { entries: FileEntry[]; bulk: boolean };

// ——————————————————————————————————————————————————————————————
// Retrieval helper
//
// Every place that needs the *actual bytes* of an uploaded file goes
// through POST /api/files/retrieve, never the raw stored fileUrl directly.
// fileUrl points at internal storage (e.g. an s3:// URI / MinIO) and is
// NOT reachable from the browser — navigating to it directly produces
// "the scheme does not have a registered handler" (for s3://) or
// net::ERR_CONNECTION_REFUSED (for other internal-only hosts).
//
// Confirmed in practice: /files/retrieve's outputFiles[key].fileUrl is
// just the internal address, unusable by the browser. The actual file
// content comes back in outputFiles[key].fileByte instead — sometimes
// base64-encoded bytes, sometimes (for text files) the decoded text
// directly, despite the field name implying raw bytes either way.
// downloadFile() below consumes fileByte via a Blob, not fileUrl.
type RetrievedFile = {
  fileUrl: string;
  fileName: string;
  fileType?: string | null;
  fileByte?: string | null;
  size?: number | null;
};

type FileRetrieveResponse = {
  success: boolean;
  outputFiles: Record<string, RetrievedFile> | null;
};

async function retrieveFile(fileUrl: string, fileName: string): Promise<RetrievedFile | null> {
  const response = await apiFetch<FileRetrieveResponse>('/file/retrieve', {
    method: 'POST',
    body: JSON.stringify({ file: [{ fileUrl, fileName }] }),
  });
  if (!response.success || !response.outputFiles) return null;
  return response.outputFiles[fileName] ?? Object.values(response.outputFiles)[0] ?? null;
}

// Best-effort base64 decode. A genuine base64 string only contains
// [A-Za-z0-9+/=] (plus optional whitespace), so atob() throws immediately
// on anything else (commas, \r\n, etc.) — used here as a cheap heuristic
// to distinguish "fileByte is base64-encoded bytes" from "fileByte is
// already plain decoded text".
function base64ToBlob(base64: string, mime: string): Blob {
  const byteChars = atob(base64);
  const byteNumbers = new Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) {
    byteNumbers[i] = byteChars.charCodeAt(i);
  }
  return new Blob([new Uint8Array(byteNumbers)], { type: mime });
}

function downloadBlob(blob: Blob, fileName: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function formatBytes(bytes?: number | null): string {
  if (bytes == null || Number.isNaN(bytes)) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, i);
  return `${i === 0 || value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

function formatUpdatedDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ——————————————————————————————————————————————————————————————
// File-type presentation — a small, deliberately non-exhaustive map.
// Unknown extensions fall back to a neutral badge rather than guessing.
//
// `accentClass` is a *solid* color (vs. the translucent `chipClass`) used
// everywhere the UI leans into the "this is a physical file" metaphor: the
// folded corner and paper-stack edges on grid cards, and the spine bar on
// list rows. Keeping it keyed off file type means someone scanning a folder
// of mixed uploads can tell CSVs from PDFs from images at a glance, before
// ever reading a label — the color is doing real information work, not
// just decoration.
type FileTypeMeta = {
  label: string;
  colorClass: string;
  chipClass: string;
  accentClass: string;
  Icon: React.ElementType;
};

const FILE_TYPE_MAP: Record<string, FileTypeMeta> = {
  csv: { label: 'CSV', colorClass: 'text-emerald-600 dark:text-emerald-400', chipClass: 'bg-emerald-500/10 border-emerald-500/30', accentClass: 'bg-emerald-500', Icon: FileSpreadsheet },
  tsv: { label: 'TSV', colorClass: 'text-emerald-600 dark:text-emerald-400', chipClass: 'bg-emerald-500/10 border-emerald-500/30', accentClass: 'bg-emerald-500', Icon: FileSpreadsheet },
  xlsx: { label: 'Excel', colorClass: 'text-teal-600 dark:text-teal-400', chipClass: 'bg-teal-500/10 border-teal-500/30', accentClass: 'bg-teal-500', Icon: FileSpreadsheet },
  xls: { label: 'Excel', colorClass: 'text-teal-600 dark:text-teal-400', chipClass: 'bg-teal-500/10 border-teal-500/30', accentClass: 'bg-teal-500', Icon: FileSpreadsheet },
  json: { label: 'JSON', colorClass: 'text-sky-600 dark:text-sky-400', chipClass: 'bg-sky-500/10 border-sky-500/30', accentClass: 'bg-sky-500', Icon: FileJson },
  pdf: { label: 'PDF', colorClass: 'text-rose-600 dark:text-rose-400', chipClass: 'bg-rose-500/10 border-rose-500/30', accentClass: 'bg-rose-500', Icon: FileText },
  png: { label: 'Image', colorClass: 'text-violet-600 dark:text-violet-400', chipClass: 'bg-violet-500/10 border-violet-500/30', accentClass: 'bg-violet-500', Icon: FileImage },
  jpg: { label: 'Image', colorClass: 'text-violet-600 dark:text-violet-400', chipClass: 'bg-violet-500/10 border-violet-500/30', accentClass: 'bg-violet-500', Icon: FileImage },
  jpeg: { label: 'Image', colorClass: 'text-violet-600 dark:text-violet-400', chipClass: 'bg-violet-500/10 border-violet-500/30', accentClass: 'bg-violet-500', Icon: FileImage },
  gif: { label: 'Image', colorClass: 'text-violet-600 dark:text-violet-400', chipClass: 'bg-violet-500/10 border-violet-500/30', accentClass: 'bg-violet-500', Icon: FileImage },
  webp: { label: 'Image', colorClass: 'text-violet-600 dark:text-violet-400', chipClass: 'bg-violet-500/10 border-violet-500/30', accentClass: 'bg-violet-500', Icon: FileImage },
  txt: { label: 'Text', colorClass: 'text-slate-600 dark:text-slate-400', chipClass: 'bg-slate-500/10 border-slate-500/30', accentClass: 'bg-slate-400', Icon: FileText },
  md: { label: 'Markdown', colorClass: 'text-slate-600 dark:text-slate-400', chipClass: 'bg-slate-500/10 border-slate-500/30', accentClass: 'bg-slate-400', Icon: FileText },
  py: { label: 'Python', colorClass: 'text-amber-600 dark:text-amber-400', chipClass: 'bg-amber-500/10 border-amber-500/30', accentClass: 'bg-amber-500', Icon: FileCode2 },
};

const DEFAULT_TYPE_META: Omit<FileTypeMeta, 'label'> = {
  colorClass: 'text-muted-foreground',
  chipClass: 'bg-secondary border-border',
  accentClass: 'bg-border',
  Icon: FileText,
};

function getFileTypeMeta(fileType?: string | null, fileName?: string): FileTypeMeta {
  const raw = (fileType || fileName?.split('.').pop() || '').toLowerCase().trim();
  return (
    FILE_TYPE_MAP[raw] ?? {
      label: raw ? raw.toUpperCase() : 'File',
      ...DEFAULT_TYPE_META,
    }
  );
}

function isHiddenArtifact(fileType?: string | null, fileName?: string): boolean {
  const normalizedType = (fileType ?? '').toLowerCase().trim();
  const normalizedName = (fileName ?? '').toLowerCase().split(/[?#]/, 1)[0];
  return (
    normalizedType === 'json' ||
    normalizedType === 'joblib' ||
    normalizedType.endsWith('/json') ||
    normalizedName.endsWith('.json') ||
    normalizedName.endsWith('.joblib')
  );
}

// ——————————————————————————————————————————————————————————————
// Toasts — transient feedback for page-level actions (upload/download
// failures, bulk-action results) so they don't sit as permanent banners.

function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: string) => void }): JSX.Element {
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[80] flex w-80 flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className={`pointer-events-auto flex items-start gap-2 rounded-lg border px-3.5 py-3 text-sm shadow-lg backdrop-blur transition-all animate-in slide-in-from-bottom-2 fade-in ${
            toast.kind === 'error'
              ? 'border-destructive/30 bg-destructive/10 text-destructive'
              : 'border-primary/30 bg-card text-foreground'
          }`}
        >
          {toast.kind === 'error' ? (
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          ) : (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          )}
          <span className="flex-1 leading-snug">{toast.text}</span>
          <button
            onClick={() => onDismiss(toast.id)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label="Dismiss notification"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

export default function FilesPage(): JSX.Element {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [previewTarget, setPreviewTarget] = useState<FileEntry | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<SourcePreview | null>(null);

  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounter = useRef(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [sortBy, setSortBy] = useState<SortBy>('recent');
  const [filterChip, setFilterChip] = useState<FilterChip>('all');

  const [selectMode, setSelectMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  // Delete confirmation is a real modal now instead of a per-card
  // click-again-to-confirm button, so it works the same way whether it was
  // triggered from a single card or the bulk-select toolbar.
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const [justAddedKeys, setJustAddedKeys] = useState<Set<string>>(new Set());
  const entriesRef = useRef<FileEntry[]>([]);

  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const pushToast = useCallback((kind: ToastKind, text: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setToasts((prev) => [...prev, { id, kind, text }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 5000);
  }, []);
  const dismissToast = (id: string) => setToasts((prev) => prev.filter((t) => t.id !== id));

  useEffect(() => {
    entriesRef.current = entries;
  }, [entries]);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await apiFetch<FilesListResponse>('/file/');

      const mapped: FileEntry[] = (response.files ?? [])
        .filter((file) => !isHiddenArtifact(file.fileType, file.fileName))
        .map((file) => ({
          key: file.fileUrl,
          fileName: file.fileName,
          fileUrl: file.fileUrl,
          fileType: file.fileType ?? null,
          size: file.size ?? undefined,
          updatedAt: file.uploadedAt ?? new Date().toISOString(),
          workspaceId: file.workspaceId ?? null,
          sourceId: file.sourceId ?? null,
          notebookId: file.notebookId ?? null,
        }));

      setEntries(
        mapped.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load files.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // "/" focuses search, Escape backs out of select mode — cheap, expected shortcuts.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isTyping = target && ['INPUT', 'TEXTAREA'].includes(target.tagName);
      if (e.key === '/' && !isTyping) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === 'Escape' && selectMode && !pendingDelete) {
        setSelectMode(false);
        setSelectedKeys(new Set());
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectMode, pendingDelete]);

  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const fileArray = Array.from(files);
      if (fileArray.length === 0) return;

      setUploading(true);
      const beforeUrls = new Set(entriesRef.current.map((e) => e.fileUrl));

      try {
        const formData = new FormData();
        fileArray.forEach((file) => formData.append('files', file));

        const res = await fetch(`${API_BASE}/file/upload-multiple`, {
          method: 'POST',
          body: formData,
          headers: authHeaders(),
        });

        if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new Error(`Upload failed (${res.status}): ${text || res.statusText}`);
        }

        const uploadResponse = await res.json();
        if (!uploadResponse.success) {
          throw new Error('One or more files failed to upload.');
        }

        await loadFiles();

        const newlyAdded = entriesRef.current
          .filter((e) => !beforeUrls.has(e.fileUrl))
          .map((e) => e.key);
        // entriesRef hasn't updated yet post-loadFiles in this closure tick,
        // so read straight off the freshest state via the functional form.
        setEntries((current) => {
          const added = current.filter((e) => !beforeUrls.has(e.fileUrl)).map((e) => e.key);
          if (added.length) {
            setJustAddedKeys(new Set(added));
            window.setTimeout(() => setJustAddedKeys(new Set()), 5000);
            pushToast('success', `Added ${added.length} file${added.length === 1 ? '' : 's'}.`);
          }
          return current;
        });
        void newlyAdded; // kept for readability of intent above
      } catch (err) {
        pushToast('error', err instanceof Error ? err.message : 'Upload failed.');
      } finally {
        setUploading(false);
      }
    },
    [loadFiles, pushToast]
  );

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      uploadFiles(e.target.files);
    }
    e.target.value = '';
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current -= 1;
    if (dragCounter.current <= 0) {
      dragCounter.current = 0;
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounter.current = 0;
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      uploadFiles(e.dataTransfer.files);
    }
  };

  const deleteFile = async (entry: FileEntry): Promise<void> => {
    await apiFetch<{ success: boolean }>(
      `/file/by-url?file_url=${encodeURIComponent(entry.fileUrl)}`,
      { method: 'DELETE' }
    );
    setEntries((prev) => prev.filter((e) => e.key !== entry.key));
    setSelectedKeys((prev) => {
      if (!prev.has(entry.key)) return prev;
      const next = new Set(prev);
      next.delete(entry.key);
      return next;
    });
  };

  const renameFile = async (entry: FileEntry, newName: string): Promise<FileEntry | null> => {
    const trimmed = newName.trim();
    if (!trimmed || trimmed === entry.fileName) return null;
    const updated = await apiFetch<ListedFile>(`/file/by-url?file_url=${encodeURIComponent(entry.fileUrl)}`, {
      method: 'PATCH',
      body: JSON.stringify({ fileName: trimmed }),
    });
    await loadFiles();
    return {
      key: entry.key,
      fileName: updated.fileName,
      fileUrl: updated.fileUrl,
      fileType: updated.fileType ?? null,
      size: entry.size,
      updatedAt: entry.updatedAt,
      workspaceId: updated.workspaceId ?? null,
      sourceId: updated.sourceId ?? null,
      notebookId: updated.notebookId ?? null,
    };
  };

  const filterCounts = useMemo(() => {
    const connected = entries.filter((e) => e.workspaceId || e.notebookId).length;
    return { all: entries.length, connected, standalone: entries.length - connected };
  }, [entries]);

  const visibleEntries = useMemo(() => {
    const q = query.trim().toLowerCase();

    let list = entries.filter((entry) => {
      if (filterChip === 'connected' && !(entry.workspaceId || entry.notebookId)) return false;
      if (filterChip === 'standalone' && (entry.workspaceId || entry.notebookId)) return false;
      if (!q) return true;
      const haystack = [entry.fileName, entry.fileType ?? '', entry.workspaceId ?? '', entry.notebookId ?? '']
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });

    list = [...list].sort((a, b) => {
      if (sortBy === 'name') return a.fileName.localeCompare(b.fileName);
      if (sortBy === 'size') return (b.size ?? 0) - (a.size ?? 0);
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });

    return list;
  }, [entries, query, filterChip, sortBy]);

  const totalFiles = entries.length;
  const workspaceCount = new Set(entries.map((entry) => entry.workspaceId).filter(Boolean)).size;

  function parseCsvPreview(text: string, maxRows = 50): SourcePreview | null {
    const lines = text.split(/\r\n|\n/).filter((l) => l.length > 0);
    if (lines.length === 0) return null;

    const parseLine = (line: string) => line.split(','); // simple CSV, no quoted-comma handling
    const columns = parseLine(lines[0]);
    const preview = lines.slice(1, maxRows + 1).map((line) => {
      const cells = parseLine(line);
      return Object.fromEntries(columns.map((col, i) => [col, cells[i] ?? '']));
    });

    return { table: 'preview', columns, preview };
  }

  const handleViewData = async (entry: FileEntry) => {
    setPreviewTarget(entry);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewData(null);

    try {
      if (entry.notebookId && entry.sourceId) {
        const data = await apiFetch<{
          table_name: string;
          columns: string[];
          rows: Record<string, unknown>[];
        }>(`/notebook/${entry.notebookId}/data-source/preview?source_id=${entry.sourceId}`);
        setPreviewData({ table: data.table_name, columns: data.columns, preview: data.rows });
      } else if (entry.workspaceId && entry.sourceId) {
        const data = await apiFetch<SourcePreview>(
          `/workspace/${entry.workspaceId}/sources/${entry.sourceId}/preview`
        );
        setPreviewData(data);
      } else {
        // No bound source — fall back to retrieving the raw file itself,
        // the same path handleDownload uses, and preview it client-side.
        const retrieved = await retrieveFile(entry.fileUrl, entry.fileName);
        if (!retrieved || !retrieved.fileByte) {
          throw new Error('File could not be retrieved.');
        }

        let text: string;
        try {
          text = atob(retrieved.fileByte);
        } catch {
          text = retrieved.fileByte; // already plain text
        }

        const parsed = parseCsvPreview(text);
        if (!parsed) {
          throw new Error("This file type can't be previewed.");
        }
        setPreviewData(parsed);
      }
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : 'Failed to load preview.');
    } finally {
      setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    setPreviewTarget(null);
    setPreviewData(null);
    setPreviewError(null);
    setPreviewLoading(false);
  };

  // Downloads always go through /api/files/retrieve — never a direct link
  // to entry.fileUrl (see the retrieveFile() comment above for why).
  // The retrieved payload's own fileUrl is also unusable in the browser
  // (it's the internal storage address, e.g. s3://...); the actual
  // content lives in fileByte, which we turn into a Blob and download
  // via an object URL instead.
  const handleDownload = async (entry: FileEntry) => {
    setDownloadingKey(entry.key);
    try {
      const retrieved = await retrieveFile(entry.fileUrl, entry.fileName);
      if (!retrieved) {
        throw new Error('File could not be retrieved.');
      }
      if (!retrieved.fileByte) {
        throw new Error('No file content was returned for this file.');
      }

      const mime = retrieved.fileType || 'application/octet-stream';
      let blob: Blob;
      try {
        blob = base64ToBlob(retrieved.fileByte, mime);
      } catch {
        // Not valid base64 — treat as raw/decoded text content instead.
        blob = new Blob([retrieved.fileByte], { type: retrieved.fileType || 'text/plain' });
      }

      downloadBlob(blob, retrieved.fileName || entry.fileName);
    } catch (err) {
      pushToast('error', `${entry.fileName}: ${err instanceof Error ? err.message : 'Failed to download file.'}`);
    } finally {
      setDownloadingKey(null);
    }
  };

  const toggleSelectMode = () => {
    setSelectMode((prev) => {
      if (prev) setSelectedKeys(new Set());
      return !prev;
    });
  };

  const toggleSelected = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedKeys((prev) => {
      const visibleKeys = visibleEntries.map((e) => e.key);
      const allSelected = visibleKeys.every((k) => prev.has(k));
      if (allSelected) return new Set();
      return new Set(visibleKeys);
    });
  };

  const handleBulkDownload = async () => {
    const targets = entries.filter((e) => selectedKeys.has(e.key));
    if (targets.length === 0) return;
    setBulkBusy(true);
    for (const entry of targets) {
      await handleDownload(entry);
    }
    setBulkBusy(false);
    pushToast('success', `Downloaded ${targets.length} file${targets.length === 1 ? '' : 's'}.`);
  };

  // Delete now always routes through a confirmation dialog — a single card's
  // Delete button opens it for that one file, the toolbar's Delete opens it
  // for the current selection. The dialog itself does the actual deleting
  // once the person confirms (see confirmPendingDelete below).
  const requestDelete = (entry: FileEntry) => {
    if (deleteBusy) return;
    setPendingDelete({ entries: [entry], bulk: false });
  };

  const requestBulkDelete = () => {
    if (deleteBusy) return;
    const selected = entries.filter((e) => selectedKeys.has(e.key));
    if (selected.length === 0) return;
    setPendingDelete({ entries: selected, bulk: true });
  };

  const cancelPendingDelete = () => {
    if (deleteBusy) return;
    setPendingDelete(null);
  };

  const confirmPendingDelete = async () => {
    if (!pendingDelete) return;

    const targets = pendingDelete.bulk
      ? pendingDelete.entries.filter((e) => !(e.workspaceId || e.notebookId))
      : pendingDelete.entries;
    const skipped = pendingDelete.entries.length - targets.length;

    if (targets.length === 0) {
      pushToast('error', "Selected files are attached to a workspace or notebook — they can't be deleted.");
      setPendingDelete(null);
      return;
    }

    setDeleteBusy(true);
    let failed = 0;
    for (const entry of targets) {
      try {
        await deleteFile(entry);
      } catch {
        failed += 1;
      }
    }
    setDeleteBusy(false);
    setPendingDelete(null);
    if (pendingDelete.bulk) setSelectMode(false);

    if (failed === 0) {
      pushToast(
        'success',
        `Deleted ${targets.length} file${targets.length === 1 ? '' : 's'}.${
          skipped ? ` ${skipped} attached file${skipped === 1 ? ' was' : 's were'} skipped.` : ''
        }`
      );
    } else {
      pushToast('error', `Deleted ${targets.length - failed} of ${targets.length} files — ${failed} failed.`);
    }
  };

  const isCardDeleting = (key: string): boolean =>
    deleteBusy && !!pendingDelete && !pendingDelete.bulk && pendingDelete.entries[0]?.key === key;

  const filterChips: { value: FilterChip; label: string; count: number }[] = [
    { value: 'all', label: 'All', count: filterCounts.all },
    { value: 'connected', label: 'Connected', count: filterCounts.connected },
    { value: 'standalone', label: 'Standalone', count: filterCounts.standalone },
  ];

  const resetFilters = () => {
    setQuery('');
    setFilterChip('all');
  };

  return (
    <div
      className="h-full min-h-0 overflow-y-auto bg-background text-foreground"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <div className="relative h-full min-h-0 overflow-y-auto bg-background text-foreground">
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

        <div className="relative mx-auto min-h-full w-full max-w-6xl px-6 py-10 pb-24">
          <header className="relative mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
            <div>
              <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
                Files
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Every file you've uploaded, whether or not it's attached to a workspace or notebook.
              </p>
            </div>

            <div className="flex items-end gap-3">
              <SearchInput ref={searchRef} value={query} onChange={setQuery} />
              <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleFileInputChange} />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                {uploading ? 'Uploading…' : 'Upload files'}
              </button>
            </div>
          </header>

          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <StatCard icon={FileText} label="Uploaded files" value={String(totalFiles)} />
            <StatCard icon={FolderOpen} label="Workspaces" value={String(workspaceCount)} />
            <StatCard icon={Calendar} label="Most recent" value={entries[0] ? formatUpdatedDate(entries[0].updatedAt) : '—'} />
          </div>

          {!loading && !error && entries.length > 0 && (
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                {filterChips.map((chip) => (
                  <button
                    key={chip.value}
                    onClick={() => setFilterChip(chip.value)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                      filterChip === chip.value
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-secondary text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {chip.label} · {chip.count}
                  </button>
                ))}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={toggleSelectMode}
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    selectMode
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border bg-secondary text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {selectMode ? <CheckSquare className="h-3.5 w-3.5" /> : <Square className="h-3.5 w-3.5" />}
                  {selectMode ? 'Selecting' : 'Select'}
                </button>

                <label className="relative">
                  <ArrowUpDown className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                  <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value as SortBy)}
                    className="appearance-none rounded-md border border-input bg-secondary py-1.5 pl-7 pr-3 text-xs font-medium text-foreground outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="recent">Recent</option>
                    <option value="name">Name A–Z</option>
                    <option value="size">Largest first</option>
                  </select>
                </label>

                <div className="flex items-center overflow-hidden rounded-md border border-border">
                  <button
                    onClick={() => setViewMode('grid')}
                    aria-label="Grid view"
                    title="Grid view"
                    className={`p-1.5 transition-colors ${viewMode === 'grid' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground'}`}
                  >
                    <LayoutGrid className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => setViewMode('list')}
                    aria-label="List view"
                    title="List view"
                    className={`p-1.5 transition-colors ${viewMode === 'list' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground'}`}
                  >
                    <ListIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          )}

          {loading && <SkeletonGrid />}
          {error && <p className="text-sm text-destructive">{error}</p>}

          {!loading && !error && entries.length === 0 && (
            <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <Upload className="mb-1 h-6 w-6 animate-bounce text-muted-foreground" />
              <p className="text-sm text-muted-foreground">No uploaded files yet.</p>
              <p className="text-xs text-muted-foreground">Drag and drop files here, or use the Upload button above.</p>
            </div>
          )}

          {!loading && !error && entries.length > 0 && visibleEntries.length === 0 && (
            <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">
                No files match {query ? `"${query}"` : 'the current filter'}.
              </p>
              <button onClick={resetFilters} className="text-xs font-medium text-primary hover:underline">
                Clear filters
              </button>
            </div>
          )}

          {!loading && !error && visibleEntries.length > 0 && (
            viewMode === 'grid' ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {visibleEntries.map((entry) => (
                  <FileCard
                    key={entry.key}
                    entry={entry}
                    layout="grid"
                    isNew={justAddedKeys.has(entry.key)}
                    selectMode={selectMode}
                    isSelected={selectedKeys.has(entry.key)}
                    onToggleSelect={() => toggleSelected(entry.key)}
                    onViewData={() => handleViewData(entry)}
                    onOpenWorkspace={entry.workspaceId ? () => navigate(`/workspace/${entry.workspaceId}`) : undefined}
                    onOpenNotebook={entry.notebookId ? () => navigate(`/notebooks/${entry.notebookId}`) : undefined}
                    onRename={(newName) => renameFile(entry, newName)}
                    onDownload={() => handleDownload(entry)}
                    onRequestDelete={() => requestDelete(entry)}
                    isDownloading={downloadingKey === entry.key}
                    isDeleting={isCardDeleting(entry.key)}
                  />
                ))}
              </div>
            ) : (
              <div className="overflow-hidden rounded-xl border border-border bg-card">
                <div className="flex items-center gap-3 border-b border-border bg-secondary/60 px-4 py-2 text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                  {selectMode && (
                    <button onClick={selectAllVisible} className="shrink-0" aria-label="Select all visible files">
                      {visibleEntries.every((e) => selectedKeys.has(e.key)) ? (
                        <CheckSquare className="h-3.5 w-3.5" />
                      ) : (
                        <Square className="h-3.5 w-3.5" />
                      )}
                    </button>
                  )}
                  <span className="w-6 shrink-0" />
                  <span className="min-w-0 flex-1">Name</span>
                  <span className="hidden w-28 shrink-0 sm:block">Type</span>
                  <span className="hidden w-20 shrink-0 text-right md:block">Size</span>
                  <span className="hidden w-24 shrink-0 text-right lg:block">Updated</span>
                  <span className="w-48 shrink-0 text-right">Actions</span>
                </div>
                <div className="divide-y divide-border">
                  {visibleEntries.map((entry) => (
                    <FileCard
                      key={entry.key}
                      entry={entry}
                      layout="list"
                      isNew={justAddedKeys.has(entry.key)}
                      selectMode={selectMode}
                      isSelected={selectedKeys.has(entry.key)}
                      onToggleSelect={() => toggleSelected(entry.key)}
                      onViewData={() => handleViewData(entry)}
                      onOpenWorkspace={entry.workspaceId ? () => navigate(`/workspace/${entry.workspaceId}`) : undefined}
                      onOpenNotebook={entry.notebookId ? () => navigate(`/notebooks/${entry.notebookId}`) : undefined}
                      onRename={(newName) => renameFile(entry, newName)}
                      onDownload={() => handleDownload(entry)}
                      onRequestDelete={() => requestDelete(entry)}
                      isDownloading={downloadingKey === entry.key}
                      isDeleting={isCardDeleting(entry.key)}
                    />
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      </div>

      {isDragging && (
        <div className="pointer-events-none fixed inset-0 z-[70] flex items-center justify-center border-4 border-dashed border-primary bg-primary/10 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-2 rounded-xl bg-card px-8 py-6 shadow-xl">
            <Upload className="h-8 w-8 animate-bounce text-primary" />
            <p className="text-sm font-medium text-foreground">Drop files to upload</p>
          </div>
        </div>
      )}

      {selectMode && selectedKeys.size > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-[65] flex justify-center px-4">
          <div className="flex items-center gap-3 rounded-full border border-border bg-card px-4 py-2.5 shadow-2xl animate-in slide-in-from-bottom-3 fade-in">
            <span className="text-sm font-medium text-foreground">
              {selectedKeys.size} selected
            </span>
            <div className="h-4 w-px bg-border" />
            <button
              onClick={handleBulkDownload}
              disabled={bulkBusy}
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-50"
            >
              {bulkBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Download
            </button>
            <button
              onClick={requestBulkDelete}
              disabled={deleteBusy}
              className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete
            </button>
            <div className="h-4 w-px bg-border" />
            <button
              onClick={() => {
                setSelectMode(false);
                setSelectedKeys(new Set());
              }}
              className="rounded-full px-3 py-1 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {previewTarget && (
        <PreviewModal
          title={previewTarget.fileName}
          subtitle={
            previewTarget.workspaceId
              ? `Workspace: ${previewTarget.workspaceId}`
              : previewTarget.notebookId
                ? `Notebook: ${previewTarget.notebookId}`
                : 'Not attached to a workspace or notebook'
          }
          loading={previewLoading}
          error={previewError}
          preview={previewData}
          onClose={closePreview}
        />
      )}

      {pendingDelete && (
        <ConfirmDeleteDialog
          entries={pendingDelete.entries}
          bulk={pendingDelete.bulk}
          busy={deleteBusy}
          onCancel={cancelPendingDelete}
          onConfirm={confirmPendingDelete}
        />
      )}

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

const SearchInput = React.forwardRef<HTMLInputElement, { value: string; onChange: (value: string) => void }>(
  ({ value, onChange }, ref) => {
    return (
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Search files…"
          className="w-56 rounded-md border border-input bg-secondary py-1.5 pl-8 pr-7 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
        />
        {value ? (
          <button
            onClick={() => onChange('')}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : (
          <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-border bg-background px-1 text-[10px] text-muted-foreground">
            /
          </kbd>
        )}
      </div>
    );
  }
);
SearchInput.displayName = 'SearchInput';

function StatCard({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }): JSX.Element {
  return (
    <div className="group rounded-xl border border-border bg-card p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4 transition-colors group-hover:text-primary" />
        <span className="text-xs font-medium uppercase tracking-[0.14em]">{label}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold text-foreground">{value}</div>
    </div>
  );
}

function SkeletonGrid(): JSX.Element {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="animate-pulse rounded-2xl border border-border bg-card p-5">
          <div className="h-5 w-24 rounded-full bg-secondary" />
          <div className="mt-3 h-4 w-3/4 rounded bg-secondary" />
          <div className="mt-2 h-3 w-1/2 rounded bg-secondary" />
          <div className="mt-6 h-3 w-full rounded bg-secondary" />
          <div className="mt-5 h-3 w-2/3 rounded bg-secondary" />
        </div>
      ))}
    </div>
  );
}

function TypeBadge({ entry, compact = false }: { entry: FileEntry; compact?: boolean }): JSX.Element {
  const meta = getFileTypeMeta(entry.fileType, entry.fileName);
  const Icon = meta.Icon;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border font-medium ${meta.chipClass} ${meta.colorClass} ${
        compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-[11px] uppercase tracking-[0.1em]'
      }`}
    >
      <Icon className={compact ? 'h-3 w-3' : 'h-3.5 w-3.5'} />
      {meta.label}
    </span>
  );
}

function FileCard({
  entry,
  layout,
  isNew,
  selectMode,
  isSelected,
  onToggleSelect,
  onViewData,
  onOpenWorkspace,
  onOpenNotebook,
  onRename,
  onDownload,
  onRequestDelete,
  isDownloading,
  isDeleting,
}: {
  entry: FileEntry;
  layout: 'grid' | 'list';
  isNew: boolean;
  selectMode: boolean;
  isSelected: boolean;
  onToggleSelect: () => void;
  onViewData: () => void;
  onOpenWorkspace?: () => void;
  onOpenNotebook?: () => void;
  onRename: (newName: string) => Promise<FileEntry | null>;
  onDownload: () => void;
  onRequestDelete: () => void;
  isDownloading: boolean;
  isDeleting: boolean;
}): JSX.Element {
  const parentLabel = entry.workspaceId ? 'In workspace' : entry.notebookId ? 'In notebook' : 'Not attached';
  const parentValue = entry.workspaceId ?? entry.notebookId ?? null;
  const isAttached = Boolean(entry.workspaceId || entry.notebookId);
  const typeMeta = getFileTypeMeta(entry.fileType, entry.fileName);

  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(entry.fileName);
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const startEdit = () => {
    setDraft(entry.fileName);
    setRenameError(null);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setRenameError(null);
  };

  const commitRename = async () => {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === entry.fileName) {
      cancelEdit();
      return;
    }
    setRenaming(true);
    setRenameError(null);
    try {
      await onRename(trimmed);
      setIsEditing(false);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : 'Rename failed.');
    } finally {
      setRenaming(false);
    }
  };

  const ActionsRow = ({ compact }: { compact: boolean }) => (
    <div className={`flex flex-wrap items-center ${compact ? 'gap-2' : 'gap-3'}`}>
      <button
        onClick={onViewData}
        title="View data"
        className={`inline-flex items-center gap-1 font-medium text-primary hover:underline ${compact ? 'text-xs' : 'text-sm'}`}
      >
        {compact ? <FileText className="h-3.5 w-3.5" /> : null}
        {compact ? null : 'View data'}
      </button>
      <button
        onClick={onDownload}
        disabled={isDownloading}
        title="Download"
        className={`inline-flex items-center gap-1 font-medium text-muted-foreground hover:text-foreground hover:underline disabled:opacity-50 ${compact ? 'text-xs' : 'text-sm'}`}
      >
        {isDownloading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
        {compact ? null : 'Download'}
      </button>
      {onOpenWorkspace && !compact && (
        <button onClick={onOpenWorkspace} className="text-sm font-medium text-muted-foreground hover:text-foreground hover:underline">
          Open workspace →
        </button>
      )}
      {onOpenNotebook && !compact && (
        <button onClick={onOpenNotebook} className="text-sm font-medium text-muted-foreground hover:text-foreground hover:underline">
          Open notebook →
        </button>
      )}
      <button
        onClick={onRequestDelete}
        disabled={isAttached || isDeleting}
        title={isAttached ? 'Attached to a workspace or notebook — detach it first to delete.' : 'Delete file'}
        className={`inline-flex items-center gap-1 font-medium text-muted-foreground hover:text-destructive hover:underline disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline ${compact ? 'text-xs' : 'text-sm'}`}
      >
        {isDeleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
        {compact ? null : 'Delete'}
      </button>
    </div>
  );

  if (layout === 'list') {
    return (
      <div
        className={`flex items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-secondary/40 ${
          isNew ? 'bg-primary/5' : ''
        } ${isDeleting ? 'opacity-50' : ''}`}
      >
        {selectMode && (
          <button onClick={onToggleSelect} className="shrink-0 text-muted-foreground hover:text-foreground" aria-label="Select file">
            {isSelected ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4" />}
          </button>
        )}
        {/* Spine bar + type icon — a small "folder tab" cue so a scan down
            the list reads by file type before the label is even parsed. */}
        <span className={`h-6 w-1 shrink-0 rounded-full ${typeMeta.accentClass}`} aria-hidden />
        <typeMeta.Icon className={`h-4 w-4 shrink-0 ${typeMeta.colorClass}`} />

        <div className="min-w-0 flex-1">
          {isEditing ? (
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                commitRename();
              }}
            >
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Escape' && cancelEdit()}
                disabled={renaming}
                className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-0.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
              />
              <button type="submit" disabled={renaming || !draft.trim()} className="shrink-0 p-1 text-muted-foreground hover:text-foreground">
                {renaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5 text-primary" />}
              </button>
              <button type="button" onClick={cancelEdit} disabled={renaming} className="shrink-0 p-1 text-muted-foreground hover:text-foreground">
                <X className="h-3.5 w-3.5" />
              </button>
            </form>
          ) : (
            <div className="group/name flex items-center gap-1.5">
              <span className="truncate font-medium text-foreground">{entry.fileName}</span>
              {isNew && <Sparkles className="h-3 w-3 shrink-0 text-primary" />}
              <button
                onClick={startEdit}
                className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover/name:opacity-100"
                aria-label="Rename file"
              >
                <Pencil className="h-3 w-3" />
              </button>
            </div>
          )}
          {renameError && <p className="mt-0.5 text-xs text-destructive">{renameError}</p>}
          {!isEditing && <p className="truncate text-xs text-muted-foreground">{parentValue ?? parentLabel}</p>}
        </div>

        <div className="hidden w-28 shrink-0 sm:block">
          <TypeBadge entry={entry} compact />
        </div>
        <div className="hidden w-20 shrink-0 text-right text-xs text-muted-foreground md:block">
          {formatBytes(entry.size)}
        </div>
        <div className="hidden w-24 shrink-0 text-right text-xs text-muted-foreground lg:block">
          {formatUpdatedDate(entry.updatedAt)}
        </div>
        <div className="w-48 shrink-0">
          <ActionsRow compact />
        </div>
      </div>
    );
  }

  return (
    <div className={`relative transition-opacity ${isDeleting ? 'opacity-50' : ''}`}>
      {/* Stacked-page edges peeking out bottom-right — a couple of receding
          rectangles behind the card so it reads as a physical file with
          some thickness to it, rather than a flat panel. Purely
          decorative, so it's hidden from assistive tech. */}
      <div aria-hidden className="absolute inset-x-3 -bottom-1.5 h-full rounded-2xl border border-border/50 bg-card/70" />
      <div aria-hidden className="absolute inset-x-1.5 -bottom-3 h-full rounded-2xl border border-border/30 bg-card/40" />

      <div
        className={`group relative z-10 rounded-2xl border bg-card p-5 shadow-sm transition-all duration-200 hover:-translate-y-1 hover:shadow-xl ${
          isNew ? 'border-primary/50 ring-2 ring-primary/20' : 'border-border'
        }`}
      >
        {/* Folded corner — the classic "this is a document" affordance,
            tinted by file type so it doubles as an at-a-glance type cue. */}
        <div aria-hidden className="pointer-events-none absolute right-0 top-0 h-6 w-6 overflow-hidden rounded-tr-2xl">
          <div className={`absolute -right-3 -top-3 h-6 w-6 rotate-45 shadow-sm ${typeMeta.accentClass} opacity-90`} />
        </div>

        {selectMode && (
          <button
            onClick={onToggleSelect}
            className="absolute -left-2 -top-2 z-10 rounded-full border border-border bg-card p-1 shadow-sm"
            aria-label="Select file"
          >
            {isSelected ? <CheckSquare className="h-4 w-4 text-primary" /> : <Square className="h-4 w-4 text-muted-foreground" />}
          </button>
        )}

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1 pr-4">
            <div className="flex items-center gap-1.5">
              <TypeBadge entry={entry} />
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                <Tag className="h-3 w-3" />
                {parentLabel}
              </span>
              {isNew && (
                <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-primary">
                  <Sparkles className="h-3 w-3" />
                  New
                </span>
              )}
            </div>

            {isEditing ? (
              <form
                className="mt-3 flex items-center gap-1.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  commitRename();
                }}
              >
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => e.key === 'Escape' && cancelEdit()}
                  disabled={renaming}
                  className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1 text-sm font-semibold text-foreground outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
                />
                <button
                  type="submit"
                  disabled={renaming || !draft.trim()}
                  className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
                  aria-label="Save rename"
                >
                  {renaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5 text-primary" />}
                </button>
                <button
                  type="button"
                  onClick={cancelEdit}
                  disabled={renaming}
                  className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
                  aria-label="Cancel rename"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </form>
            ) : (
              <div className="mt-3 flex items-center gap-1.5">
                <h2 className="truncate text-sm font-semibold text-foreground">{entry.fileName}</h2>
                <button
                  onClick={startEdit}
                  className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                  aria-label="Rename file"
                  title="Rename"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </div>
            )}

            {renameError && <p className="mt-1 text-xs text-destructive">{renameError}</p>}

            <p className="mt-1 truncate text-sm text-muted-foreground">{parentValue ?? 'Standalone upload'}</p>
          </div>
        </div>

        <div className="mt-4 space-y-2 text-xs text-muted-foreground">
          {parentValue && (
            <div className="flex items-center gap-2">
              <FolderOpen className="h-3.5 w-3.5" />
              <span className="truncate">{parentValue}</span>
            </div>
          )}
          <div className="flex items-center gap-2">
            <Tag className="h-3.5 w-3.5" />
            <span className="truncate">{formatBytes(entry.size)}</span>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-between gap-3">
          <ActionsRow compact={false} />
          <span className="text-xs text-muted-foreground">{formatUpdatedDate(entry.updatedAt)}</span>
        </div>
      </div>
    </div>
  );
}

function PreviewModal({
  title,
  subtitle,
  loading,
  error,
  preview,
  onClose,
}: {
  title: string;
  subtitle: string;
  loading: boolean;
  error: string | null;
  preview: SourcePreview | null;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 animate-in fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-5xl rounded-lg border border-border bg-card p-5 shadow-xl animate-in zoom-in-95 fade-in">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-foreground">{title}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading preview…
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {!loading && !error && preview && (
          <>
            <div className="max-h-[60vh] overflow-auto rounded-md border border-border scrollbar-thin">
              <table className="min-w-full text-left font-mono text-xs">
                <thead className="sticky top-0 bg-secondary text-muted-foreground">
                  <tr>
                    {preview.columns.map((column) => (
                      <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.preview.map((row, index) => (
                    <tr key={index} className="border-t border-border transition-colors hover:bg-secondary/40">
                      {preview.columns.map((column) => (
                        <td key={column} className="whitespace-nowrap px-3 py-2 text-foreground">
                          {String(row[column] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {preview.columns.length} columns · showing first {preview.preview.length} rows
            </p>
          </>
        )}
      </div>
    </div>
  );
}

// ——————————————————————————————————————————————————————————————
// Delete confirmation — a small, focused popup rather than the old
// click-again-on-the-same-button pattern. Works for both a single card's
// Delete action and the bulk-select toolbar's Delete action (`bulk`
// controls the copy and the "some of these can't be deleted" messaging).
function ConfirmDeleteDialog({
  entries,
  bulk,
  busy,
  onCancel,
  onConfirm,
}: {
  entries: FileEntry[];
  bulk: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  const deletable = bulk ? entries.filter((e) => !(e.workspaceId || e.notebookId)) : entries;
  const skipped = entries.length - deletable.length;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCancel();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [busy, onCancel]);

  return (
    <div
      className="fixed inset-0 z-[75] flex items-center justify-center bg-black/60 p-4 animate-in fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-xl animate-in zoom-in-95 fade-in">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <Trash2 className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-foreground">
              {deletable.length === 1 ? 'Delete this file?' : `Delete ${deletable.length} files?`}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">This can't be undone.</p>

            {deletable.length > 0 && (
              <ul className="mt-3 max-h-28 space-y-1 overflow-y-auto rounded-md border border-border bg-secondary/40 px-2.5 py-2 text-xs text-foreground scrollbar-thin">
                {deletable.map((e) => (
                  <li key={e.key} className="truncate">
                    {e.fileName}
                  </li>
                ))}
              </ul>
            )}

            {skipped > 0 && (
              <p className="mt-2 text-xs text-muted-foreground">
                {skipped} attached file{skipped === 1 ? '' : 's'} {skipped === 1 ? "isn't" : "aren't"} included —
                detach {skipped === 1 ? 'it' : 'them'} first to delete.
              </p>
            )}

            {deletable.length === 0 && (
              <p className="mt-2 text-xs text-destructive">
                All selected files are attached to a workspace or notebook — none can be deleted.
              </p>
            )}
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-border bg-secondary px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary/70 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy || deletable.length === 0}
            className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground transition-colors hover:bg-destructive/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            {busy ? 'Deleting…' : deletable.length === 1 ? 'Delete file' : `Delete ${deletable.length} files`}
          </button>
        </div>
      </div>
    </div>
  );
}
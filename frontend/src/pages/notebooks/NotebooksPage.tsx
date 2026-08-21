import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

// Components
import DotField from "@/components/background/DotField";
import { FolderCard } from '@/components/common/FolderCard';

// Types
import type { Notebook } from "@/types";

// API handler
import { apiFetch, statusMeta } from "./shared";

/**
 * Notebooks Page
 * ==============
 */
export default function NotebooksPage(): JSX.Element {
  const navigate = useNavigate();
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  // Notebook currently targeted by rename / delete / duplicate.
  const [renameTarget, setRenameTarget] = useState<Notebook | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Notebook | null>(null);
  const [duplicatingId, setDuplicatingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refreshNotebooks = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await apiFetch<Notebook[]>("/notebook");
      setNotebooks(data);
    } catch (err) {
      console.log(err)
      setListError(err instanceof Error ? err.message : "Failed to load notebooks.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshNotebooks();
  }, [refreshNotebooks]);

  const handleCreated = (nb: Notebook) => {
    setIsCreateOpen(false);
    navigate(`/notebooks/${nb.id}`);
  };

  const handleRenamed = (nb: Notebook) => {
    setNotebooks((prev) => prev.map((n) => (n.id === nb.id ? nb : n)));
    setRenameTarget(null);
  };

  const handleDuplicate = async (notebook: Notebook) => {
    setDuplicatingId(notebook.id);
    setActionError(null);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebook.id}/duplicate`, {
        method: "POST",
      });
      setNotebooks((prev) => [nb, ...prev]);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to duplicate notebook.");
    } finally {
      setDuplicatingId(null);
    }
  };

  const performDelete = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    setDeletingId(target.id);
    setActionError(null);
    try {
      await apiFetch<void>(`/notebook/${target.id}`, { method: "DELETE" });
      setNotebooks((prev) => prev.filter((n) => n.id !== target.id));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to delete notebook.");
    } finally {
      setDeletingId(null);
    }
  };

  const filtered = notebooks.filter((nb) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return nb.name.toLowerCase().includes(q) || (nb.description || "").toLowerCase().includes(q);
  });

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
      <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
        <div className="pointer-events-none absolute inset-0 overflow-hidden ">
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
          <header className="relative mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
            <div>
              <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
                Notebooks
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Connect a dataset, then explore it or ask questions in plain English.
              </p>
            </div>

            <div className="flex items-end gap-3">
              <SearchInput value={query} onChange={setQuery} />
              <button
                onClick={() => setIsCreateOpen(true)}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
                New notebook
              </button>
            </div>
          </header>
          {loading && <p className="text-sm text-muted-foreground">Loading notebooks…</p>}
          {listError && <p className="text-sm text-destructive">{listError}</p>}
          {actionError && <p className="mb-4 text-sm text-destructive">{actionError}</p>}

          {!loading && !listError && notebooks.length === 0 && (
            <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">
                No notebooks yet — create one to start exploring a dataset.
              </p>
            </div>
          )}

          {!loading && !listError && notebooks.length > 0 && filtered.length === 0 && (
            <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">No notebooks match “{query}”.</p>
            </div>
          )}

          <div className="relative grid grid-cols-1 gap-x-6 gap-y-9 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((nb) => {
              const meta = statusMeta(nb.status);
              return (
                <FolderCard
                  key={nb.id}
                  title={nb.name}
                  statusDotClass={meta.tabClass}
                  footerText={`Last Update: ${formatUpdatedDate(nb.updated_at)}`}
                  onOpen={() => navigate(`/notebooks/${nb.id}`)}
                  busy={duplicatingId === nb.id || deletingId === nb.id}
                  busyLabel={duplicatingId === nb.id ? 'Duplicating…' : 'Deleting…'}
                  menuItems={[
                    { label: 'Rename', onClick: () => setRenameTarget(nb) },
                    { label: 'Duplicate', onClick: () => handleDuplicate(nb) },
                    { label: 'Delete', onClick: () => setDeleteTarget(nb), destructive: true },
                  ]}
                />
              );
            })}
          </div>

          {isCreateOpen && (
            <CreateNotebookModal onClose={() => setIsCreateOpen(false)} onCreated={handleCreated} />
          )}

          {renameTarget && (
            <RenameNotebookModal
              notebook={renameTarget}
              onClose={() => setRenameTarget(null)}
              onRenamed={handleRenamed}
            />
          )}

          <ConfirmDialog
            open={deleteTarget !== null}
            title="Delete this notebook?"
            message={
              deleteTarget
                ? `"${deleteTarget.name}" and all of its cells will be permanently deleted. This can't be undone.`
                : ""
            }
            confirmLabel="Delete"
            destructive
            onConfirm={performDelete}
            onCancel={() => setDeleteTarget(null)}
          />
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Shared confirm / warning popup
// ──────────────────────────────────────────────────────────────────────────

function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  destructive = false,
  alertOnly = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  destructive?: boolean;
  alertOnly?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-xl">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{message}</p>
        <div className="mt-5 flex justify-end gap-2">
          {!alertOnly && (
            <button
              onClick={onCancel}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
            >
              Cancel
            </button>
          )}
          <button
            onClick={alertOnly ? onCancel : onConfirm}
            className={`rounded-md px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-opacity hover:opacity-90 ${
              destructive ? "bg-destructive" : "bg-primary-gradient"
            }`}
          >
            {alertOnly ? "Got it" : confirmLabel}
          </button>
        </div>
      </div>
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
        placeholder="Search notebooks…"
        className="w-56 rounded-md border border-input bg-secondary py-1.5 pl-8 pr-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
      />
      {value && (
        <button
          onClick={() => onChange("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label="Clear search"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}

function CreateNotebookModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (nb: Notebook) => void;
}): JSX.Element {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const nb = await apiFetch<Notebook>("/notebook", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), description: description.trim() || undefined }),
      });
      onCreated(nb);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create notebook.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-sm">
        <div className="absolute -top-3 left-4 h-4 w-28 rounded-t-md bg-primary-gradient" />
        <form
          onSubmit={handleCreate}
          className="relative rounded-lg rounded-tl-none border border-border bg-card p-5 shadow-lg"
        >
          <div className="mb-4 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-foreground">New notebook</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                You'll connect a dataset next — upload a CSV or pick an existing source.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          <label
            htmlFor="notebook-name"
            className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Name
          </label>
          <input
            ref={inputRef}
            id="notebook-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Churn dataset exploration"
            className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
          />

          <label
            htmlFor="notebook-description"
            className="mb-1.5 mt-4 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Description <span className="normal-case text-muted-foreground/70">(optional)</span>
          </label>
          <textarea
            id="notebook-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What are you trying to find out?"
            rows={2}
            className="w-full resize-none rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
          />

          {createError && <p className="mt-2 text-xs text-destructive">{createError}</p>}

          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={creating || !name.trim()}
              className="rounded-md bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {creating ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function RenameNotebookModal({
  notebook,
  onClose,
  onRenamed,
}: {
  notebook: Notebook;
  onClose: () => void;
  onRenamed: (nb: Notebook) => void;
}): JSX.Element {
  const [name, setName] = useState(notebook.name);
  const [description, setDescription] = useState(notebook.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const nb = await apiFetch<Notebook>(`/notebook/${notebook.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: name.trim(), description: description.trim() || undefined }),
      });
      onRenamed(nb);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename notebook.");
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
      <div className="relative w-full max-w-sm">
        <div className="absolute -top-3 left-4 h-4 w-28 rounded-t-md bg-primary-gradient" />
        <form
          onSubmit={handleSave}
          className="relative rounded-lg rounded-tl-none border border-border bg-card p-5 shadow-lg"
        >
          <div className="mb-4 flex items-start justify-between">
            <h2 className="text-sm font-semibold text-foreground">Rename notebook</h2>
            <button
              type="button"
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          <label
            htmlFor="rename-notebook-name"
            className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Name
          </label>
          <input
            ref={inputRef}
            id="rename-notebook-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
          />

          <label
            htmlFor="rename-notebook-description"
            className="mb-1.5 mt-4 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Description <span className="normal-case text-muted-foreground/70">(optional)</span>
          </label>
          <textarea
            id="rename-notebook-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full resize-none rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
          />

          {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              className="rounded-md bg-primary-gradient px-3 py-1.5 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function formatUpdatedDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

type Props = {
  notebook: Notebook;
  onOpen: () => void;
  onRename: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  isDuplicating: boolean;
  isDeleting: boolean;
};

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Workspace, WorkspaceDataType } from "./shared";
import { apiFetch, statusMeta, DATA_TYPE_OPTIONS } from "./shared";

// Dotted background (same component used on the Notebooks page)
import DotField from "@/components/background/DotField";

/**
 * Workspace Page
 * ============
 */
export default function WorkspacePage(): JSX.Element {
  const navigate = useNavigate();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const refreshWorkspaces = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const data = await apiFetch<Workspace[]>("/workspace");
      setWorkspaces(data);
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load workspaces.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshWorkspaces();
  }, [refreshWorkspaces]);

  const handleCreated = (ws: Workspace) => {
    setIsCreateOpen(false);
    navigate(`/workspace/${ws.workspace_id}`);
  };

  const filteredWorkspaces = workspaces.filter((ws) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      ws.name.toLowerCase().includes(q)
    );
  });

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
      <div className="relative h-full min-h-0 overflow-y-auto bg-background text-foreground">
        {/* Dotted background. Lives inside the same bg-background container it
            paints on top of, and the content column below is un-capped
            (min-h-full, not h-full) so this stretches across the full
            scrollable content, not just the first screen. */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <DotField
            dotRadius={1.5}
            dotSpacing={14}
            bulgeStrength={67}
            glowRadius={160}
            sparkle={false}
            waveAmplitude={0}
            cursorRadius={500}
            cursorForce={0}
            bulgeOnly={false}
            gradientFrom="#592603"
            gradientTo="#613508"
            glowColor="#120F17"
          />
        </div>

        <div className="relative min-h-full w-full mx-auto max-w-6xl px-6 py-10">
          <header className="relative mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
            <div>
              <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
                Workspace
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Upload your data, pick what to predict, and compare models.
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
                New workspace
              </button>
            </div>
          </header>

          {loading && <p className="relative text-sm text-muted-foreground">Loading workspaces…</p>}
          {listError && <p className="relative text-sm text-destructive">{listError}</p>}

          {!loading && !listError && workspaces.length === 0 && (
            <div className="relative flex h-64 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">
                No workspaces yet — create one to get started.
              </p>
            </div>
          )}

          {!loading && !listError && workspaces.length > 0 && filteredWorkspaces.length === 0 && (
            <div className="relative flex h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">No workspaces match “{query}”.</p>
            </div>
          )}

          <div className="relative grid grid-cols-1 gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {filteredWorkspaces.map((ws) => (
              <WorkspaceCard key={ws.workspace_id} workspace={ws} onOpen={() => navigate(`/workspace/${ws.workspace_id}`)} />
            ))}
          </div>
        </div>

        {isCreateOpen && (
          <CreateWorkspaceModal onClose={() => setIsCreateOpen(false)} onCreated={handleCreated} />
        )}
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
        placeholder="Search workspaces…"
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

function CreateWorkspaceModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (ws: Workspace) => void;
}): JSX.Element {
  const [name, setName] = useState("");
  const [dataType, setDataType] = useState<WorkspaceDataType>("structured");
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
      const ws = await apiFetch<Workspace>("/workspace", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), data_type: dataType }),
      });
      onCreated(ws);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create workspace.");
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
              <h2 className="text-sm font-semibold text-foreground">New workspace</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Give it a name and choose the kind of data you'll work with — this can't be
                changed once the workspace has an input source.
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
            htmlFor="workspace-name"
            className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Name
          </label>
          <input
            ref={inputRef}
            id="workspace-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Churn model v1"
            className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
          />

          <label
            htmlFor="workspace-data-type"
            className="mb-1.5 mt-4 block text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
          >
            Data type
          </label>
          <select
            id="workspace-data-type"
            value={dataType}
            onChange={(e) => setDataType(e.target.value as WorkspaceDataType)}
            className="w-full rounded-md border border-input bg-secondary px-3 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring"
          >
            {DATA_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

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

/**
 * WorkspaceCard
 * =============
 * Deliberately distinct from the Notebooks page's folder-shaped card:
 * this one reads like a little "app window" — a title bar with a status
 * dot, a body with the workspace name and target column, and a raised
 * drop-shadow "twin" card underneath that peeks out on hover, giving it
 * a stacked / layered feel instead of a flat folder tab.
 */
function WorkspaceCard({ workspace, onOpen }: { workspace: Workspace; onOpen: () => void }): JSX.Element {
  const meta = statusMeta(workspace.status);

  return (
    <button onClick={onOpen} className="group relative block w-full text-left">
      {/* Ghost card peeking out from behind — creates the stacked-deck effect */}
      <div
        className="absolute inset-0 translate-x-2 translate-y-2 rounded-2xl border border-border/70 bg-secondary/50 transition-transform duration-300 ease-out group-hover:translate-x-3 group-hover:translate-y-3"
        aria-hidden
      />

      {/* Main card */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-all duration-300 ease-out group-hover:-translate-y-1 group-hover:shadow-xl group-hover:border-primary/30">
        {/* Title bar */}
        <div className="flex items-center gap-2 border-b border-border/70 bg-secondary/40 px-4 py-2.5">
          <span className="relative flex h-2 w-2">
            <span
              className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${meta.tabClass}`}
            />
            <span className={`relative inline-flex h-2 w-2 rounded-full ${meta.tabClass}`} />
          </span>
          <span className="flex-1 truncate text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {meta.label}
          </span>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" className="text-muted-foreground/60">
            <rect x="4" y="4" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
            <rect x="13" y="4" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
            <rect x="4" y="13" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
            <rect x="13" y="13" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </div>

        {/* Body */}
        <div className="px-4 pb-4 pt-3.5">
          <h2 className="truncate text-[15px] font-semibold leading-snug text-foreground">
            {workspace.name}
          </h2>

          {workspace.target_column ? (
            <div className="mt-2.5 inline-flex max-w-full items-center gap-1.5 rounded-md bg-secondary px-2 py-1">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" className="shrink-0 text-primary">
                <path d="M12 2v20M2 12h20" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
              </svg>
              <span className="truncate font-mono text-[11px] text-muted-foreground">
                {workspace.target_column}
              </span>
            </div>
          ) : (
            <p className="mt-2.5 text-xs text-muted-foreground">No target column yet</p>
          )}
        </div>

        {/* Footer accent — brand gradient hairline that "fills in" on hover */}
        <div className="h-[3px] w-full bg-border/60">
          <div className="h-full w-0 bg-primary-gradient transition-all duration-300 ease-out group-hover:w-full" />
        </div>
      </div>
    </button>
  );
}
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Cpu, Workflow } from "lucide-react";
import type {
  MLModelRead,
  ModelDetails,
  ModelFile,
  ModelMetric,
  ModelSummary,
  Workspace,
} from "@/types";
import { downloadOutputFile, formatPercent, statusMeta } from "@/pages/workspace/shared";
import { apiFetch } from "@/lib/utils";
import { etlApi } from "@/api/etlApi";
import {
  ModelDetailsDialog,
  type ModelMetricDisplay,
} from "@/components/models/ModelDetailsDialog";

// Dotted background (same component used on the Workspace page) — kept
// consistent so Models reads as a continuation of Workspace, not a
// separately-designed page.
import DotField from "@/components/background/DotField";

/**
 * Models Page
 * ===========
 *
 * Two galleries: the winning model from each workspace, and every model
 * registered by an ETL pipeline run. Clicking any card opens the shared model
 * details dialog (expected input, preprocessing, training curve); navigation
 * to the owning workspace/pipeline stays a separate, explicit action.
 */

interface BestModelEntry {
  workspace: Workspace;
  modelSummary: ModelSummary | null;
  bestModelKey: string | null;
  metrics: ModelMetric[];
  file: ModelFile | null;
  details: ModelDetails | null;
}

interface DialogState {
  title: string;
  subtitle?: string | null;
  details: ModelDetails | null;
  metrics: ModelMetricDisplay[];
}

export default function ModelsPage(): JSX.Element {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<BestModelEntry[]>([]);
  const [etlModels, setEtlModels] = useState<MLModelRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [downloadingUrl, setDownloadingUrl] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        // Collect the best model out of EVERY workspace, not just ones
        // currently sitting in "completed" status. A workspace can still
        // hold a model from an earlier successful build even if its most
        // recent run later failed or is in progress — /models degrades
        // gracefully (empty model_summary) for workspaces that never
        // built one, so it's safe to query all of them.
        const [workspaces, pipelineModels] = await Promise.all([
          apiFetch<Workspace[]>("/workspace"),
          etlApi.models().catch(() => [] as MLModelRead[]),
        ]);

        const results = await Promise.all(
          workspaces.map(async (ws): Promise<BestModelEntry> => {
            try {
              const data = await apiFetch<{
                model_summary?: ModelSummary;
                model_comparison: ModelMetric[];
                best_model?: string | null;
                model_details?: ModelDetails | null;
                model_files: ModelFile[];
              }>(`/workspace/${ws.workspace_id}/models`);

              const bestModelKey = data.best_model ?? null;
              const metrics = bestModelKey
                ? data.model_comparison.filter((m) => m.model_key === bestModelKey)
                : [];
              const file = bestModelKey
                ? data.model_files.find((f) => f.model_key === bestModelKey) ?? null
                : null;

              return {
                workspace: ws,
                modelSummary: data.model_summary ?? null,
                bestModelKey,
                metrics,
                file,
                details: data.model_details ?? data.model_summary?.model_details ?? null,
              };
            } catch {
              // A single workspace's models endpoint failing shouldn't
              // block the rest of the gallery from rendering.
              return {
                workspace: ws,
                modelSummary: null,
                bestModelKey: null,
                metrics: [],
                file: null,
                details: null,
              };
            }
          })
        );

        if (!cancelled) {
          setEntries(results.filter((r) => r.bestModelKey));
          setEtlModels(pipelineModels);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load models.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.workspace.name.toLowerCase().includes(q) ||
        e.bestModelKey?.toLowerCase().includes(q) ||
        e.workspace.target_column?.toLowerCase().includes(q)
    );
  }, [entries, query]);

  const filteredEtl = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return etlModels;
    return etlModels.filter(
      (model) =>
        model.name.toLowerCase().includes(q) ||
        model.pipeline_name?.toLowerCase().includes(q) ||
        model.algorithm?.toLowerCase().includes(q) ||
        model.task.toLowerCase().includes(q)
    );
  }, [etlModels, query]);

  const handleDownload = async (entry: BestModelEntry) => {
    if (!entry.file) return;
    setDownloadError(null);
    setDownloadingUrl(entry.file.file_url);
    try {
      await downloadOutputFile(entry.workspace.workspace_id, entry.file.file_url, `${entry.file.model_key}.joblib`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed.");
    } finally {
      setDownloadingUrl(null);
    }
  };

  const openWorkspaceModel = (entry: BestModelEntry) => {
    setDialog({
      title: entry.bestModelKey ?? entry.workspace.name,
      subtitle: `${entry.workspace.name}${entry.workspace.target_column ? ` · target ${entry.workspace.target_column}` : ""}`,
      details: entry.details,
      metrics: entry.metrics.map((m) => ({
        label: m.metric_name,
        value: `${formatPercent(m.mean)}${m.std != null ? ` ± ${formatPercent(m.std)}` : ""}`,
      })),
    });
  };

  const openEtlModel = (model: MLModelRead) => {
    setDialog({
      title: model.name,
      subtitle: model.pipeline_name ? `Pipeline: ${model.pipeline_name}` : model.pipeline_id,
      details: model.details ?? null,
      metrics: etlMetricDisplays(model),
    });
  };

  const totalModels = entries.length + etlModels.length;

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
      <div className="relative h-full min-h-0 overflow-y-auto bg-background text-foreground">
        {/* Dotted background. Lives inside the same bg-background container it
            paints on top of, and the content column below is un-capped
            (min-h-full, not h-full) so this stretches across the full
            scrollable content, not just the first screen — same treatment
            as the Workspace page. */}
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
          <header className="relative mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
            <div>
              <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
                Models
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                {loading
                  ? "Gathering results…"
                  : `${totalModels} model${totalModels === 1 ? "" : "s"} across your workspaces and pipelines. Click a card for its input schema, preprocessing, and training curve.`}
              </p>
            </div>

            <SearchInput value={query} onChange={setQuery} />
          </header>

          {loading && <p className="relative text-sm text-muted-foreground">Loading models…</p>}
          {error && <p className="relative text-sm text-destructive">{error}</p>}
          {downloadError && <p className="relative mb-4 text-sm text-destructive">{downloadError}</p>}

          {!loading && !error && totalModels === 0 && (
            <div className="relative flex h-64 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">
                No models yet — build one from a workspace or train one in a pipeline.
              </p>
              <button onClick={() => navigate("/workspace")} className="text-sm font-medium text-primary hover:underline">
                Go to Workspace →
              </button>
            </div>
          )}

          {!loading && !error && totalModels > 0 && filtered.length === 0 && filteredEtl.length === 0 && (
            <div className="relative flex h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
              <p className="text-sm text-muted-foreground">No models match “{query}”.</p>
            </div>
          )}

          {filtered.length > 0 && (
            <section className="relative">
              <SectionHeading
                icon={<Cpu className="h-4 w-4 text-primary" />}
                title="Workspace models"
                count={filtered.length}
              />
              <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
                {filtered.map((entry) => (
                  <BestModelCard
                    key={entry.workspace.workspace_id}
                    entry={entry}
                    downloading={downloadingUrl === entry.file?.file_url}
                    onDownload={() => handleDownload(entry)}
                    onOpenDetails={() => openWorkspaceModel(entry)}
                    onOpenWorkspace={() => navigate(`/workspace/${entry.workspace.workspace_id}`)}
                  />
                ))}
              </div>
            </section>
          )}

          {filteredEtl.length > 0 && (
            <section className="relative mt-10">
              <SectionHeading
                icon={<Workflow className="h-4 w-4 text-primary" />}
                title="Pipeline models"
                count={filteredEtl.length}
              />
              <div className="mt-4 grid grid-cols-1 gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
                {filteredEtl.map((model) => (
                  <EtlModelCard
                    key={model.id}
                    model={model}
                    onOpenDetails={() => openEtlModel(model)}
                    onOpenPipeline={() => navigate(`/pipelines/${model.pipeline_id}`)}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      <ModelDetailsDialog
        open={dialog != null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        title={dialog?.title ?? "Model"}
        subtitle={dialog?.subtitle}
        details={dialog?.details}
        metrics={dialog?.metrics}
      />
    </div>
  );
}

function etlMetricDisplays(model: MLModelRead): ModelMetricDisplay[] {
  const metrics = model.metrics ?? {};
  const displays: ModelMetricDisplay[] = [
    { label: "task", value: model.task },
  ];
  if (model.algorithm) displays.push({ label: "algorithm", value: model.algorithm });
  if (typeof metrics.inertia === "number") {
    displays.push({ label: "inertia", value: metrics.inertia.toFixed(2) });
  }
  if (typeof metrics.n_clusters === "number") {
    displays.push({ label: "clusters", value: String(metrics.n_clusters) });
  }
  return displays;
}

function SectionHeading({
  icon,
  title,
  count,
}: {
  icon: JSX.Element;
  title: string;
  count: number;
}): JSX.Element {
  return (
    <div className="flex items-center gap-2 border-b border-border pb-2">
      {icon}
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{count}</span>
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
        placeholder="Search models…"
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

/**
 * BestModelCard
 * =============
 * Same "app window" language as the Workspace page's WorkspaceCard: a
 * ghost card peeking out behind for a stacked-deck feel, a title bar with
 * a live status dot, and a gradient hairline that fills in on hover. The
 * whole card opens the model details dialog; Download and View Workspace are
 * real nested buttons (stopPropagation keeps them from also opening it), so
 * the outer wrapper is a div instead of a button to keep the markup valid.
 */
function BestModelCard({
  entry,
  downloading,
  onDownload,
  onOpenDetails,
  onOpenWorkspace,
}: {
  entry: BestModelEntry;
  downloading: boolean;
  onDownload: () => void;
  onOpenDetails: () => void;
  onOpenWorkspace: () => void;
}): JSX.Element {
  const { workspace, modelSummary, bestModelKey, metrics, file } = entry;
  const meta = statusMeta(workspace.status);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenDetails}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onOpenDetails();
      }}
      className="group relative block w-full cursor-pointer text-left"
    >
      {/* Ghost card peeking out from behind — creates the stacked-deck effect */}
      <div
        className="absolute inset-0 translate-x-2 translate-y-2 rounded-2xl border border-border/70 bg-secondary/50 transition-transform duration-300 ease-out group-hover:translate-x-3 group-hover:translate-y-3"
        aria-hidden
      />

      {/* Main card */}
      <div className="relative flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-all duration-300 ease-out group-hover:-translate-y-1 group-hover:shadow-xl group-hover:border-primary/30">
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
          <span className="shrink-0 rounded bg-primary-gradient px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
            Best
          </span>
        </div>

        {/* Body */}
        <div className="flex-1 px-4 pb-4 pt-3.5">
          <h2 className="truncate text-[15px] font-semibold leading-snug text-foreground">{workspace.name}</h2>

          {workspace.target_column && (
            <div className="mt-2.5 inline-flex max-w-full items-center gap-1.5 rounded-md bg-secondary px-2 py-1">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" className="shrink-0 text-primary">
                <path d="M12 2v20M2 12h20" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
              </svg>
              <span className="truncate font-mono text-[11px] text-muted-foreground">
                {workspace.target_column}
              </span>
            </div>
          )}

          <div className="mt-3 flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary-gradient" />
            <span className="truncate font-mono text-sm font-medium text-foreground">{bestModelKey}</span>
          </div>

          {metrics.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {metrics.map((m, i) => (
                <span
                  key={`${m.metric_name}-${i}`}
                  className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[11px] text-foreground"
                >
                  {m.metric_name}: {formatPercent(m.mean)}
                  {m.std != null && <span className="text-muted-foreground"> ± {formatPercent(m.std)}</span>}
                </span>
              ))}
            </div>
          )}

          {modelSummary?.best_model_rationale && (
            <p className="mt-3 line-clamp-3 text-xs text-muted-foreground">{modelSummary.best_model_rationale}</p>
          )}
        </div>

        {/* Footer — view workspace + download, both nested actions */}
        <div className="flex items-center justify-between border-t border-border/60 px-4 py-2.5">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenWorkspace();
            }}
            className="text-xs font-medium text-muted-foreground transition-colors hover:text-primary"
          >
            View Workspace
          </button>
          {file && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDownload();
              }}
              disabled={downloading}
              className="flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {downloading ? "Downloading…" : "Download"}
            </button>
          )}
        </div>

        {/* Footer accent — brand gradient hairline that "fills in" on hover */}
        <div className="h-[3px] w-full bg-border/60">
          <div className="h-full w-0 bg-primary-gradient transition-all duration-300 ease-out group-hover:w-full" />
        </div>
      </div>
    </div>
  );
}

/**
 * EtlModelCard
 * ============
 * Pipeline-trained model. Deliberately the same shape as BestModelCard so the
 * two galleries read as one page rather than two designs.
 */
function EtlModelCard({
  model,
  onOpenDetails,
  onOpenPipeline,
}: {
  model: MLModelRead;
  onOpenDetails: () => void;
  onOpenPipeline: () => void;
}): JSX.Element {
  const metrics = model.metrics ?? {};
  const created = model.created_at ? new Date(model.created_at).toLocaleDateString() : null;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpenDetails}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onOpenDetails();
      }}
      className="group relative block w-full cursor-pointer text-left"
    >
      <div
        className="absolute inset-0 translate-x-2 translate-y-2 rounded-2xl border border-border/70 bg-secondary/50 transition-transform duration-300 ease-out group-hover:translate-x-3 group-hover:translate-y-3"
        aria-hidden
      />

      <div className="relative flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-all duration-300 ease-out group-hover:-translate-y-1 group-hover:shadow-xl group-hover:border-primary/30">
        <div className="flex items-center gap-2 border-b border-border/70 bg-secondary/40 px-4 py-2.5">
          <span className="flex h-2 w-2 rounded-full bg-emerald-500" />
          <span className="flex-1 truncate text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {model.task}
          </span>
          <span className="shrink-0 rounded border border-border bg-card px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            ETL
          </span>
        </div>

        <div className="flex-1 px-4 pb-4 pt-3.5">
          <h2 className="truncate text-[15px] font-semibold leading-snug text-foreground">{model.name}</h2>

          {model.pipeline_name && (
            <div className="mt-2.5 inline-flex max-w-full items-center gap-1.5 rounded-md bg-secondary px-2 py-1">
              <Workflow className="h-3 w-3 shrink-0 text-primary" />
              <span className="truncate font-mono text-[11px] text-muted-foreground">{model.pipeline_name}</span>
            </div>
          )}

          <div className="mt-3 flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary-gradient" />
            <span className="truncate font-mono text-sm font-medium text-foreground">
              {model.algorithm || model.task}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap gap-1.5">
            {model.target && (
              <span className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[11px] text-foreground">
                target: {model.target}
              </span>
            )}
            {typeof metrics.inertia === "number" && (
              <span className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[11px] text-foreground">
                inertia: {metrics.inertia.toFixed(2)}
              </span>
            )}
            {typeof metrics.n_clusters === "number" && (
              <span className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[11px] text-foreground">
                k: {String(metrics.n_clusters)}
              </span>
            )}
          </div>

          {created && <p className="mt-3 text-xs text-muted-foreground">Trained {created}</p>}
        </div>

        <div className="flex items-center justify-between border-t border-border/60 px-4 py-2.5">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenPipeline();
            }}
            className="text-xs font-medium text-muted-foreground transition-colors hover:text-primary"
          >
            Open pipeline
          </button>
          <span className="text-xs font-medium text-muted-foreground group-hover:text-primary">Model card →</span>
        </div>

        <div className="h-[3px] w-full bg-border/60">
          <div className="h-full w-0 bg-primary-gradient transition-all duration-300 ease-out group-hover:w-full" />
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PipelineStepList } from "@/components/etl/PipelineStepList";
import { etlApi } from "@/api/etlApi";
import { apiFetch } from "@/lib/utils";
import type {
  JobEventRead,
  Notebook,
  PipelineDefinition,
  PipelineRead,
  PipelineRunRead,
} from "@/types";

function pipelineStatusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "approved") return "default";
  if (status === "archived") return "secondary";
  return "outline";
}

function runStatusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (status === "succeeded") return "default";
  if (status === "running") return "secondary";
  return "outline";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function formatDuration(run: PipelineRunRead): string {
  if (!run.started_at) return "—";
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isDefinitionDirty(text: string, definition: PipelineDefinition | undefined): boolean {
  if (!definition) return false;
  try {
    return JSON.stringify(JSON.parse(text)) !== JSON.stringify(definition);
  } catch {
    return true;
  }
}

function RunResult({ run }: { run: PipelineRunRead }): JSX.Element | null {
  const result = asRecord(run.result);
  if (!result) return null;

  const sink = asRecord(result.sink);
  const materialization = asRecord(result.materialization);
  const columns = Array.isArray(result.columns)
    ? result.columns.filter((column): column is string => typeof column === "string")
    : [];

  const rows: Array<{ label: string; value: string }> = [];
  if (sink) {
    const kind = asString(sink.kind);
    if (kind) rows.push({ label: "Sink", value: kind });
    const table = asString(sink.table);
    if (table) rows.push({ label: "Table", value: table });
    const objectKey = asString(sink.object_key);
    if (objectKey) rows.push({ label: "Object key", value: objectKey });
    const mode = asString(sink.mode);
    if (mode) rows.push({ label: "Write mode", value: mode });
    const sinkRows = asNumber(sink.row_count);
    if (sinkRows !== null) rows.push({ label: "Rows written", value: String(sinkRows) });
  }
  if (run.row_count !== null && run.row_count !== undefined) {
    rows.push({ label: "Run row count", value: String(run.row_count) });
  }
  if (materialization) {
    const notebookId = asString(materialization.notebook_id);
    if (notebookId) rows.push({ label: "Materialized notebook", value: notebookId });
    const cellId = asString(materialization.dashboard_cell_id);
    if (cellId) rows.push({ label: "Dashboard cell", value: cellId });
  }
  const materializationError = asString(result.materialization_error);
  if (materializationError) {
    rows.push({ label: "Materialization error", value: materializationError });
  }

  return (
    <div className="mt-3 space-y-3 rounded-lg border border-border bg-background/60 p-3">
      {result.dry_run === true && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" /> Validated only — no destination was written.
        </div>
      )}
      {rows.length > 0 && (
        <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex flex-col">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="break-words font-mono text-foreground">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {columns.length > 0 && (
        <div className="text-xs">
          <div className="text-muted-foreground">Output columns</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {columns.map((column) => (
              <Badge key={column} variant="secondary" className="font-mono font-normal">
                {column}
              </Badge>
            ))}
          </div>
        </div>
      )}
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">Raw result</summary>
        <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px] leading-5">
          {JSON.stringify(run.result, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export default function PipelineDetailPage(): JSX.Element {
  const { pipelineId = "" } = useParams();
  const navigate = useNavigate();

  const [pipeline, setPipeline] = useState<PipelineRead | null>(null);
  const [runs, setRuns] = useState<PipelineRunRead[]>([]);
  const [events, setEvents] = useState<JobEventRead[]>([]);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [definitionText, setDefinitionText] = useState("");
  const [materializeNotebookId, setMaterializeNotebookId] = useState("");

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [running, setRunning] = useState<"dry" | "prod" | null>(null);
  const [materializing, setMaterializing] = useState(false);

  const loadRuns = useCallback(async () => {
    const nextRuns = await etlApi.runs(pipelineId);
    setRuns(nextRuns);
    setSelectedRunId((current) =>
      current && nextRuns.some((run) => run.id === current)
        ? current
        : nextRuns[0]?.id ?? null
    );
    return nextRuns;
  }, [pipelineId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const [current, nextRuns, nextNotebooks] = await Promise.all([
          etlApi.get(pipelineId),
          etlApi.runs(pipelineId),
          apiFetch<Notebook[]>("/notebook").catch(() => [] as Notebook[]),
        ]);
        if (cancelled) return;

        setPipeline(current);
        setName(current.name);
        setDescription(current.description ?? "");
        setDefinitionText(JSON.stringify(current.definition, null, 2));
        setRuns(nextRuns);
        setSelectedRunId(nextRuns[0]?.id ?? null);
        setNotebooks(nextNotebooks);
        setMaterializeNotebookId(nextNotebooks[0]?.id ?? "");
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to load pipeline.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  const hasActiveRun = runs.some(
    (run) => run.status === "queued" || run.status === "running"
  );

  useEffect(() => {
    if (!hasActiveRun) return;
    const timer = window.setInterval(() => {
      void loadRuns().catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, loadRuns]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId]
  );

  const canMaterialize = useMemo(
    () =>
      runs.some((run) => {
        if (run.dry_run || run.status !== "succeeded") return false;
        const result = asRecord(run.result);
        const sink = result ? asRecord(result.sink) : null;
        return asString(sink?.kind) === "postgres";
      }),
    [runs]
  );

  useEffect(() => {
    if (!selectedRunId) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    void etlApi
      .events(selectedRunId)
      .then((nextEvents) => {
        if (!cancelled) setEvents(nextEvents);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to load run events.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId, selectedRun?.status]);

  const selectedRunActive =
    selectedRun?.status === "queued" || selectedRun?.status === "running";

  useEffect(() => {
    if (!selectedRunActive || !selectedRunId) return;
    const timer = window.setInterval(() => {
      void etlApi
        .events(selectedRunId)
        .then(setEvents)
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [selectedRunActive, selectedRunId]);

  const definitionDirty = isDefinitionDirty(definitionText, pipeline?.definition);
  const formDirty =
    !!pipeline &&
    (name !== pipeline.name ||
      description !== (pipeline.description ?? "") ||
      definitionDirty);

  const save = async () => {
    if (!pipeline) return;
    let parsed: PipelineDefinition;
    try {
      parsed = JSON.parse(definitionText) as PipelineDefinition;
    } catch {
      setError("The pipeline definition must be valid JSON.");
      return;
    }
    if (!parsed || !Array.isArray(parsed.steps) || parsed.steps.length === 0) {
      setError("The definition must contain a non-empty 'steps' array.");
      return;
    }

    const definitionChanged = isDefinitionDirty(definitionText, pipeline.definition);
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await etlApi.update(pipeline.id, {
        name: name.trim() || pipeline.name,
        description: description.trim() || null,
        ...(definitionChanged ? { definition: parsed } : {}),
      });
      setPipeline(updated);
      setName(updated.name);
      setDescription(updated.description ?? "");
      setDefinitionText(JSON.stringify(updated.definition, null, 2));
      setNotice(
        definitionChanged
          ? "Saved. The definition change reset approval and incremented the version."
          : "Saved."
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save pipeline.");
    } finally {
      setSaving(false);
    }
  };

  const approve = async () => {
    if (!pipeline) return;
    if (
      !window.confirm(
        `Approve "${pipeline.name}" (version ${pipeline.version}) for production runs?`
      )
    ) {
      return;
    }
    setApproving(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await etlApi.approve(pipeline.id);
      setPipeline(updated);
      setNotice("Pipeline approved for production runs.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to approve pipeline.");
    } finally {
      setApproving(false);
    }
  };

  const startRun = async (dryRun: boolean) => {
    if (!pipeline) return;
    if (!dryRun && pipeline.status !== "approved") {
      setError("Approve this pipeline before starting a production run.");
      return;
    }
    if (
      !dryRun &&
      !window.confirm(
        `Start a production run of "${pipeline.name}"? This writes to its configured destination.`
      )
    ) {
      return;
    }
    setRunning(dryRun ? "dry" : "prod");
    setError(null);
    setNotice(null);
    try {
      const run = await etlApi.run(pipeline.id, { dry_run: dryRun });
      setRuns((items) => [run, ...items.filter((item) => item.id !== run.id)]);
      setSelectedRunId(run.id);
      setNotice(dryRun ? "Dry run queued." : "Production run queued.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to queue a run.");
    } finally {
      setRunning(null);
    }
  };

  const materialize = async () => {
    if (!pipeline || !materializeNotebookId) return;
    setMaterializing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await etlApi.materialize(pipeline.id, {
        notebook_id: materializeNotebookId,
      });
      const notebookId = asString(result.notebook_id) ?? materializeNotebookId;
      navigate(`/notebooks/${notebookId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to materialize pipeline.");
    } finally {
      setMaterializing(false);
    }
  };

  if (loading) {
    return (
      <main className="flex min-h-full items-center justify-center bg-background p-8 text-muted-foreground">
        <Loader2 className="mr-2 animate-spin" /> Loading pipeline...
      </main>
    );
  }

  if (!pipeline) {
    return (
      <main className="min-h-full bg-background px-6 py-10">
        <div className="mx-auto max-w-3xl rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {error ?? "Pipeline not found."}
        </div>
      </main>
    );
  }

  const canRunProduction = pipeline.status === "approved";

  return (
    <main className="min-h-full bg-background px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <Link
          to="/pipelines"
          className="mb-6 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Pipelines
        </Link>

        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-3xl font-semibold text-foreground">{pipeline.name}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {pipeline.description || "No description"}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={pipelineStatusVariant(pipeline.status)}>
                {pipeline.status.replaceAll("_", " ")}
              </Badge>
              <span>v{pipeline.version}</span>
              <span>
                {pipeline.definition.steps.length} step
                {pipeline.definition.steps.length === 1 ? "" : "s"}
              </span>
              {pipeline.approved_at && <span>Approved {formatDate(pipeline.approved_at)}</span>}
              {formDirty && <Badge variant="outline">Unsaved changes</Badge>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {!canRunProduction && (
              <Button variant="outline" onClick={() => void approve()} disabled={approving}>
                {approving ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
                {approving ? "Approving..." : "Approve"}
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => void startRun(true)}
              disabled={running !== null}
            >
              {running === "dry" ? <Loader2 className="animate-spin" /> : <Play />}
              Dry run
            </Button>
            <Button
              onClick={() => void startRun(false)}
              disabled={!canRunProduction || running !== null}
              title={
                canRunProduction
                  ? undefined
                  : "Approve this pipeline before starting a production run."
              }
            >
              {running === "prod" ? <Loader2 className="animate-spin" /> : <Play />}
              Production run
            </Button>
          </div>
        </header>

        {error && (
          <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            <span className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </span>
            <button type="button" className="text-xs underline" onClick={() => setError(null)}>
              Dismiss
            </button>
          </div>
        )}
        {notice && (
          <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
            <span className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> {notice}
            </span>
            <button type="button" className="text-xs underline" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
          {/* Definition */}
          <section className="space-y-4 rounded-xl border border-border bg-card p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold text-foreground">Definition</h2>
              <Button size="sm" onClick={() => void save()} disabled={saving || !formDirty}>
                {saving ? <Loader2 className="animate-spin" /> : <Save />}
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="detail-name" className="text-xs font-medium text-muted-foreground">
                Name
              </label>
              <Input
                id="detail-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="detail-description"
                className="text-xs font-medium text-muted-foreground"
              >
                Description
              </label>
              <Textarea
                id="detail-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Description"
              />
            </div>

            <div>
              <div className="mb-2 text-xs font-medium text-muted-foreground">DAG</div>
              <PipelineStepList steps={pipeline.definition.steps} compact />
            </div>

            <details className="rounded-lg border border-border bg-background/60 p-3">
              <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground hover:text-foreground">
                Edit definition JSON
              </summary>
              <Textarea
                value={definitionText}
                onChange={(event) => setDefinitionText(event.target.value)}
                className="mt-2 min-h-[260px] font-mono text-xs"
                spellCheck={false}
                aria-label="Pipeline definition JSON"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                Saving a definition change resets approval and increments the version. Sources
                must be allow-listed or use the configured ETL source prefix; Postgres sinks must
                use the configured sink prefix.
              </p>
            </details>

            <div className="space-y-2 border-t border-border pt-4">
              <div className="text-xs font-medium text-muted-foreground">
                Materialize latest Postgres sink
              </div>
              {notebooks.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No notebooks available.{" "}
                  <Link to="/notebooks" className="text-primary underline">
                    Create one first
                  </Link>
                  .
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <Select
                    value={materializeNotebookId}
                    onValueChange={setMaterializeNotebookId}
                  >
                    <SelectTrigger className="min-w-[220px] flex-1">
                      <SelectValue placeholder="Select a notebook" />
                    </SelectTrigger>
                    <SelectContent>
                      {notebooks.map((notebook) => (
                        <SelectItem key={notebook.id} value={notebook.id}>
                          {notebook.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="outline"
                    onClick={() => void materialize()}
                    disabled={materializing || !materializeNotebookId || !canMaterialize}
                    title={
                      canMaterialize
                        ? undefined
                        : "Run the pipeline successfully with a Postgres sink before materializing."
                    }
                  >
                    {materializing ? <Loader2 className="animate-spin" /> : <Sparkles />}
                    Materialize
                  </Button>
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                {canMaterialize
                  ? "Adds the sink table as a managed data source and creates a dashboard preview cell in the selected notebook."
                  : "A successful production run with a Postgres sink is required before the result can be materialized."}
              </p>
            </div>
          </section>

          {/* Runs and logs */}
          <section className="space-y-4">
            <div className="rounded-xl border border-border bg-card p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="font-semibold text-foreground">Run history</h2>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void loadRuns().catch(() => undefined)}
                >
                  <RefreshCw /> Refresh
                </Button>
              </div>

              {runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No runs yet. Start with a dry run to validate the definition.
                </p>
              ) : (
                <div className="space-y-2">
                  {runs.map((run) => {
                    const isSelected = run.id === selectedRunId;
                    return (
                      <button
                        key={run.id}
                        type="button"
                        onClick={() => setSelectedRunId(run.id)}
                        className={`flex w-full flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-left text-sm transition-colors ${
                          isSelected
                            ? "border-primary/50 bg-primary/5"
                            : "border-border hover:bg-secondary"
                        }`}
                      >
                        <span className="flex items-center gap-2">
                          {run.status === "succeeded" ? (
                            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                          ) : run.status === "failed" ? (
                            <XCircle className="h-4 w-4 text-destructive" />
                          ) : (
                            <Clock className="h-4 w-4 text-muted-foreground" />
                          )}
                          <span className="font-medium">
                            {run.dry_run ? "Dry run" : "Production"} · {run.id.slice(0, 8)}
                          </span>
                        </span>
                        <span className="flex items-center gap-2 text-xs text-muted-foreground">
                          {run.row_count !== null && run.row_count !== undefined && (
                            <span>{run.row_count} rows</span>
                          )}
                          <span>{formatDate(run.created_at)}</span>
                          <Badge variant={runStatusVariant(run.status)}>{run.status}</Badge>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-border bg-card p-5">
              <h2 className="mb-4 font-semibold text-foreground">Run details</h2>
              {!selectedRun ? (
                <p className="text-sm text-muted-foreground">
                  Select a run to inspect its result and events.
                </p>
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant={runStatusVariant(selectedRun.status)}>
                      {selectedRun.status}
                    </Badge>
                    <span>{selectedRun.dry_run ? "Dry run" : "Production"}</span>
                    <span>Started {formatDate(selectedRun.started_at)}</span>
                    <span>Duration {formatDuration(selectedRun)}</span>
                  </div>

                  {selectedRun.error && (
                    <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                      {selectedRun.error}
                    </div>
                  )}

                  <RunResult run={selectedRun} />

                  <div>
                    <div className="mb-2 text-xs font-medium text-muted-foreground">
                      Logs and events
                    </div>
                    {events.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No events recorded yet.</p>
                    ) : (
                      <div className="max-h-72 space-y-2 overflow-auto rounded-lg border border-border bg-background/60 p-3 font-mono text-xs">
                        {events.map((event) => (
                          <div key={event.id} className="flex gap-2">
                            <span className="shrink-0 text-muted-foreground">
                              {event.created_at
                                ? new Date(event.created_at).toLocaleTimeString()
                                : ""}
                            </span>
                            <span
                              className={
                                event.level === "error"
                                  ? "text-destructive"
                                  : event.level === "warning"
                                    ? "text-amber-600 dark:text-amber-400"
                                    : "text-foreground"
                              }
                            >
                              {event.message}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

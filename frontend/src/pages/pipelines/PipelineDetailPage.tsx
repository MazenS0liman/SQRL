import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  ArrowUp,
  Brain,
  CheckCircle2,
  Filter,
  GitMerge,
  LayoutDashboard,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Sparkles,
  Upload,
  User,
  X,
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
import { DagCanvas } from "@/components/etl/DagCanvas";
import { InsertStageDialog, type StageEdge } from "@/components/etl/InsertStageDialog";
import { NodePropertiesPanel } from "@/components/etl/NodePropertiesPanel";
import { SourceToolbar } from "@/components/etl/SourceToolbar";
import {
  buildLayout,
  deriveTaskStates,
  eventStep,
  runStateOf,
  STATE_META,
  type OutputTarget,
} from "@/components/etl/dagUtils";
import { etlApi } from "@/api/etlApi";
import { apiFetch, cn } from "@/lib/utils";
import type {
  JobEventRead,
  Notebook,
  PipelineDefinition,
  PipelineRead,
  PipelineRunRead,
  PipelineStagePlanRead,
  PipelineStep,
  PlanConversationTurn,
  SourceSpec,
} from "@/types";

/* ---------- helpers ---------- */

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  issues?: string[];
  error?: boolean;
}

const STARTERS = [
  {
    title: "Filter + aggregate → dashboard",
    prompt:
      "Read etl_orders, keep only completed orders, aggregate total revenue by order_date into etl_daily_revenue, and show it on a dashboard.",
    output: "dashboard" as OutputTarget,
    icon: Filter,
  },
  {
    title: "Join → train a model",
    prompt:
      "Join etl_orders with etl_customers on customer_id, keep active customers, write to etl_customer_orders and train a churn model on it.",
    output: "ml" as OutputTarget,
    icon: GitMerge,
  },
  {
    title: "Clean + dashboard and model",
    prompt:
      "Read etl_events, drop null user_id, dedupe by event_id, cast created_at to timestamptz, then build a dashboard and train a model.",
    output: "both" as OutputTarget,
    icon: Upload,
  },
];

const OUTPUT_OPTIONS: { value: OutputTarget; label: string }[] = [
  { value: "none", label: "Data only" },
  { value: "dashboard", label: "Dashboard" },
  { value: "ml", label: "ML model" },
  { value: "both", label: "Both" },
];

const OUTPUT_PROMPT: Record<OutputTarget, string> = {
  none: "",
  dashboard: "\n\nDesired output: a dashboard on the final result.",
  ml: "\n\nDesired output: train an ML model on the final result.",
  both: "\n\nDesired output: both a dashboard and a trained ML model on the final result.",
};

function outputTarget(definition: PipelineDefinition): OutputTarget {
  const outputs = definition.outputs;
  return outputs
    ? outputs.dashboard && outputs.ml
      ? "both"
      : outputs.ml
        ? "ml"
        : outputs.dashboard
          ? "dashboard"
          : "none"
    : "none";
}

const makeId = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

function nextStepId(prefix: "source" | "transform" | "sink", definition: PipelineDefinition | null): string {
  const used = new Set((definition?.steps ?? []).map((step) => step.id));
  let index = 1;
  while (used.has(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
}

function defaultSinkTable(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 50);
  return `etl_${slug || "output"}`.slice(0, 63);
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function durationSeconds(run: PipelineRunRead): number | null {
  if (!run.started_at) return null;
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

function formatDuration(run: PipelineRunRead): string {
  const s = durationSeconds(run);
  if (s === null) return "—";
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function planIssues(plan: { validation: Record<string, unknown> }): string[] {
  const validation = plan.validation as Record<string, unknown>;
  const raw = validation?.errors ?? validation?.warnings;
  if (Array.isArray(raw)) return raw.map((entry) => String(entry));
  if (typeof raw === "string" && raw.trim()) return [raw];
  return [];
}

const runStatusVariant = (status: string): "default" | "secondary" | "destructive" | "outline" =>
  status === "failed" ? "destructive" : status === "succeeded" ? "default" : status === "running" ? "secondary" : "outline";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

type BottomTab = "logs" | "config" | "result" | "outputs";

/* ---------- page ---------- */

export default function PipelineDetailPage(): JSX.Element {
  const { pipelineId } = useParams();
  const isDraft = !pipelineId;
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [pipeline, setPipeline] = useState<PipelineRead | null>(null);
  const [draft, setDraft] = useState<PipelineDefinition | null>(null);
  const [name, setName] = useState(searchParams.get("name") ?? "");
  const [description, setDescription] = useState(searchParams.get("description") ?? "");
  const [output, setOutput] = useState<OutputTarget>("none");

  const [runs, setRuns] = useState<PipelineRunRead[]>([]);
  const [events, setEvents] = useState<JobEventRead[]>([]);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [notebookId, setNotebookId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [propertiesNodeId, setPropertiesNodeId] = useState<string | null>(null);
  const [stageEdge, setStageEdge] = useState<StageEdge | null>(null);
  const [tab, setTab] = useState<BottomTab>("logs");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [planning, setPlanning] = useState(false);

  const [loading, setLoading] = useState(!isDraft);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [running, setRunning] = useState<"dry" | "prod" | null>(null);
  const [materializing, setMaterializing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  /* load existing pipeline */
  const loadRuns = useCallback(async () => {
    if (!pipelineId) return [];
    const next = await etlApi.runs(pipelineId);
    setRuns(next);
    setSelectedRunId((cur) => (cur && next.some((r) => r.id === cur) ? cur : next[0]?.id ?? null));
    return next;
  }, [pipelineId]);

  useEffect(() => {
    if (!pipelineId) return;
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
        setDraft(current.definition);
        setOutput(outputTarget(current.definition));
        setRuns(nextRuns);
        setSelectedRunId(nextRuns[0]?.id ?? null);
        setNotebooks(nextNotebooks);
        setNotebookId(nextNotebooks[0]?.id ?? "");
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load pipeline.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  /* polling */
  const hasActiveRun = runs.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!hasActiveRun) return;
    const t = window.setInterval(() => void loadRuns().catch(() => undefined), 3000);
    return () => window.clearInterval(t);
  }, [hasActiveRun, loadRuns]);

  const selectedRun = useMemo(() => runs.find((r) => r.id === selectedRunId) ?? null, [runs, selectedRunId]);
  const selectedRunActive = selectedRun?.status === "queued" || selectedRun?.status === "running";

  useEffect(() => {
    if (!selectedRunId) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    const fetchEvents = () =>
      etlApi
        .events(selectedRunId)
        .then((e) => !cancelled && setEvents(e))
        .catch(() => undefined);
    void fetchEvents();
    if (!selectedRunActive) return () => { cancelled = true; };
    const t = window.setInterval(() => void fetchEvents(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [selectedRunId, selectedRunActive, selectedRun?.status]);

  /* graph + monitoring state */
  const layout = useMemo(() => buildLayout(draft, output), [draft, output]);
  const states = useMemo(() => deriveTaskStates(layout, selectedRun, events), [layout, selectedRun, events]);
  const realIds = useMemo(() => layout.nodes.filter((n) => !n.planned).map((n) => n.id), [layout]);
  const selectedNode = layout.nodes.find((n) => n.id === selectedNodeId) ?? null;
  const propertiesStep = useMemo(
    () => draft?.steps.find((step) => step.id === propertiesNodeId) ?? null,
    [draft, propertiesNodeId]
  );

  const visibleEvents = useMemo(
    () => (selectedNodeId ? events.filter((e) => eventStep(e, realIds) === selectedNodeId) : events),
    [events, selectedNodeId, realIds]
  );

  const definitionDirty = JSON.stringify(draft) !== JSON.stringify(pipeline?.definition ?? null);
  const metaDirty = !!pipeline && (name !== pipeline.name || description !== (pipeline.description ?? ""));
  const dirty = isDraft ? !!draft : definitionDirty || metaDirty;
  const canRunProduction = pipeline?.status === "approved" && !definitionDirty;

  const canMaterialize = runs.some((run) => {
    if (run.dry_run || run.status !== "succeeded") return false;
    return asRecord(asRecord(asRecord(run.result)?.sink))?.kind === "postgres" ||
      asRecord(asRecord(run.result)?.sink)?.kind === "postgres";
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, planning]);

  /* chat */
  const send = async (prompt?: string, forcedOutput?: OutputTarget) => {
    const query = (prompt ?? input).trim();
    if (!query || planning) return;
    const target = forcedOutput ?? output;
    if (forcedOutput) setOutput(forcedOutput);

    const history: PlanConversationTurn[] = messages
      .filter((m) => !m.error && m.content.trim())
      .slice(-19)
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((cur) => [...cur, { id: makeId("u"), role: "user", content: query }]);
    setInput("");
    setError(null);
    setPlanning(true);
    try {
      // `output_targets` is sent as a structured field AND appended to the prompt,
      // so the planner sees the intent even before the backend schema supports it.
      const request = {
        query: query + OUTPUT_PROMPT[target],
        history,
        previous_definition: draft ?? undefined,
        output_targets: target === "none" ? [] : target === "both" ? ["dashboard", "ml"] : [target],
      } as unknown as Parameters<typeof etlApi.plan>[0];
      const plan = await etlApi.plan(request);
      setDraft(plan.definition);
      setOutput(outputTarget(plan.definition));
      setSelectedNodeId(null);
      if (isDraft) {
        if (!name.trim()) setName(plan.name);
        if (!description.trim() && plan.description) setDescription(plan.description);
      }
      setMessages((cur) => [
        ...cur,
        {
          id: makeId("a"),
          role: "assistant",
          content:
            plan.reply ||
            plan.description ||
            `I updated the DAG: ${plan.definition.steps.length} tasks.`,
          issues: planIssues(plan),
        },
      ]);
    } catch (err) {
      setMessages((cur) => [
        ...cur,
        {
          id: makeId("e"),
          role: "assistant",
          error: true,
          content:
            err instanceof Error
              ? err.message
              : "I couldn't turn that into a DAG. Try naming the source table and columns.",
        },
      ]);
    } finally {
      setPlanning(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  /* actions */
  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      if (isDraft) {
        const created = await etlApi.create({
          name: name.trim() || "Untitled pipeline",
          description: description.trim() || null,
          definition: draft,
        });
        navigate(`/pipelines/${created.id}`, { replace: true });
      } else if (pipeline) {
        const updated = await etlApi.update(pipeline.id, {
          name: name.trim() || pipeline.name,
          description: description.trim() || null,
          ...(definitionDirty ? { definition: draft } : {}),
        });
        setPipeline(updated);
        setDraft(updated.definition);
        setNotice(
          definitionDirty ? "Saved. The DAG change reset approval and incremented the version." : "Saved."
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save pipeline.");
    } finally {
      setSaving(false);
    }
  };

  const approve = async () => {
    if (!pipeline) return;
    if (!window.confirm(`Approve "${pipeline.name}" (version ${pipeline.version}) for production runs?`)) return;
    setApproving(true);
    setError(null);
    try {
      setPipeline(await etlApi.approve(pipeline.id));
      setNotice("Pipeline approved for production runs.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to approve pipeline.");
    } finally {
      setApproving(false);
    }
  };

  const startRun = async (dryRun: boolean) => {
    if (!pipeline) return;
    if (!dryRun && !window.confirm(`Start a production run of "${pipeline.name}"? This writes to its destination.`)) return;
    setRunning(dryRun ? "dry" : "prod");
    setError(null);
    setNotice(null);
    try {
      const run = await etlApi.run(pipeline.id, { dry_run: dryRun });
      setRuns((items) => [run, ...items.filter((i) => i.id !== run.id)]);
      setSelectedRunId(run.id);
      setTab("logs");
      setNotice(dryRun ? "Dry run queued." : "Production run queued.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to queue a run.");
    } finally {
      setRunning(null);
    }
  };

  const materialize = async () => {
    if (!pipeline || !notebookId) return;
    setMaterializing(true);
    setError(null);
    try {
      const result = await etlApi.materialize(pipeline.id, { notebook_id: notebookId });
      const id = typeof result.notebook_id === "string" && result.notebook_id ? result.notebook_id : notebookId;
      navigate(`/notebooks/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to build the dashboard.");
    } finally {
      setMaterializing(false);
    }
  };

  /* graph editing */
  const openProperties = (id: string) => {
    if (!draft || id.startsWith("__")) return;
    setSelectedNodeId(id);
    setPropertiesNodeId(id);
  };

  const addSource = (source: SourceSpec) => {
    const definition = draft ?? { steps: [] };
    const id = nextStepId("source", definition);
    const steps: PipelineStep[] = [
      ...definition.steps,
      { id, type: "source", source: { ...source, columns: source.columns ?? [] } },
    ];

    // A pipeline needs exactly one sink; give a brand-new DAG a valid end so the
    // new source is immediately runnable, and let later sources be joined in.
    const hasSink = definition.steps.some((step) => step.type === "sink");
    if (!hasSink) {
      steps.push({
        id: nextStepId("sink", { ...definition, steps }),
        type: "sink",
        input: id,
        sink: { kind: "postgres", table: defaultSinkTable(name), mode: "replace" },
      });
    }

    setDraft({ ...definition, steps });
    setError(null);
    setNotice(
      hasSink
        ? `Added ${id}. Use the + on an edge to join it into the pipeline.`
        : `Added ${id} and a Postgres sink. Pick the destination table, then save.`
    );
    setSelectedNodeId(id);
    setPropertiesNodeId(id);
  };

  const updateStep = (next: PipelineStep) => {
    setDraft((current) =>
      current
        ? { ...current, steps: current.steps.map((step) => (step.id === next.id ? next : step)) }
        : current
    );
    setNotice(`Updated ${next.id}. Save to persist the change.`);
  };

  const deleteStep = (stepId: string) => {
    if (!draft) return;
    const target = draft.steps.find((step) => step.id === stepId);
    if (!target) return;

    const replacement = target.input ?? null;
    const dependents = draft.steps.filter(
      (step) => step.id !== stepId && (step.input === stepId || step.transform?.right === stepId)
    );

    if (dependents.length > 0 && !replacement) {
      setError(
        `${stepId} still feeds ${dependents.map((step) => step.id).join(", ")}. ` +
          "Delete or reconnect those steps first."
      );
      return;
    }

    // Splice the removed step out: everything that read from it now reads its
    // input, which keeps the DAG connected.
    const steps = draft.steps
      .filter((step) => step.id !== stepId)
      .map((step) => {
        let next = step;
        if (step.input === stepId) next = { ...next, input: replacement };
        if (step.transform?.right === stepId) {
          next = { ...next, transform: { ...step.transform, right: replacement } };
        }
        return next;
      });

    setDraft({ ...draft, steps });
    setSelectedNodeId((current) => (current === stepId ? null : current));
    setPropertiesNodeId(null);
    setNotice(`Removed ${stepId}. Save to persist the change.`);
  };

  const handleStagePlanned = (plan: PipelineStagePlanRead) => {
    setDraft(plan.definition);
    setOutput(outputTarget(plan.definition));
    setStageEdge(null);
    setSelectedNodeId(plan.steps[0]?.id ?? null);
    setTab("config");
    setNotice(plan.reply || `Inserted ${plan.steps.length} stage step(s). Save to persist.`);
    setMessages((current) => [
      ...current,
      {
        id: makeId("a"),
        role: "assistant",
        content: plan.reply || `Inserted ${plan.steps.length} stage step(s) into the DAG.`,
      },
    ]);
  };

  /* render */
  if (loading) {
    return (
      <main className="flex min-h-full items-center justify-center bg-background p-8 text-muted-foreground">
        <Loader2 className="mr-2 animate-spin" /> Loading pipeline...
      </main>
    );
  }
  if (!isDraft && !pipeline) {
    return (
      <main className="min-h-full bg-background px-6 py-10">
        <div className="mx-auto max-w-3xl rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {error ?? "Pipeline not found."}
        </div>
      </main>
    );
  }

  const bannerClass = "flex items-start justify-between gap-3 rounded-lg border p-2.5 text-sm";

  return (
    <main className="flex h-full min-h-0 flex-col bg-background">
      {/* toolbar */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button variant="ghost" size="icon" aria-label="Back to pipelines" onClick={() => navigate("/pipelines")}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0 flex-1 space-y-1">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Pipeline name"
              className="h-8 max-w-md border-transparent bg-transparent px-1 text-base font-semibold hover:border-border focus-visible:border-border"
            />
            <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-muted-foreground">
              {isDraft ? (
                <Badge variant="outline">Draft · not saved</Badge>
              ) : (
                <>
                  <Badge variant={pipeline!.status === "approved" ? "default" : "outline"}>
                    {pipeline!.status.replaceAll("_", " ")}
                  </Badge>
                  <span>v{pipeline!.version}</span>
                </>
              )}
              <span>{layout.nodes.filter((n) => !n.planned).length} tasks</span>
              {dirty && <Badge variant="outline">Unsaved changes</Badge>}
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Description (optional)"
                className="h-6 max-w-xs border-transparent bg-transparent px-1 text-xs hover:border-border"
              />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void save()} disabled={saving || !dirty || !draft}>
            {saving ? <Loader2 className="animate-spin" /> : <Save />} {isDraft ? "Create pipeline" : "Save"}
          </Button>
          {!isDraft && pipeline!.status !== "approved" && (
            <Button size="sm" variant="outline" onClick={() => void approve()} disabled={approving || definitionDirty}>
              {approving ? <Loader2 className="animate-spin" /> : <CheckCircle2 />} Approve
            </Button>
          )}
          {!isDraft && (
            <>
              <Button size="sm" variant="outline" onClick={() => void startRun(true)} disabled={running !== null || definitionDirty}
                title={definitionDirty ? "Save your DAG changes first." : undefined}>
                {running === "dry" ? <Loader2 className="animate-spin" /> : <Play />} Dry run
              </Button>
              <Button size="sm" onClick={() => void startRun(false)} disabled={!canRunProduction || running !== null}
                title={canRunProduction ? undefined : "Save and approve the current DAG before a production run."}>
                {running === "prod" ? <Loader2 className="animate-spin" /> : <Play />} Trigger DAG
              </Button>
            </>
          )}
        </div>
      </header>

      {(error || notice) && (
        <div className="space-y-2 px-4 pt-3">
          {error && (
            <div className={cn(bannerClass, "border-destructive/40 bg-destructive/10 text-destructive")}>
              <span className="flex items-start gap-2"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}</span>
              <button type="button" className="text-xs underline" onClick={() => setError(null)}>Dismiss</button>
            </div>
          )}
          {notice && (
            <div className={cn(bannerClass, "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300")}>
              <span className="flex items-start gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> {notice}</span>
              <button type="button" className="text-xs underline" onClick={() => setNotice(null)}>Dismiss</button>
            </div>
          )}
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* DAG + monitoring */}
        <section className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
          {!isDraft && (
            <div className="rounded-xl border border-border bg-card p-3">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-semibold text-foreground">DAG runs</h2>
                <Button size="sm" variant="ghost" onClick={() => void loadRuns().catch(() => undefined)}>
                  <RefreshCw /> Refresh
                </Button>
              </div>
              {runs.length === 0 ? (
                <p className="text-xs text-muted-foreground">No runs yet. Start with a dry run to validate the DAG.</p>
              ) : (
                <div className="flex items-end gap-1 overflow-x-auto pb-1">
                  {[...runs].slice(0, 30).reverse().map((run) => {
                    const secs = durationSeconds(run) ?? 0;
                    const max = Math.max(1, ...runs.slice(0, 30).map((r) => durationSeconds(r) ?? 0));
                    const h = 14 + Math.round((secs / max) * 34);
                    return (
                      <button
                        key={run.id}
                        type="button"
                        onClick={() => setSelectedRunId(run.id)}
                        title={`${run.dry_run ? "Dry run" : "Production"} · ${run.status} · ${formatDate(run.created_at)} · ${formatDuration(run)}`}
                        style={{ height: h }}
                        className={cn(
                          "w-4 shrink-0 rounded-sm transition",
                          STATE_META[runStateOf(run.status)].dot,
                          run.dry_run && "opacity-60",
                          run.id === selectedRunId && "ring-2 ring-primary ring-offset-1 ring-offset-card"
                        )}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          )}

          <div className="flex min-h-[300px] flex-1 items-stretch gap-3">
            <SourceToolbar onAdd={addSource} disabled={planning} />
            <DagCanvas
              layout={layout}
              states={states}
              selectedId={selectedNodeId}
              onSelect={setSelectedNodeId}
              onInsertStage={(from, to) => setStageEdge({ from, to })}
              onEditNode={openProperties}
              className="min-w-0 flex-1"
            />
          </div>

          {/* bottom panel */}
          <div className="rounded-xl border border-border bg-card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
              <div className="flex gap-1">
                {([
                  ["logs", "Logs"],
                  ["config", "Task config"],
                  ["result", "Run result"],
                  ["outputs", "Outputs"],
                ] as [BottomTab, string][]).map(([value, label]) => (
                  <Button key={value} size="sm" variant={tab === value ? "secondary" : "ghost"} onClick={() => setTab(value)}>
                    {label}
                  </Button>
                ))}
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                {selectedNode && (
                  <Badge variant="outline" className="gap-1 font-mono">
                    {selectedNode.label}
                    <button type="button" aria-label="Clear task filter" onClick={() => setSelectedNodeId(null)}>
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                )}
                {selectedRun && (
                  <>
                    <Badge variant={runStatusVariant(selectedRun.status)}>{selectedRun.status}</Badge>
                    <span>{selectedRun.dry_run ? "Dry run" : "Production"}</span>
                    <span>Started {formatDate(selectedRun.started_at)}</span>
                    <span>Duration {formatDuration(selectedRun)}</span>
                  </>
                )}
              </div>
            </div>

            <div className="max-h-64 overflow-auto p-3">
              {tab === "logs" && (
                <>
                  {selectedRun?.error && (
                    <div className="mb-2 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                      {selectedRun.error}
                    </div>
                  )}
                  {visibleEvents.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {selectedRun ? "No events for this selection yet." : "Run the DAG to see task logs here."}
                    </p>
                  ) : (
                    <div className="space-y-1.5 font-mono text-xs">
                      {visibleEvents.map((event) => (
                        <div key={event.id} className="flex gap-2">
                          <span className="shrink-0 text-muted-foreground">
                            {event.created_at ? new Date(event.created_at).toLocaleTimeString() : ""}
                          </span>
                          <span className={event.level === "error" ? "text-destructive" : event.level === "warning" ? "text-amber-600 dark:text-amber-400" : "text-foreground"}>
                            {event.message}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}

              {tab === "config" &&
                (selectedNode ? (
                  <pre className="rounded-md bg-muted p-2 font-mono text-[11px] leading-5">
                    {JSON.stringify(selectedNode.config, null, 2)}
                  </pre>
                ) : (
                  <p className="text-sm text-muted-foreground">Click a task in the graph to inspect its configuration.</p>
                ))}

              {tab === "result" &&
                (selectedRun?.result ? (
                  <pre className="rounded-md bg-muted p-2 font-mono text-[11px] leading-5">
                    {JSON.stringify(selectedRun.result, null, 2)}
                  </pre>
                ) : (
                  <p className="text-sm text-muted-foreground">Select a finished run to see its result.</p>
                ))}

              {tab === "outputs" && (
                <div className="space-y-3 text-sm">
                  <p className="text-xs text-muted-foreground">
                    Target: <span className="font-medium text-foreground">{OUTPUT_OPTIONS.find((o) => o.value === output)?.label}</span>.
                    Change it in the chat panel; planned outputs appear as dashed nodes in the graph.
                  </p>
                  {(output === "dashboard" || output === "both") && (
                    <div className="space-y-2 rounded-lg border border-border p-3">
                      <div className="flex items-center gap-2 text-xs font-medium"><LayoutDashboard className="h-4 w-4 text-primary" /> Dashboard</div>
                      {isDraft ? (
                        <p className="text-xs text-muted-foreground">Create the pipeline and complete a production run first.</p>
                      ) : notebooks.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          No notebooks available. <Link to="/notebooks" className="text-primary underline">Create one first</Link>.
                        </p>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <Select value={notebookId} onValueChange={setNotebookId}>
                            <SelectTrigger className="min-w-[220px] flex-1"><SelectValue placeholder="Select a notebook" /></SelectTrigger>
                            <SelectContent>
                              {notebooks.map((nb) => (<SelectItem key={nb.id} value={nb.id}>{nb.name}</SelectItem>))}
                            </SelectContent>
                          </Select>
                          <Button variant="outline" onClick={() => void materialize()} disabled={materializing || !notebookId || !canMaterialize}
                            title={canMaterialize ? undefined : "A successful production run with a Postgres sink is required."}>
                            {materializing ? <Loader2 className="animate-spin" /> : <Sparkles />} Build dashboard
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                  {(output === "ml" || output === "both") && (
                    <div className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">
                      <div className="mb-1 flex items-center gap-2 font-medium text-foreground"><Brain className="h-4 w-4 text-primary" /> ML model</div>
                      Recorded as a planned task. Model training needs a backend training step before it can execute.
                    </div>
                  )}
                  {output === "none" && <p className="text-xs text-muted-foreground">No dashboard or model requested.</p>}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* chat */}
        <aside className="flex min-h-[420px] min-w-0 flex-col rounded-xl border border-border bg-card/40 lg:min-h-0">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Sparkles className="h-4 w-4 text-primary" />
            <div>
              <h2 className="text-sm font-semibold text-foreground">Describe your DAG</h2>
              <p className="text-xs text-muted-foreground">Sources, transformations and the output you want.</p>
            </div>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {messages.length === 0 && !planning && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  {isDraft
                    ? "The canvas is empty. Tell me what the pipeline should do, or start from an example:"
                    : "Ask for changes to this DAG, e.g. “add a dedupe step before the sink”."}
                </p>
                {isDraft &&
                  STARTERS.map((s) => (
                    <button key={s.title} type="button" onClick={() => void send(s.prompt, s.output)}
                      className="w-full rounded-lg border border-border bg-card p-2.5 text-left transition-colors hover:border-primary/40 hover:bg-primary/5">
                      <div className="flex items-center gap-2 text-xs font-medium text-foreground"><s.icon className="h-3.5 w-3.5 text-primary" />{s.title}</div>
                      <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{s.prompt}</p>
                    </button>
                  ))}
              </div>
            )}

            {messages.map((m) => (
              <div key={m.id} className={cn("flex gap-2", m.role === "user" && "flex-row-reverse")}>
                <div className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-full", m.role === "user" ? "bg-secondary text-secondary-foreground" : "bg-primary-gradient text-primary-foreground")}>
                  {m.role === "user" ? <User className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                </div>
                <div className={cn("min-w-0 max-w-[85%] rounded-2xl px-3 py-2 text-sm shadow-sm",
                  m.role === "user" ? "rounded-tr-sm bg-primary text-primary-foreground"
                    : m.error ? "rounded-tl-sm border border-destructive/40 bg-destructive/10 text-destructive"
                    : "rounded-tl-sm border border-border bg-card text-foreground")}>
                  <div className="whitespace-pre-wrap break-words">{m.content}</div>
                  {m.issues && m.issues.length > 0 && (
                    <ul className="mt-2 list-disc space-y-0.5 border-t border-border pt-2 pl-4 text-xs text-destructive">
                      {m.issues.map((issue, i) => (<li key={i}>{issue}</li>))}
                    </ul>
                  )}
                </div>
              </div>
            ))}

            {planning && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Designing the DAG…
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="space-y-2 border-t border-border p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">Output:</span>
              {OUTPUT_OPTIONS.map((o) => (
                <button key={o.value} type="button" onClick={() => setOutput(o.value)}
                  className={cn("rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
                    output === o.value ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground hover:bg-secondary")}>
                  {o.label}
                </button>
              ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-2 focus-within:border-primary/50">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={3}
                disabled={planning}
                placeholder={isDraft ? "e.g. Read etl_orders, keep completed, aggregate revenue by day…" : "Describe a change to this DAG…"}
                className="min-h-[64px] resize-none border-0 bg-transparent text-sm shadow-none focus-visible:ring-0"
              />
              <div className="flex items-center justify-between px-1">
                <span className="text-[11px] text-muted-foreground">Enter to send · Shift+Enter for new line</span>
                <Button size="icon" onClick={() => void send()} disabled={!input.trim() || planning} aria-label="Send">
                  {planning ? <Loader2 className="animate-spin" /> : <ArrowUp />}
                </Button>
              </div>
            </div>
          </div>
        </aside>
      </div>

      <NodePropertiesPanel
        open={propertiesNodeId !== null}
        step={propertiesStep}
        definition={draft}
        onOpenChange={(open) => {
          if (!open) setPropertiesNodeId(null);
        }}
        onSave={updateStep}
        onDelete={deleteStep}
      />

      <InsertStageDialog
        open={stageEdge !== null}
        edge={stageEdge}
        definition={draft}
        onOpenChange={(open) => {
          if (!open) setStageEdge(null);
        }}
        onPlanned={handleStagePlanned}
      />
    </main>
  );
}
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  Loader2,
  Pencil,
  Play,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PipelinePlanCard } from "@/components/etl/PipelinePlanCard";
import { etlApi } from "@/api/etlApi";
import type { PipelineDefinition, PipelinePlanRead, PipelineRead } from "@/types";

const defaultDefinition: PipelineDefinition = {
  steps: [
    {
      id: "source",
      type: "source",
      source: { kind: "table", table_name: "etl_source" },
    },
    {
      id: "sink",
      type: "sink",
      input: "source",
      sink: { kind: "postgres", table: "etl_pipeline_output", mode: "replace" },
    },
  ],
};

const defaultDefinitionText = JSON.stringify(defaultDefinition, null, 2);

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "approved") return "default";
  if (status === "archived") return "secondary";
  return "outline";
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export default function PipelinesPage(): JSX.Element {
  const navigate = useNavigate();
  const [pipelines, setPipelines] = useState<PipelineRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Manual create
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [createDefinition, setCreateDefinition] = useState(defaultDefinitionText);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Natural-language planner
  const [planOpen, setPlanOpen] = useState(false);
  const [planQuery, setPlanQuery] = useState("");
  const [planConnectorId, setPlanConnectorId] = useState("");
  const [planConnectorTable, setPlanConnectorTable] = useState("");
  const [planSinkTable, setPlanSinkTable] = useState("");
  const [plan, setPlan] = useState<PipelinePlanRead | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [creatingFromPlan, setCreatingFromPlan] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      setPipelines(await etlApi.list());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load pipelines.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const openCreate = () => {
    setCreateName("");
    setCreateDescription("");
    setCreateDefinition(defaultDefinitionText);
    setCreateError(null);
    setCreateOpen(true);
  };

  const create = async () => {
    if (!createName.trim()) return;
    let definition: PipelineDefinition;
    try {
      definition = JSON.parse(createDefinition) as PipelineDefinition;
    } catch {
      setCreateError("The pipeline definition must be valid JSON.");
      return;
    }
    if (!definition || !Array.isArray(definition.steps) || definition.steps.length === 0) {
      setCreateError("The definition must contain a non-empty 'steps' array.");
      return;
    }

    setCreating(true);
    setCreateError(null);
    try {
      const pipeline = await etlApi.create({
        name: createName.trim(),
        description: createDescription.trim() || null,
        definition,
      });
      setCreateOpen(false);
      navigate(`/pipelines/${pipeline.id}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Unable to create pipeline.");
    } finally {
      setCreating(false);
    }
  };

  const openPlan = () => {
    setPlanQuery("");
    setPlanConnectorId("");
    setPlanConnectorTable("");
    setPlanSinkTable("");
    setPlan(null);
    setPlanError(null);
    setPlanOpen(true);
  };

  const generatePlan = async () => {
    if (!planQuery.trim()) return;
    setPlanning(true);
    setPlanError(null);
    try {
      setPlan(
        await etlApi.plan({
          query: planQuery.trim(),
          connector_id: planConnectorId.trim() || undefined,
          table: planConnectorTable.trim() || undefined,
          sink_table: planSinkTable.trim() || undefined,
        })
      );
    } catch (err) {
      setPlan(null);
      setPlanError(err instanceof Error ? err.message : "Unable to plan pipeline.");
    } finally {
      setPlanning(false);
    }
  };

  const createFromPlan = async () => {
    if (!plan) return;
    setCreatingFromPlan(true);
    setPlanError(null);
    try {
      const pipeline = await etlApi.create({
        name: plan.name,
        description: plan.description,
        definition: plan.definition,
      });
      setPlanOpen(false);
      navigate(`/pipelines/${pipeline.id}`);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Unable to create pipeline.");
    } finally {
      setCreatingFromPlan(false);
    }
  };

  const approve = async (pipeline: PipelineRead) => {
    if (!window.confirm(`Approve "${pipeline.name}" (version ${pipeline.version}) for production runs?`)) {
      return;
    }
    setBusyId(pipeline.id);
    try {
      const updated = await etlApi.approve(pipeline.id);
      setPipelines((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to approve pipeline.");
    } finally {
      setBusyId(null);
    }
  };

  const dryRun = async (pipeline: PipelineRead) => {
    setBusyId(pipeline.id);
    try {
      await etlApi.run(pipeline.id, { dry_run: true });
      navigate(`/pipelines/${pipeline.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to queue a dry run.");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (pipeline: PipelineRead) => {
    if (!window.confirm(`Delete "${pipeline.name}" and its run history?`)) return;
    setBusyId(pipeline.id);
    try {
      await etlApi.remove(pipeline.id);
      setPipelines((items) => items.filter((item) => item.id !== pipeline.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to delete pipeline.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="min-h-full bg-background px-6 py-10">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
          <div>
            <h1 className="bg-primary-gradient bg-clip-text text-3xl font-semibold text-transparent">
              Pipelines
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Build, approve, and monitor safe declarative ETL workflows.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={openPlan}>
              <Sparkles /> Plan with AI
            </Button>
            <Button onClick={openCreate}>
              <Plus /> New pipeline
            </Button>
          </div>
        </header>

        {error && (
          <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            <span>{error}</span>
            <button
              type="button"
              className="text-xs underline"
              onClick={() => setError(null)}
            >
              Dismiss
            </button>
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="animate-spin" /> Loading pipelines...
          </div>
        ) : (
          <div className="grid gap-4">
            {pipelines.length === 0 && (
              <div className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground">
                No pipelines yet. Create one manually, or describe what you want and let the
                ETL planner draft it.
              </div>
            )}
            {pipelines.map((pipeline) => {
              const isBusy = busyId === pipeline.id;
              return (
                <article
                  key={pipeline.id}
                  className="rounded-xl border border-border bg-card p-5 shadow-sm"
                >
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0">
                      <Link
                        to={`/pipelines/${pipeline.id}`}
                        className="text-lg font-semibold hover:text-primary"
                      >
                        {pipeline.name}
                      </Link>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {pipeline.description || "No description"}
                      </p>
                      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant={statusVariant(pipeline.status)}>
                          {pipeline.status.replaceAll("_", " ")}
                        </Badge>
                        <span>
                          {pipeline.definition.steps.length} step
                          {pipeline.definition.steps.length === 1 ? "" : "s"}
                        </span>
                        <span>v{pipeline.version}</span>
                        <span>Updated {formatDate(pipeline.updated_at)}</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => navigate(`/pipelines/${pipeline.id}`)}
                      >
                        <Pencil /> Open
                      </Button>
                      {pipeline.status !== "approved" && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isBusy}
                          onClick={() => void approve(pipeline)}
                        >
                          <CheckCircle2 /> Approve
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={isBusy}
                        onClick={() => void dryRun(pipeline)}
                      >
                        {isBusy ? <Loader2 className="animate-spin" /> : <Play />} Dry run
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={isBusy}
                        onClick={() => void remove(pipeline)}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {/* Manual create */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>New pipeline</DialogTitle>
            <DialogDescription>
              Define a constrained DAG of source, transform, and sink steps. The definition is
              validated before it is stored, and a production run requires approval.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="pipeline-name" className="text-sm font-medium">
                Name
              </label>
              <Input
                id="pipeline-name"
                value={createName}
                onChange={(event) => setCreateName(event.target.value)}
                placeholder="Daily customer revenue"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="pipeline-description" className="text-sm font-medium">
                Description
              </label>
              <Textarea
                id="pipeline-description"
                value={createDescription}
                onChange={(event) => setCreateDescription(event.target.value)}
                placeholder="Optional summary of what this pipeline produces"
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="pipeline-definition" className="text-sm font-medium">
                Definition (JSON)
              </label>
              <Textarea
                id="pipeline-definition"
                value={createDefinition}
                onChange={(event) => setCreateDefinition(event.target.value)}
                className="min-h-[280px] font-mono text-xs"
                spellCheck={false}
              />
              <p className="text-xs text-muted-foreground">
                Source tables must be allow-listed or start with the configured ETL source
                prefix. Postgres sinks must start with the configured sink prefix.
              </p>
            </div>

            {createError && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {createError}
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void create()} disabled={creating || !createName.trim()}>
              {creating ? <Loader2 className="animate-spin" /> : <Plus />}
              {creating ? "Creating..." : "Create pipeline"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Planner */}
      <Dialog open={planOpen} onOpenChange={setPlanOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Plan with AI</DialogTitle>
            <DialogDescription>
              Describe the pipeline you want. The planner returns a validated proposal; nothing
              is stored or executed until you create and approve it.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="plan-query" className="text-sm font-medium">
                What should the pipeline do?
              </label>
              <Textarea
                id="plan-query"
                value={planQuery}
                onChange={(event) => setPlanQuery(event.target.value)}
                placeholder="Read etl_orders, keep completed orders, aggregate revenue by day, and write to etl_daily_revenue."
                className="min-h-[110px]"
              />
              <p className="text-xs text-muted-foreground">
                Name the source table and columns in your request, or supply connector details
                below. File sources can be planned from chat where you attach files.
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <label htmlFor="plan-connector" className="text-sm font-medium">
                  Connector ID (optional)
                </label>
                <Input
                  id="plan-connector"
                  value={planConnectorId}
                  onChange={(event) => setPlanConnectorId(event.target.value)}
                  placeholder="connector_123"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="plan-table" className="text-sm font-medium">
                  Connector table (optional)
                </label>
                <Input
                  id="plan-table"
                  value={planConnectorTable}
                  onChange={(event) => setPlanConnectorTable(event.target.value)}
                  placeholder="public.orders"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="plan-sink" className="text-sm font-medium">
                Destination table (optional)
              </label>
              <Input
                id="plan-sink"
                value={planSinkTable}
                onChange={(event) => setPlanSinkTable(event.target.value)}
                placeholder="etl_daily_revenue"
              />
            </div>

            {planError && !plan && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                {planError}
              </div>
            )}

            {plan && (
              <PipelinePlanCard
                plan={plan}
                onCreate={() => void createFromPlan()}
                creating={creatingFromPlan}
                error={planError}
              />
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setPlanOpen(false)}>
              Close
            </Button>
            <Button onClick={() => void generatePlan()} disabled={planning || !planQuery.trim()}>
              {planning ? <Loader2 className="animate-spin" /> : <Sparkles />}
              {planning ? "Planning..." : plan ? "Regenerate plan" : "Generate plan"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}

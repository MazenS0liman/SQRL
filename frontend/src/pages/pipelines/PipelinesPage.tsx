import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, Pencil, Play, Plus, Trash2, Workflow } from "lucide-react";
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
import { etlApi } from "@/api/etlApi";
import type { PipelineRead } from "@/types";

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

  // New pipeline: name only, then straight into an empty DAG workspace.
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");

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
    setCreateOpen(true);
  };

  const startNew = () => {
    const params = new URLSearchParams();
    if (createName.trim()) params.set("name", createName.trim());
    if (createDescription.trim()) params.set("description", createDescription.trim());
    setCreateOpen(false);
    navigate(`/pipelines/new?${params.toString()}`);
  };

  const approve = async (pipeline: PipelineRead) => {
    if (!window.confirm(`Approve "${pipeline.name}" (version ${pipeline.version}) for production runs?`)) return;
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
              Design DAGs by chatting, then monitor every run task by task.
            </p>
          </div>
          <Button onClick={openCreate}>
            <Plus /> New pipeline
          </Button>
        </header>

        {error && (
          <div className="mb-4 flex items-start justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            <span>{error}</span>
            <button type="button" className="text-xs underline" onClick={() => setError(null)}>
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
                <Workflow className="mx-auto mb-3 h-8 w-8" />
                No pipelines yet. Create one to open an empty DAG and describe it in the chat.
              </div>
            )}
            {pipelines.map((pipeline) => {
              const isBusy = busyId === pipeline.id;
              return (
                <article key={pipeline.id} className="rounded-xl border border-border bg-card p-5 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0">
                      <Link to={`/pipelines/${pipeline.id}`} className="text-lg font-semibold hover:text-primary">
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
                          {pipeline.definition.steps.length} task
                          {pipeline.definition.steps.length === 1 ? "" : "s"}
                        </span>
                        <span>v{pipeline.version}</span>
                        <span>Updated {formatDate(pipeline.updated_at)}</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => navigate(`/pipelines/${pipeline.id}`)}>
                        <Pencil /> Open DAG
                      </Button>
                      {pipeline.status !== "approved" && (
                        <Button size="sm" variant="outline" disabled={isBusy} onClick={() => void approve(pipeline)}>
                          <CheckCircle2 /> Approve
                        </Button>
                      )}
                      <Button size="sm" variant="outline" disabled={isBusy} onClick={() => void dryRun(pipeline)}>
                        {isBusy ? <Loader2 className="animate-spin" /> : <Play />} Dry run
                      </Button>
                      <Button size="sm" variant="destructive" disabled={isBusy} onClick={() => void remove(pipeline)}>
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

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>New pipeline</DialogTitle>
            <DialogDescription>
              You&apos;ll land on an empty DAG. Describe the steps in the chat and they appear as a
              graph. Nothing is stored until you create the pipeline there.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="pipeline-name" className="text-sm font-medium">Name</label>
              <Input
                id="pipeline-name"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="Daily customer revenue"
                onKeyDown={(e) => e.key === "Enter" && startNew()}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="pipeline-description" className="text-sm font-medium">Description</label>
              <Textarea
                id="pipeline-description"
                value={createDescription}
                onChange={(e) => setCreateDescription(e.target.value)}
                placeholder="Optional summary"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button onClick={startNew}><Workflow /> Open empty DAG</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}
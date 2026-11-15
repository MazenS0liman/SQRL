import { AlertCircle, CheckCircle2, Loader2, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PipelineStepList } from "@/components/etl/PipelineStepList";
import type { PipelinePlanRead } from "@/types";

interface PipelinePlanCardProps {
  plan: PipelinePlanRead;
  onCreate?: () => void;
  creating?: boolean;
  createLabel?: string;
  error?: string | null;
  className?: string;
}

function validationErrors(validation: Record<string, unknown>): string[] {
  const raw = validation.errors ?? validation.warnings;
  if (Array.isArray(raw)) {
    return raw.map((entry) => String(entry));
  }
  if (typeof raw === "string" && raw.trim()) {
    return [raw];
  }
  return [];
}

function describeSink(validation: Record<string, unknown>): string | null {
  const sink = validation.sink;
  if (!sink || typeof sink !== "object") return null;
  const record = sink as Record<string, unknown>;
  if (record.kind === "postgres" && typeof record.table === "string") {
    const mode = typeof record.mode === "string" ? ` (${record.mode})` : "";
    return `PostgreSQL · ${record.table}${mode}`;
  }
  if (record.kind === "minio" && typeof record.object_key === "string") {
    return `MinIO · ${record.object_key}`;
  }
  return null;
}

/**
 * Renders a validated ETL planner proposal. The plan is never executed here:
 * the user reviews the DAG and explicitly creates a pipeline, which still has
 * to be approved before its first production run.
 */
export function PipelinePlanCard({
  plan,
  onCreate,
  creating = false,
  createLabel = "Create pipeline",
  error,
  className,
}: PipelinePlanCardProps): JSX.Element {
  const errors = validationErrors(plan.validation);
  const schemaChecked = plan.validation.schema_checked === true;
  const isValid = plan.validation.valid !== false && errors.length === 0;
  const sinkLabel = describeSink(plan.validation);

  return (
    <div className={className}>
      <div className="rounded-xl border border-primary/30 bg-primary/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-foreground">{plan.name}</h3>
              <Badge variant="secondary">Planner proposal</Badge>
              {isValid ? (
                <Badge variant="outline">
                  <CheckCircle2 className="mr-1 h-3 w-3" /> Validated
                </Badge>
              ) : (
                <Badge variant="destructive">
                  <AlertCircle className="mr-1 h-3 w-3" /> Invalid
                </Badge>
              )}
              {schemaChecked && <Badge variant="outline">Schema checked</Badge>}
            </div>
            {plan.description && (
              <p className="mt-1 text-xs text-muted-foreground">{plan.description}</p>
            )}
            {sinkLabel && (
              <p className="mt-1 text-xs text-muted-foreground">
                Destination: <span className="font-mono text-foreground">{sinkLabel}</span>
              </p>
            )}
          </div>
          {onCreate && (
            <Button size="sm" onClick={onCreate} disabled={creating}>
              {creating ? <Loader2 className="animate-spin" /> : <Plus />}
              {creating ? "Creating..." : createLabel}
            </Button>
          )}
        </div>

        {errors.length > 0 && (
          <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
            <div className="flex items-center gap-2 font-semibold">
              <AlertCircle className="h-3.5 w-3.5" /> Validation issues
            </div>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {errors.map((entry, index) => (
                <li key={index}>{entry}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-3">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-widest text-primary">
            Proposed DAG · {plan.definition.steps.length}{" "}
            {plan.definition.steps.length === 1 ? "step" : "steps"}
          </div>
          <PipelineStepList steps={plan.definition.steps} compact />
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

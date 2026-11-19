import { ArrowRight, Database, Upload, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { PipelineStep, SourceSpec, SinkSpec, TransformSpec } from "@/types";

type StepType = "source" | "transform" | "sink";

const STEP_ICON: Record<StepType, typeof Database> = {
  source: Database,
  transform: Wand2,
  sink: Upload,
};

const STEP_LABEL: Record<StepType, string> = {
  source: "Source",
  transform: "Transform",
  sink: "Sink",
};

function describeSource(source: SourceSpec): string {
  switch (source.kind) {
    case "connector":
      return `Connector ${source.connector_id ?? "?"} · ${source.table ?? "?"}`;
    case "file":
      return `File ${source.file_url ?? "?"}`;
    case "table":
      return `Table ${source.table_name ?? "?"}`;
    default:
      return "Unknown source";
  }
}

function describeTransform(transform: TransformSpec): string {
  switch (transform.op) {
    case "select":
      return `Select ${transform.columns?.join(", ") || "columns"}`;
    case "filter": {
      const values = transform.values?.length
        ? `[${transform.values.map((value) => String(value)).join(", ")}]`
        : transform.value !== undefined && transform.value !== null
          ? String(transform.value)
          : "";
      return `Filter ${transform.column ?? "?"} ${transform.comparison ?? "?"}${
        values ? ` ${values}` : ""
      }`;
    }
    case "rename": {
      const mapping = Object.entries(transform.mapping ?? {})
        .map(([from, to]) => `${from} → ${to}`)
        .join(", ");
      return `Rename ${mapping || "columns"}`;
    }
    case "cast": {
      const casts = Object.entries(transform.casts ?? {})
        .map(([column, type]) => `${column} → ${type}`)
        .join(", ");
      return `Cast ${casts || "columns"}`;
    }
    case "dedupe":
      return transform.columns?.length
        ? `Dedupe on ${transform.columns.join(", ")}`
        : "Dedupe all rows";
    case "limit":
      return `Limit to ${transform.count ?? "?"} rows`;
    case "aggregate": {
      const aggregations = (transform.aggregations ?? [])
        .map((aggregation) =>
          `${aggregation.function}(${aggregation.column ?? "*"}) as ${aggregation.alias}`
        )
        .join(", ");
      const groupBy = transform.group_by?.length
        ? ` grouped by ${transform.group_by.join(", ")}`
        : "";
      return `Aggregate ${aggregations || "values"}${groupBy}`;
    }
    case "join": {
      const keys = (transform.on ?? [])
        .map((key) => `${key.left} = ${key.right}`)
        .join(", ");
      return `Join ${transform.right ?? "?"} (${transform.how ?? "inner"}) on ${keys || "keys"}`;
    }
    default:
      return transform.op;
  }
}

function describeSink(sink: SinkSpec): string {
  if (sink.kind === "postgres") {
    return `PostgreSQL · ${sink.table ?? "?"} · ${sink.mode ?? "replace"}`;
  }
  if (sink.kind === "minio") {
    return `MinIO · ${sink.object_key ?? "?"} · ${sink.format ?? "csv"}`;
  }
  return "Unknown sink";
}

export function describePipelineStep(step: PipelineStep): string {
  if (step.source) return describeSource(step.source);
  if (step.transform) return describeTransform(step.transform);
  if (step.sink) return describeSink(step.sink);
  return step.type;
}

export function stepTypeLabel(step: PipelineStep): string {
  return STEP_LABEL[step.type as StepType] ?? step.type;
}

interface PipelineStepListProps {
  steps: PipelineStep[];
  className?: string;
  compact?: boolean;
}

export function PipelineStepList({
  steps,
  className,
  compact = false,
}: PipelineStepListProps): JSX.Element {
  return (
    <ol className={cn("space-y-2", className)}>
      {steps.map((step, index) => {
        const type: StepType =
          step.type === "transform" ? "transform" : step.type === "sink" ? "sink" : "source";
        const Icon = STEP_ICON[type];
        return (
          <li
            key={`${step.id}-${index}`}
            className={cn(
              "rounded-lg border border-border bg-background/60",
              compact ? "px-3 py-2" : "px-4 py-3"
            )}
          >
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-foreground">
                    {index + 1}. {step.id}
                  </span>
                  <Badge variant={type === "sink" ? "default" : "secondary"}>
                    {STEP_LABEL[type]}
                  </Badge>
                  {step.input && (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <ArrowRight className="h-3 w-3" /> {step.input}
                    </span>
                  )}
                </div>
                <p className="mt-1 break-words text-xs text-muted-foreground">
                  {describePipelineStep(step)}
                </p>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

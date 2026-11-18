import type { ReactNode } from "react";
import { Activity, Database, ListChecks, Target } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TrainingLossChart } from "@/components/models/TrainingLossChart";
import type { ModelDetails } from "@/types";

export interface ModelMetricDisplay {
  label: string;
  value: string;
}

export interface ModelDetailsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  subtitle?: string | null;
  details: ModelDetails | null | undefined;
  metrics?: ModelMetricDisplay[];
  footer?: ReactNode;
}

/**
 * Shared model card shown from Workspace, Models, and Pipeline Outputs.
 *
 * Renders the same three things everywhere: the expected input schema, the
 * preprocessing the model assumes, and its training/loss curve.
 */
export function ModelDetailsDialog({
  open,
  onOpenChange,
  title,
  subtitle,
  details,
  metrics = [],
  footer,
}: ModelDetailsDialogProps) {
  const inputSchema = details?.input_schema ?? [];
  const preprocessing = details?.preprocessing ?? [];
  const history = details?.training_history ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 pr-6">
            <Activity className="h-4 w-4 text-primary" />
            <span className="truncate">{title}</span>
          </DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
        </DialogHeader>

        <div className="space-y-5">
          <div className="flex flex-wrap gap-1.5">
            {details?.task_type && <Badge label="Task" value={details.task_type} />}
            {details?.algorithm && <Badge label="Algorithm" value={details.algorithm} />}
            {details?.target && <Badge label="Target" value={details.target} />}
            {details?.refit && <Badge label="Version" value="retrained" />}
          </div>

          {metrics.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {metrics.map((metric, index) => (
                <span
                  key={`${metric.label}-${index}`}
                  className="rounded border border-border bg-secondary px-2 py-1 font-mono text-[11px] text-foreground"
                >
                  {metric.label}: {metric.value}
                </span>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Stat icon={<Database className="h-3.5 w-3.5" />} label="Input features" value={details?.n_features ?? inputSchema.length} />
            <Stat icon={<ListChecks className="h-3.5 w-3.5" />} label="Training rows" value={details?.n_samples ?? "—"} />
            <Stat icon={<Target className="h-3.5 w-3.5" />} label="Target" value={details?.target || "—"} />
          </div>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Training curve
            </h3>
            {history && history.series?.length > 0 ? (
              <div className="rounded-lg border border-border bg-card p-2">
                <TrainingLossChart history={history} />
                <p className="mt-1 px-1 text-[11px] text-muted-foreground">
                  {history.kind === "learning_curve"
                    ? "Held-out error as the training set grows."
                    : history.kind === "elbow"
                      ? "Inertia across candidate cluster counts."
                      : "Per-epoch loss recorded during training."}
                </p>
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-border p-3 text-sm text-muted-foreground">
                No training curve was recorded for this model. Metrics below are still
                the values measured when it was built.
              </p>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Expected input
            </h3>
            {inputSchema.length > 0 ? (
              <div className="max-h-64 overflow-auto rounded-lg border border-border">
                <table className="min-w-full text-left text-sm">
                  <thead className="sticky top-0 bg-secondary font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">Column</th>
                      <th className="px-3 py-2 font-medium">Type</th>
                      <th className="px-3 py-2 font-medium">Example</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inputSchema.map((field, index) => (
                      <tr key={`${field.name}-${index}`} className="border-t border-border">
                        <td className="px-3 py-1.5 font-mono text-foreground">{field.name}</td>
                        <td className="px-3 py-1.5 font-mono text-muted-foreground">{field.dtype || "—"}</td>
                        <td className="max-w-[220px] truncate px-3 py-1.5 font-mono text-muted-foreground">
                          {field.example == null ? "—" : String(field.example)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No input schema was captured for this model.
              </p>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Required preprocessing
            </h3>
            {preprocessing.length > 0 ? (
              <ol className="list-inside list-decimal space-y-1 rounded-lg border border-border bg-secondary/30 p-3 text-sm text-muted-foreground">
                {preprocessing.map((step, index) => (
                  <li key={`${step}-${index}`}>{step}</li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">
                No preprocessing notes were captured. The model expects the same
                numeric feature matrix it was trained on.
              </p>
            )}
          </section>

          {footer && <div className="border-t border-border pt-3">{footer}</div>}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Badge({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border bg-secondary px-2 py-1 text-[11px] text-foreground">
      <span className="font-mono uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}

function Stat({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-sm text-foreground">{value}</div>
    </div>
  );
}

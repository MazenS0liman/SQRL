import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Brain, Loader2, Save, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { etlApi } from "@/api/etlApi";
import type { MLModelRead, MLSpec, OutputsSpec, PipelineDefinition } from "@/types";

const MODE_LABELS: Record<MLSpec["mode"], string> = {
  train: "Train a new model",
  retrain: "Retrain an existing model",
  inference: "Inference only (score rows)",
};

const TASK_LABELS: Record<MLSpec["task"], string> = {
  classification: "Classification",
  regression: "Regression",
  clustering: "Clustering",
};

function defaultSpec(): MLSpec {
  return {
    mode: "train",
    task: "classification",
    target: "",
    features: [],
    algorithm: "auto",
    test_size: 0.2,
    n_clusters: 3,
    model_id: null,
  };
}

function splitFeatures(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export interface MLPropertiesPanelProps {
  open: boolean;
  definition: PipelineDefinition | null;
  pipelineId?: string | null;
  onOpenChange: (open: boolean) => void;
  /** `null` removes the ML output entirely. */
  onSave: (outputs: OutputsSpec | null) => void;
}

/**
 * Output properties for the `__ml` node: train a fresh model, refit a
 * registered one, or only score new rows with an existing model.
 */
export function MLPropertiesPanel({
  open,
  definition,
  pipelineId,
  onOpenChange,
  onSave,
}: MLPropertiesPanelProps): JSX.Element {
  const [spec, setSpec] = useState<MLSpec>(defaultSpec());
  const [featuresText, setFeaturesText] = useState("");
  const [models, setModels] = useState<MLModelRead[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const existing = definition?.outputs?.ml;
    const next = existing ? { ...defaultSpec(), ...existing } : defaultSpec();
    setSpec(next);
    setFeaturesText((next.features ?? []).join(", "));
    setModelsError(null);
  }, [open, definition]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setModelsLoading(true);
    void etlApi
      .models(pipelineId ?? undefined)
      .then((rows) => {
        if (cancelled) return;
        setModels(rows);
        // A retrain/inference output with no model yet: pick the newest.
        setSpec((current) =>
          current.mode !== "train" && !current.model_id && rows.length > 0
            ? { ...current, model_id: rows[0].id }
            : current
        );
      })
      .catch((err) => {
        if (!cancelled) setModelsError(err instanceof Error ? err.message : "Could not load models.");
      })
      .finally(() => !cancelled && setModelsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, pipelineId]);

  const selectedModel = useMemo(
    () => models.find((model) => model.id === spec.model_id) ?? null,
    [models, spec.model_id]
  );

  const needsModel = spec.mode === "retrain" || spec.mode === "inference";
  const canSave = !needsModel || Boolean(spec.model_id);

  const save = () => {
    const next: MLSpec = {
      ...spec,
      features: splitFeatures(featuresText),
      target: spec.target?.trim() || null,
      model_id: needsModel ? spec.model_id ?? null : null,
    };
    const current = definition?.outputs ?? {};
    onSave({ ...current, ml: next });
    onOpenChange(false);
  };

  const remove = () => {
    const current = definition?.outputs ?? {};
    const dashboard = Boolean(current.dashboard);
    onSave(dashboard ? { dashboard } : null);
    onOpenChange(false);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-lg">
        <SheetHeader className="border-b border-border px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
              <Brain className="h-5 w-5 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <SheetTitle className="flex items-center gap-2 text-base">
                ML output <Badge variant="outline">__ml</Badge>
              </SheetTitle>
              <SheetDescription>
                Choose whether this pipeline trains a new model, retrains a registered one, or only scores rows.
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {modelsError && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {modelsError}
            </div>
          )}

          <div className="space-y-2">
            <Label>What should the run do?</Label>
            <div className="grid gap-2">
              {(Object.keys(MODE_LABELS) as MLSpec["mode"][]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setSpec((current) => ({ ...current, mode }))}
                  className={`rounded-lg border p-3 text-left text-xs transition-colors ${
                    spec.mode === mode
                      ? "border-primary bg-primary/5 text-foreground"
                      : "border-border text-muted-foreground hover:bg-secondary"
                  }`}
                >
                  <span className="font-medium">{MODE_LABELS[mode]}</span>
                  <span className="mt-0.5 block text-[11px]">
                    {mode === "train"
                      ? "Fit a new model on this run's output rows."
                      : mode === "retrain"
                        ? "Load a registered model and refit it on the new rows."
                        : "Score the new rows with a registered model; nothing is retrained."}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {needsModel && (
            <div className="space-y-2">
              <Label htmlFor="ml-model">Registered model</Label>
              <Select
                value={spec.model_id ?? ""}
                onValueChange={(value) => setSpec((current) => ({ ...current, model_id: value }))}
                disabled={modelsLoading}
              >
                <SelectTrigger id="ml-model">
                  <SelectValue placeholder={modelsLoading ? "Loading models…" : "Choose a model"} />
                </SelectTrigger>
                <SelectContent>
                  {models.map((model) => (
                    <SelectItem key={model.id} value={model.id}>
                      {model.name} · {model.task}
                      {model.target ? ` · ${model.target}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {modelsLoading ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading registered models…
                </p>
              ) : models.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No models registered yet. Run this pipeline once in “Train a new model” mode first.
                </p>
              ) : selectedModel ? (
                <p className="text-xs text-muted-foreground">
                  Target <code className="font-mono">{selectedModel.target ?? "—"}</code> ·{" "}
                  {selectedModel.features.length} feature(s)
                </p>
              ) : null}
            </div>
          )}

          <div className="space-y-2">
            <Label>Task</Label>
            <Select
              value={spec.task}
              onValueChange={(value) => setSpec((current) => ({ ...current, task: value as MLSpec["task"] }))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(TASK_LABELS) as MLSpec["task"][]).map((task) => (
                  <SelectItem key={task} value={task}>
                    {TASK_LABELS[task]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {spec.task !== "clustering" && (
            <div className="space-y-2">
              <Label htmlFor="ml-target">Target column</Label>
              <Input
                id="ml-target"
                value={spec.target ?? ""}
                onChange={(event) => setSpec((current) => ({ ...current, target: event.target.value }))}
                placeholder={spec.mode === "inference" ? "Optional for inference" : "e.g. churned"}
                className="font-mono"
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="ml-features">Features</Label>
            <Input
              id="ml-features"
              value={featuresText}
              onChange={(event) => setFeaturesText(event.target.value)}
              placeholder="comma-separated columns (empty = all other columns)"
              className="font-mono"
            />
            <p className="text-xs text-muted-foreground">
              Leave empty to use every column except the target. Inference can reuse the model's
              stored features.
            </p>
          </div>

          {spec.mode === "train" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Algorithm</Label>
                <Select
                  value={spec.algorithm ?? "auto"}
                  onValueChange={(value) => setSpec((current) => ({ ...current, algorithm: value as MLSpec["algorithm"] }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Auto</SelectItem>
                    <SelectItem value="random_forest">Random forest</SelectItem>
                    <SelectItem value="gradient_boosting">Gradient boosting</SelectItem>
                    <SelectItem value="linear">Linear</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {spec.task === "clustering" ? (
                <div className="space-y-2">
                  <Label htmlFor="ml-clusters">Clusters</Label>
                  <Input
                    id="ml-clusters"
                    type="number"
                    min={2}
                    max={20}
                    value={spec.n_clusters ?? 3}
                    onChange={(event) =>
                      setSpec((current) => ({ ...current, n_clusters: Number(event.target.value) }))
                    }
                  />
                </div>
              ) : (
                <div className="space-y-2">
                  <Label htmlFor="ml-test-size">Test size</Label>
                  <Input
                    id="ml-test-size"
                    type="number"
                    min={0.1}
                    max={0.4}
                    step={0.05}
                    value={spec.test_size ?? 0.2}
                    onChange={(event) =>
                      setSpec((current) => ({ ...current, test_size: Number(event.target.value) }))
                    }
                  />
                </div>
              )}
            </div>
          )}
        </div>

        <SheetFooter className="flex-row items-center justify-between gap-2 border-t border-border px-5 py-3">
          <Button type="button" variant="destructive" size="sm" onClick={remove}>
            <Trash2 /> Remove output
          </Button>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="button" size="sm" onClick={save} disabled={!canSave} title={canSave ? undefined : "Choose a registered model."}>
              <Save /> Save changes
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

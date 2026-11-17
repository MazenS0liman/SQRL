import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DashboardModal,
  type DashboardLayoutPayload,
} from "@/components/visual/DashboardModal";
import { etlApi, EtlApiError } from "@/api/etlApi";
import type { ChartDef, DashboardLayout, PipelineDashboardRead } from "@/types";

function asAxis(raw: unknown): ChartDef["x"] {
  const value = (raw ?? {}) as Record<string, unknown>;
  return {
    column: typeof value.column === "string" ? value.column : "",
    label: typeof value.label === "string" ? value.label : "",
    type: typeof value.type === "string" ? value.type : "",
  };
}

/** Convert a notebook chart payload into the shared dashboard renderer shape. */
export function toChartDef(raw: Record<string, unknown>): ChartDef {
  return {
    id: String(raw.id ?? ""),
    title: String(raw.title ?? "Untitled"),
    description: "",
    plot_type: String(raw.plot_type ?? "bar"),
    analytical_type: "",
    x: asAxis(raw.x),
    y: asAxis(raw.y),
    group_by: (raw.group_by as string | null | undefined) ?? null,
    observation: String(raw.observation ?? ""),
    data: Array.isArray(raw.data) ? (raw.data as Record<string, unknown>[]) : [],
    total_row_count: (raw.total_row_count as number | null | undefined) ?? null,
    truncated: Boolean(raw.truncated),
  };
}

function asLayout(raw: Record<string, unknown> | null | undefined): DashboardLayout | null {
  if (!raw) return null;
  return {
    order: Array.isArray(raw.order) ? (raw.order as string[]) : [],
    hidden_ids: Array.isArray(raw.hidden_ids) ? (raw.hidden_ids as string[]) : [],
    hints: (raw.hints as Record<string, string> | undefined) ?? {},
    overrides: (raw.overrides as Record<string, Record<string, unknown>> | undefined) ?? {},
    custom_charts: (raw.custom_charts as Record<string, unknown>[] | undefined) ?? [],
    text_blocks: (raw.text_blocks as Record<string, unknown>[] | undefined) ?? [],
  };
}

export interface PipelineDashboardDialogProps {
  pipelineId: string;
  pipelineName?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Payload from the run that just finished, used while the GET is in flight. */
  initial?: PipelineDashboardRead | null;
}

/**
 * PowerBI-style dashboard modal for a pipeline output.
 *
 * Reuses the notebook dashboard renderer (tiles, slicers, notes, text blocks,
 * custom charts) but lives inside the pipeline editor, so a run never sends the
 * user to a separate notebook page.
 */
export function PipelineDashboardDialog({
  pipelineId,
  pipelineName,
  open,
  onOpenChange,
  initial = null,
}: PipelineDashboardDialogProps) {
  const [dashboard, setDashboard] = useState<PipelineDashboardRead | null>(initial);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await etlApi.getDashboard(pipelineId);
      setDashboard(payload);
    } catch (err) {
      if (err instanceof EtlApiError && err.status === 404) {
        // A run that just produced a dashboard may not be visible to GET yet;
        // fall back to the payload handed in by the run result.
        if (initial) {
          setDashboard(initial);
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : "Could not load the dashboard.");
      }
    } finally {
      setLoading(false);
    }
  }, [pipelineId, initial]);

  useEffect(() => {
    if (!open) return;
    if (initial) setDashboard(initial);
    void load();
  }, [open, initial, load]);

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
  }, []);

  const handleLayoutChange = useCallback(
    (layout: DashboardLayoutPayload) => {
      // ``DashboardModal`` emits its layout whenever any tile state changes.
      // Skip identical payloads so saving does not feed back into a render
      // loop (the modal re-reconciles when the chart array identity changes).
      const serialized = JSON.stringify(layout);
      if (serialized === lastSaved.current) return;
      lastSaved.current = serialized;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      setSaveStatus("saving");
      saveTimer.current = setTimeout(() => {
        etlApi
          .saveDashboardLayout(pipelineId, { layout: layout as unknown as Record<string, unknown> })
          .then(() => setSaveStatus("saved"))
          .catch(() => {
            lastSaved.current = "";
            setSaveStatus("error");
          });
      }, 800);
    },
    [pipelineId],
  );

  const handleRegenerate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await etlApi.buildDashboard(pipelineId, { regenerate: true });
      setDashboard(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rebuild the dashboard.");
    } finally {
      setLoading(false);
    }
  }, [pipelineId]);

  // Keep the chart array identity stable across renders. ``DashboardModal``
  // reconciles its local layout whenever this prop changes, so rebuilding the
  // array on every render would trigger an endless save loop.
  const charts = useMemo(
    () => (dashboard?.charts ?? []).map(toChartDef),
    [dashboard?.charts],
  );
  const initialLayout = useMemo(() => asLayout(dashboard?.layout), [dashboard?.layout]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[min(1400px,96vw)] gap-0 overflow-hidden p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>{pipelineName ? `${pipelineName} dashboard` : "Pipeline dashboard"}</DialogTitle>
          <DialogDescription>Dashboard generated from the pipeline's latest run.</DialogDescription>
        </DialogHeader>

        <div className="relative h-[80vh] w-full">
          {loading && !dashboard && (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading dashboard…
            </div>
          )}

          {error && !dashboard && (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
              <AlertCircle className="h-6 w-6 text-destructive" />
              <p className="max-w-md text-sm text-muted-foreground">{error}</p>
              <button
                type="button"
                onClick={() => void load()}
                className="text-sm font-medium text-primary hover:underline"
              >
                Try again
              </button>
            </div>
          )}

          {dashboard && (
            <DashboardModal
              charts={charts}
              summary={undefined}
              loading={loading}
              error={error}
              onClose={() => onOpenChange(false)}
              onRegenerate={() => void handleRegenerate()}
              regenerating={loading}
              initialLayout={initialLayout}
              onLayoutChange={handleLayoutChange}
              saveStatus={saveStatus}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

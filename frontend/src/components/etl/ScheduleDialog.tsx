import { useEffect, useState } from "react";
import { AlertCircle, CalendarClock, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { etlApi } from "@/api/etlApi";
import type { PipelineRead, ScheduleMode, ScheduleSpec } from "@/types";

const MODES: { value: ScheduleMode; label: string; hint: string }[] = [
  { value: "manual", label: "Manual", hint: "Only run when you trigger it." },
  { value: "daily", label: "Daily", hint: "Run once a day at the chosen UTC time." },
  { value: "monthly", label: "Monthly", hint: "Run on a day of the month at the chosen time." },
  { value: "yearly", label: "Yearly", hint: "Run once a year on the chosen date." },
  { value: "continuous", label: "Continuous", hint: "Micro-batch dispatch every N seconds." },
];

function specFromPipeline(pipeline: PipelineRead | null): ScheduleSpec {
  return {
    mode: (pipeline?.schedule_mode as ScheduleMode) ?? "manual",
    enabled: Boolean(pipeline?.schedule_enabled),
    time: pipeline?.schedule_time ?? "02:00",
    day: pipeline?.schedule_day ?? 1,
    month: pipeline?.schedule_month ?? 1,
    interval_seconds: pipeline?.schedule_interval_seconds ?? 300,
  };
}

export interface ScheduleDialogProps {
  open: boolean;
  pipeline: PipelineRead | null;
  onOpenChange: (open: boolean) => void;
  onSaved: (pipeline: PipelineRead) => void;
}

/** Set a pipeline to run daily, monthly, yearly, or continuously. */
export function ScheduleDialog({ open, pipeline, onOpenChange, onSaved }: ScheduleDialogProps) {
  const [spec, setSpec] = useState<ScheduleSpec>(() => specFromPipeline(pipeline));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSpec(specFromPipeline(pipeline));
    setError(null);
  }, [open, pipeline]);

  const update = <K extends keyof ScheduleSpec>(key: K, value: ScheduleSpec[K]) =>
    setSpec((current) => ({ ...current, [key]: value }));

  const needsApproval = spec.enabled && spec.mode !== "manual" && pipeline?.status !== "approved";
  const activeHint = MODES.find((mode) => mode.value === spec.mode)?.hint;

  const save = async () => {
    if (!pipeline) return;
    setSaving(true);
    setError(null);
    try {
      const next = await etlApi.updateSchedule(pipeline.id, { schedule: spec });
      onSaved(next);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the schedule.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-primary" /> Pipeline schedule
          </DialogTitle>
          <DialogDescription>
            Scheduled runs use the approved definition and still require the pipeline to be approved.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </div>
          )}
          {needsApproval && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              Approve this pipeline before enabling an automatic schedule.
            </div>
          )}

          <div className="space-y-2">
            <Label>Frequency</Label>
            <Select value={spec.mode} onValueChange={(value) => update("mode", value as ScheduleMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODES.map((mode) => (
                  <SelectItem key={mode.value} value={mode.value}>
                    {mode.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {activeHint && <p className="text-xs text-muted-foreground">{activeHint}</p>}
          </div>

          {(spec.mode === "daily" || spec.mode === "monthly" || spec.mode === "yearly") && (
            <div className="space-y-2">
              <Label htmlFor="schedule-time">Time (UTC)</Label>
              <Input
                id="schedule-time"
                type="time"
                value={spec.time}
                onChange={(event) => update("time", event.target.value)}
              />
            </div>
          )}

          {(spec.mode === "monthly" || spec.mode === "yearly") && (
            <div className="space-y-2">
              <Label htmlFor="schedule-day">Day of month</Label>
              <Input
                id="schedule-day"
                type="number"
                min={1}
                max={31}
                value={spec.day}
                onChange={(event) => update("day", Math.max(1, Math.min(31, Number(event.target.value))))}
              />
              <p className="text-xs text-muted-foreground">
                Months with fewer days run on their last day.
              </p>
            </div>
          )}

          {spec.mode === "yearly" && (
            <div className="space-y-2">
              <Label>Month</Label>
              <Select value={String(spec.month)} onValueChange={(value) => update("month", Number(value))}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Array.from({ length: 12 }, (_, index) => (
                    <SelectItem key={index + 1} value={String(index + 1)}>
                      {new Date(2020, index, 1).toLocaleString(undefined, { month: "long" })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {spec.mode === "continuous" && (
            <div className="space-y-2">
              <Label htmlFor="schedule-interval">Micro-batch interval (seconds)</Label>
              <Input
                id="schedule-interval"
                type="number"
                min={30}
                max={86400}
                value={spec.interval_seconds}
                onChange={(event) =>
                  update("interval_seconds", Math.max(30, Math.min(86400, Number(event.target.value))))
                }
              />
              <p className="text-xs text-muted-foreground">
                Continuous mode dispatches a run every interval. Minimum 30 seconds; runs never
                overlap.
              </p>
            </div>
          )}

          <label className="flex items-center gap-2 rounded-lg border border-border p-3 text-sm">
            <input
              type="checkbox"
              checked={spec.enabled && spec.mode !== "manual"}
              disabled={spec.mode === "manual"}
              onChange={(event) => update("enabled", event.target.checked)}
              className="h-4 w-4 rounded border-border"
            />
            <span>
              Enable automatic runs
              {pipeline?.next_run_at && spec.enabled && (
                <span className="ml-1 text-xs text-muted-foreground">
                  · next {new Date(pipeline.next_run_at).toLocaleString()}
                </span>
              )}
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={saving || needsApproval}>
            {saving && <Loader2 className="animate-spin" />} Save schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

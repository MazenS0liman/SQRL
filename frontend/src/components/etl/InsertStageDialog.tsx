import { useEffect, useState } from "react";
import { ArrowRight, Loader2, Plus, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { cn } from "@/lib/utils";
import type {
  PipelineDefinition,
  PipelineStagePlanRead,
} from "@/types";

export interface StageEdge {
  from: string;
  to: string;
}

interface InsertStageDialogProps {
  open: boolean;
  edge: StageEdge | null;
  definition: PipelineDefinition | null;
  onOpenChange: (open: boolean) => void;
  onPlanned: (plan: PipelineStagePlanRead) => void;
}

const EXAMPLES = [
  "Keep only completed orders",
  "Drop rows with a null customer_id",
  "Aggregate total revenue by order_date",
  "Cast created_at to timestamptz",
  "Remove duplicate event_id rows",
  "Join with the customers source on customer_id",
];

export function InsertStageDialog({
  open,
  edge,
  definition,
  onOpenChange,
  onPlanned,
}: InsertStageDialogProps): JSX.Element {
  const [description, setDescription] = useState("");
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
  }, [open, edge?.from, edge?.to]);

  const submit = async () => {
    if (!edge || !definition || !description.trim() || planning) return;
    setPlanning(true);
    setError(null);
    try {
      const plan = await etlApi.planStage({
        description: description.trim(),
        definition,
        insert_after: edge.from,
        insert_before: edge.to,
      });
      onPlanned(plan);
      setDescription("");
      onOpenChange(false);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "The stage could not be planned. Try naming the columns and the operation."
      );
    } finally {
      setPlanning(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plus className="h-4 w-4 text-primary" /> Insert a stage
          </DialogTitle>
          <DialogDescription>
            Describe what should happen between these two steps. The planner turns it into a
            validated transform — it never runs code or writes SQL.
          </DialogDescription>
        </DialogHeader>

        {edge && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs">
            <Badge variant="outline" className="font-mono">
              {edge.from}
            </Badge>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <Badge variant="secondary" className="gap-1">
              <Sparkles className="h-3 w-3" /> new stage
            </Badge>
            <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
            <Badge variant="outline" className="font-mono">
              {edge.to}
            </Badge>
          </div>
        )}

        <div className="space-y-2">
          <Textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void submit();
              }
            }}
            rows={4}
            autoFocus
            disabled={planning}
            placeholder="e.g. Keep only rows where status is completed, then aggregate total revenue by order_date"
            className="resize-none"
          />
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                disabled={planning}
                onClick={() => setDescription(example)}
                className={cn(
                  "rounded-full border border-border px-2.5 py-0.5 text-[11px] text-muted-foreground transition-colors",
                  "hover:border-primary/40 hover:bg-primary/5 hover:text-foreground"
                )}
              >
                {example}
              </button>
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground">
            Tip: name real columns and mention another step id when you want a join.
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={planning}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => void submit()}
            disabled={!description.trim() || planning || !definition}
          >
            {planning ? <Loader2 className="animate-spin" /> : <Sparkles />}
            {planning ? "Planning stage…" : "Insert stage"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

import { useState } from "react";
import {
  Brain,
  Database,
  HardDrive,
  LayoutDashboard,
  Plus,
  Settings2,
  Shuffle,
  Workflow,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  NODE_H,
  NODE_W,
  STATE_META,
  type DagLayout,
  type NodeKind,
  type TaskState,
} from "@/components/etl/dagUtils";

const KIND_ICON: Record<NodeKind, typeof Database> = {
  source: Database,
  transform: Shuffle,
  sink: HardDrive,
  dashboard: LayoutDashboard,
  ml: Brain,
};

const LEGEND: TaskState[] = ["success", "running", "failed", "upstream_failed", "queued", "none", "planned"];

interface DagCanvasProps {
  layout: DagLayout;
  states: Record<string, TaskState>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onInsertStage?: (from: string, to: string) => void;
  onEditNode?: (id: string) => void;
  className?: string;
}

export function DagCanvas({
  layout,
  states,
  selectedId,
  onSelect,
  onInsertStage,
  onEditNode,
  className,
}: DagCanvasProps): JSX.Element {
  const [zoom, setZoom] = useState(1);
  const nodeById = new Map(layout.nodes.map((n) => [n.id, n]));

  return (
    <div className={cn("flex min-h-0 flex-col rounded-xl border border-border bg-card", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          {LEGEND.map((state) => (
            <span key={state} className="flex items-center gap-1.5">
              <span className={cn("h-2.5 w-2.5 rounded-sm", STATE_META[state].dot)} />
              {STATE_META[state].label}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <Button size="icon" variant="ghost" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.1).toFixed(2)))}>
            <ZoomOut />
          </Button>
          <span className="w-10 text-center text-xs text-muted-foreground">{Math.round(zoom * 100)}%</span>
          <Button size="icon" variant="ghost" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.1).toFixed(2)))}>
            <ZoomIn />
          </Button>
        </div>
      </div>

      <div
        className="group relative min-h-[260px] flex-1 overflow-auto"
        style={{
          backgroundImage: "radial-gradient(circle, rgba(128,128,128,.28) 1px, transparent 1px)",
          backgroundSize: "18px 18px",
        }}
        onClick={() => onSelect(null)}
      >
        {layout.nodes.length === 0 ? (
          <div className="flex h-full min-h-[260px] flex-col items-center justify-center p-6 text-center">
            <Workflow className="h-10 w-10 text-muted-foreground" />
            <p className="mt-3 text-sm font-medium text-foreground">Empty DAG</p>
            <p className="mt-1 max-w-sm text-xs text-muted-foreground">
              Describe the pipeline in the chat — sources, transformations and whether you want a
              dashboard, an ML model, or both. The tasks appear here as a graph.
            </p>
          </div>
        ) : (
          <div style={{ width: layout.width * zoom, height: layout.height * zoom }}>
            <div
              className="relative origin-top-left"
              style={{ width: layout.width, height: layout.height, transform: `scale(${zoom})` }}
            >
              <svg className="absolute inset-0" width={layout.width} height={layout.height}>
                <defs>
                  <marker id="dag-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L10,5 L0,10 z" className="fill-muted-foreground/70" />
                  </marker>
                </defs>
                {layout.edges.map((edge) => {
                  const a = nodeById.get(edge.from);
                  const b = nodeById.get(edge.to);
                  if (!a || !b) return null;
                  const x1 = a.x + NODE_W;
                  const y1 = a.y + NODE_H / 2;
                  const x2 = b.x;
                  const y2 = b.y + NODE_H / 2;
                  const dx = Math.max(24, (x2 - x1) / 2);
                  const active = selectedId === edge.from || selectedId === edge.to;
                  return (
                    <path
                      key={`${edge.from}->${edge.to}`}
                      d={`M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 2},${y2}`}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={active ? 2.5 : 1.5}
                      strokeDasharray={edge.planned ? "5 4" : undefined}
                      markerEnd="url(#dag-arrow)"
                      className={active ? "text-primary" : "text-muted-foreground/60"}
                    />
                  );
                })}
              </svg>

              {onInsertStage &&
                layout.edges
                  .filter((edge) => !edge.planned)
                  .map((edge) => {
                    const a = nodeById.get(edge.from);
                    const b = nodeById.get(edge.to);
                    if (!a || !b) return null;
                    const midX = (a.x + NODE_W + b.x) / 2;
                    const midY = (a.y + b.y + NODE_H) / 2;
                    return (
                      <button
                        key={`insert-${edge.from}-${edge.to}`}
                        type="button"
                        title={`Insert a stage between ${edge.from} and ${edge.to}`}
                        aria-label={`Insert a stage between ${edge.from} and ${edge.to}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onInsertStage(edge.from, edge.to);
                        }}
                        style={{ left: midX - 11, top: midY - 11 }}
                        className={cn(
                          "absolute z-10 flex h-[22px] w-[22px] items-center justify-center rounded-full",
                          "border border-border bg-card text-muted-foreground shadow-sm transition",
                          "opacity-70 hover:scale-110 hover:border-primary hover:text-primary hover:opacity-100",
                          "focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring"
                        )}
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    );
                  })}

              {layout.nodes.map((node) => {
                const state = states[node.id] ?? "none";
                const Icon = KIND_ICON[node.kind];
                return (
                  <div key={node.id}>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(node.id === selectedId ? null : node.id);
                      }}
                      onDoubleClick={(event) => {
                        event.stopPropagation();
                        onEditNode?.(node.id);
                      }}
                      style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                      className={cn(
                        "absolute flex items-center gap-2.5 rounded-lg border border-l-4 bg-card px-3 text-left shadow-sm transition",
                        "hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        STATE_META[state].border,
                        node.planned && "border-dashed",
                        state === "running" && "animate-pulse",
                        selectedId === node.id ? "ring-2 ring-primary" : "border-border"
                      )}
                      title={`${node.label} — ${STATE_META[state].label}${onEditNode ? " · double-click to edit" : ""}`}
                    >
                      <Icon className="h-4 w-4 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs font-semibold text-foreground">
                          {node.label}
                        </span>
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {node.subtitle}
                        </span>
                      </span>
                      <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", STATE_META[state].dot)} />
                    </button>
                    {selectedId === node.id && onEditNode && (!node.planned || node.id === "__ml") && (
                      <button
                        type="button"
                        title="Edit properties"
                        aria-label={`Edit ${node.label} properties`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onEditNode(node.id);
                        }}
                        style={{ left: node.x + NODE_W - 13, top: node.y - 11 }}
                        className="absolute z-20 flex h-[22px] w-[22px] items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition hover:border-primary hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <Settings2 className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

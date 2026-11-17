import type { JobEventRead, PipelineDefinition, PipelineRunRead } from "@/types";

export type OutputTarget = "none" | "dashboard" | "ml" | "both";
export type TaskState =
  | "none"
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "upstream_failed"
  | "planned";
export type NodeKind = "source" | "transform" | "sink" | "dashboard" | "ml";

type Loose = Record<string, unknown>;

export interface DagNode {
  id: string;
  label: string;
  subtitle: string;
  kind: NodeKind;
  parents: string[];
  layer: number;
  x: number;
  y: number;
  planned: boolean;
  config: unknown;
}

export interface DagEdge {
  from: string;
  to: string;
  planned: boolean;
}

export interface DagLayout {
  nodes: DagNode[];
  edges: DagEdge[];
  width: number;
  height: number;
}

export const NODE_W = 210;
export const NODE_H = 64;
const GAP_X = 84;
const GAP_Y = 26;
const PAD = 32;

const PARENT_KEYS = [
  "input",
  "inputs",
  "left",
  "right",
  "left_input",
  "right_input",
  "parents",
  "depends_on",
];

function rec(value: unknown): Loose | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Loose) : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function parentsOf(step: Loose): string[] {
  const out: string[] = [];
  for (const key of PARENT_KEYS) {
    const value = step[key];
    if (typeof value === "string") {
      out.push(value);
    } else if (Array.isArray(value)) {
      for (const item of value) {
        if (typeof item === "string") out.push(item);
      }
    }
  }
  // Joins reference their second input via transform.right
  const right = str(rec(step.transform)?.right);
  if (right) out.push(right);
  return Array.from(new Set(out));
}

function describe(step: Loose): { kind: NodeKind; subtitle: string } {
  const type = str(step.type);
  if (type === "source") {
    const s = rec(step.source);
    return {
      kind: "source",
      subtitle:
        str(s?.table_name) ?? str(s?.table) ?? str(s?.object_key) ?? str(s?.kind) ?? "source",
    };
  }
  if (type === "sink") {
    const s = rec(step.sink);
    const target = str(s?.table) ?? str(s?.object_key);
    const kind = str(s?.kind) ?? "sink";
    return { kind: "sink", subtitle: target ? `${kind} → ${target}` : kind };
  }
  const t = rec(step.transform);
  const op =
    str(t?.kind) ?? str(t?.type) ?? str(t?.op) ?? str(step.op) ?? str(step.operation) ?? "transform";
  return { kind: "transform", subtitle: op.replaceAll("_", " ") };
}

export function buildLayout(
  definition: PipelineDefinition | null | undefined,
  output: OutputTarget
): DagLayout {
  const steps = ((definition?.steps ?? []) as unknown as Loose[]).filter(Boolean);
  if (steps.length === 0) return { nodes: [], edges: [], width: 0, height: 0 };

  const ids = new Set(steps.map((step, i) => str(step.id) ?? `step_${i}`));
  const raw: Omit<DagNode, "layer" | "x" | "y">[] = steps.map((step, i) => {
    const id = str(step.id) ?? `step_${i}`;
    const info = describe(step);
    return {
      id,
      label: id,
      subtitle: info.subtitle,
      kind: info.kind,
      parents: parentsOf(step).filter((p) => ids.has(p) && p !== id),
      planned: false,
      config: step,
    };
  });

  const sinkId =
    raw.find((n) => n.kind === "sink")?.id ?? raw[raw.length - 1]?.id ?? raw[0].id;
  if (output === "dashboard" || output === "both") {
    raw.push({
      id: "__dashboard",
      label: "dashboard",
      subtitle: "Notebook dashboard cell",
      kind: "dashboard",
      parents: [sinkId],
      planned: true,
      config: { planned_output: "dashboard" },
    });
  }
  if (output === "ml" || output === "both") {
    raw.push({
      id: "__ml",
      label: "train_model",
      subtitle: "Train ML model",
      kind: "ml",
      parents: [sinkId],
      planned: true,
      config: { planned_output: "ml_model" },
    });
  }

  const byId = new Map(raw.map((n) => [n.id, n]));
  const layerMemo = new Map<string, number>();
  const layerOf = (id: string, trail: Set<string> = new Set()): number => {
    const cached = layerMemo.get(id);
    if (cached !== undefined) return cached;
    if (trail.has(id)) return 0;
    trail.add(id);
    const node = byId.get(id);
    const layer =
      !node || node.parents.length === 0
        ? 0
        : Math.max(...node.parents.map((p) => layerOf(p, trail))) + 1;
    layerMemo.set(id, layer);
    return layer;
  };

  const rowCount = new Map<number, number>();
  raw.forEach((n) => {
    const layer = layerOf(n.id);
    rowCount.set(layer, (rowCount.get(layer) ?? 0) + 1);
  });
  const maxRows = Math.max(...rowCount.values());
  const layerCount = Math.max(...rowCount.keys()) + 1;
  const rowSeen = new Map<number, number>();
  const unit = NODE_H + GAP_Y;

  const nodes: DagNode[] = raw.map((n) => {
    const layer = layerOf(n.id);
    const row = rowSeen.get(layer) ?? 0;
    rowSeen.set(layer, row + 1);
    const inLayer = rowCount.get(layer) ?? 1;
    return {
      ...n,
      layer,
      x: PAD + layer * (NODE_W + GAP_X),
      y: PAD + ((maxRows - inLayer) * unit) / 2 + row * unit,
    };
  });

  const edges: DagEdge[] = nodes.flatMap((n) =>
    n.parents.map((p) => ({ from: p, to: n.id, planned: n.planned }))
  );

  return {
    nodes,
    edges,
    width: PAD * 2 + layerCount * NODE_W + (layerCount - 1) * GAP_X,
    height: PAD * 2 + maxRows * NODE_H + (maxRows - 1) * GAP_Y,
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Which DAG step an event belongs to: explicit step_id if the backend sends one, else id in message. */
export function eventStep(event: JobEventRead, ids: string[]): string | null {
  const raw = event as unknown as Loose;
  const direct =
    str(raw.step_id) ??
    str(rec(raw.payload)?.step_id) ??
    str(rec(raw.data)?.step_id) ??
    str(rec(raw.metadata)?.step_id);
  if (direct) return direct;
  const message = String(event.message ?? "");
  return (
    ids.find((id) => new RegExp(`(^|[^\\w])${escapeRegExp(id)}([^\\w]|$)`).test(message)) ?? null
  );
}

const DONE = /(done|complete|finish|succe|written|loaded|ok\b)/i;

export function deriveTaskStates(
  layout: DagLayout,
  run: PipelineRunRead | null,
  events: JobEventRead[]
): Record<string, TaskState> {
  const states: Record<string, TaskState> = {};
  for (const n of layout.nodes) states[n.id] = n.planned ? "planned" : "none";
  if (!run) return states;

  // Preferred: the backend reports per-task state in run.result.task_states
  const reported = rec(rec(run.result)?.task_states);
  if (reported) {
    for (const n of layout.nodes) {
      const s = str(rec(reported[n.id])?.state);
      if (s && s in STATE_META) states[n.id] = s as TaskState;
    }
    return states;
  }

  const real = layout.nodes.filter((n) => !n.planned).sort((a, b) => a.layer - b.layer);
  const ids = real.map((n) => n.id);

  if (run.status === "succeeded") {
    real.forEach((n) => (states[n.id] = "success"));
    if (rec(rec(run.result)?.materialization) && "__dashboard" in states) {
      states.__dashboard = "success";
    }
    return states;
  }
  if (run.status === "queued") {
    real.forEach((n) => (states[n.id] = "queued"));
    return states;
  }

  const byStep = new Map<string, JobEventRead[]>();
  for (const event of events) {
    const id = eventStep(event, ids);
    if (id) byStep.set(id, [...(byStep.get(id) ?? []), event]);
  }
  for (const n of real) {
    const evs = byStep.get(n.id) ?? [];
    if (evs.some((e) => e.level === "error")) states[n.id] = "failed";
    else if (evs.some((e) => DONE.test(String(e.message ?? "")))) states[n.id] = "success";
    else if (evs.length > 0) states[n.id] = "running";
    else states[n.id] = run.status === "running" ? "queued" : "none";
  }

  if (run.status === "failed") {
    if (!real.some((n) => states[n.id] === "failed")) {
      const first = real.find((n) => states[n.id] !== "success");
      if (first) states[first.id] = "failed";
    }
    for (const n of real) {
      if (
        states[n.id] !== "success" &&
        states[n.id] !== "failed" &&
        n.parents.some((p) => states[p] === "failed" || states[p] === "upstream_failed")
      ) {
        states[n.id] = "upstream_failed";
      }
    }
  }
  return states;
}

export const STATE_META: Record<TaskState, { label: string; dot: string; border: string }> = {
  success: { label: "success", dot: "bg-emerald-500", border: "border-l-emerald-500" },
  running: { label: "running", dot: "bg-lime-400", border: "border-l-lime-400" },
  failed: { label: "failed", dot: "bg-red-500", border: "border-l-red-500" },
  upstream_failed: { label: "upstream failed", dot: "bg-orange-500", border: "border-l-orange-500" },
  queued: { label: "queued", dot: "bg-slate-400", border: "border-l-slate-400" },
  none: { label: "no status", dot: "bg-transparent border border-border", border: "border-l-border" },
  planned: { label: "planned", dot: "bg-violet-400/60", border: "border-l-violet-400" },
};

export function runStateOf(status: string): TaskState {
  if (status === "succeeded") return "success";
  if (status === "failed") return "failed";
  if (status === "running") return "running";
  if (status === "queued") return "queued";
  return "none";
}
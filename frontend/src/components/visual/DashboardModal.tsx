import { Fragment, useState, useEffect, useMemo, useRef, type DragEvent, type ReactNode } from "react";
import {
  X, ChevronDown, ChevronLeft, AlertCircle, LayoutDashboard, RotateCcw, Check, Loader2,
  GripVertical, Plus, Pencil, Trash2, Eye, EyeOff, PanelRightOpen, PanelRightClose, FileDown,
  History, Cloud, CloudOff, Type, Square,
} from "lucide-react";
import type { Summary, ChartDef, AxisDef, DashboardLayout, DashboardVersion } from "@/types";
import { ChartBlock } from "@/components/visual/ChartBlock"

function makeId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return `${prefix}-${crypto.randomUUID()}`;
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
}

/** Lightweight client-side type guess for a raw data-source preview column
 * (preview rows come back untyped, unlike a ChartDef's x/y AxisDef). Not
 * meant to be as rigorous as the backend's schema inference — good enough
 * to pick a sensible default axis type for a freshly built custom chart. */
function inferColumnType(rows: Record<string, unknown>[], col: string): string {
  const vals = rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined && v !== "");
  if (vals.length === 0) return "nominal";
  const allNumeric = vals.every(
    (v) => typeof v === "number" || (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)))
  );
  if (allNumeric) return "quantitative";
  const allDate = vals.every((v) => !Number.isNaN(Date.parse(String(v))));
  if (allDate) return "temporal";
  return "nominal";
}

// ── Global (cross-chart) filter detection ───────────────────────────────────
interface FilterDef { column: string; label: string; values: string[]; }

/**
 * Finds dimensions worth turning into a dashboard-wide slicer: a column
 * used as the x-axis (nominal/ordinal/temporal) or as group_by on at least
 * two charts, with a manageable number of distinct values (2–20). Mirrors
 * how PowerBI slicers are usually built off shared dimension columns.
 */
function computeGlobalFilters(charts: ChartDef[]): FilterDef[] {
  const perColumn = new Map<string, { label: string; values: Set<string>; chartIds: Set<string> }>();

  for (const chart of charts) {
    const candidates = new Set<string>();
    if (chart.x && ["nominal", "ordinal", "temporal"].includes(chart.x.type)) candidates.add(chart.x.column);
    if (chart.group_by) candidates.add(chart.group_by);

    for (const col of candidates) {
      const distinct = new Set(chart.data.map((r) => String(r[col])));
      if (distinct.size < 2 || distinct.size > 20) continue;

      if (!perColumn.has(col)) {
        perColumn.set(col, { label: col === chart.x?.column ? chart.x.label : col, values: new Set(), chartIds: new Set() });
      }
      const entry = perColumn.get(col)!;
      distinct.forEach((v) => entry.values.add(v));
      entry.chartIds.add(chart.id);
    }
  }

  return Array.from(perColumn.entries())
    .filter(([, entry]) => entry.chartIds.size >= 2) // only cross-chart dimensions are worth a slicer
    .map(([column, entry]) => ({ column, label: entry.label, values: Array.from(entry.values).sort() }));
}

/** Row-level filter — a chart that doesn't carry the filtered column is left untouched. */
function applyGlobalFilters(chart: ChartDef, filters: Record<string, Set<string>>): ChartDef {
  const active = Object.entries(filters).filter(([, set]) => set.size > 0);
  if (active.length === 0) return chart;
  const data = chart.data.filter((row) =>
    active.every(([col, set]) => !(col in row) || set.has(String(row[col])))
  );
  return { ...chart, data };
}

// ── Slicer dropdown ──────────────────────────────────────────────────────────
function FilterDropdown({
  filterDef, selected, onToggle,
}: { filterDef: FilterDef; selected: Set<string>; onToggle: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
          selected.size > 0 ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"
        }`}>
        {filterDef.label}{selected.size > 0 ? ` (${selected.size})` : ""}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-56 w-52 overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-xl">
          {filterDef.values.map((v) => (
            <button key={v} onClick={() => onToggle(v)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-secondary">
              <span className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border ${selected.has(v) ? "border-primary bg-primary" : "border-border"}`}>
                {selected.has(v) && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
              </span>
              <span className="truncate">{v}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Layout / sizing ───────────────────────────────────────────────────────────
// Every tile — chart or text — lives on a 6-unit-wide dense grid instead of
// a fixed auto-wrapping column count. Each tile carries its own width, so
// e.g. two "half" tiles or three "third" tiles land in the same row and
// visibly sit beside each other, instead of width being an accident of how
// many items came before it.
export type TileWidth = "third" | "half" | "twoThirds" | "full";

const WIDTH_OPTIONS: { value: TileWidth; label: string; short: string; spanClass: string }[] = [
  { value: "third",     label: "Small — 1/3 width", short: "S",    spanClass: "col-span-6 md:col-span-2" },
  { value: "half",      label: "Medium — 1/2 width", short: "M",   spanClass: "col-span-6 md:col-span-3" },
  { value: "twoThirds", label: "Large — 2/3 width",  short: "L",   spanClass: "col-span-6 md:col-span-4" },
  { value: "full",      label: "Full width",         short: "Full", spanClass: "col-span-6" },
];

function widthSpanClass(width: TileWidth | undefined): string {
  return WIDTH_OPTIONS.find((o) => o.value === width)?.spanClass ?? WIDTH_OPTIONS[0].spanClass;
}

function WidthPicker({ value, onChange }: { value: TileWidth; onChange: (v: TileWidth) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {WIDTH_OPTIONS.map((opt) => (
        <button key={opt.value} onClick={() => onChange(opt.value)}
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            value === opt.value ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"
          }`}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ── Customization types ──────────────────────────────────────────────────────
interface ChartOverride {
  title?: string;
  description?: string;
  plot_type?: string;
  observation?: string;
  group_by?: string | null;
  x?: AxisDef;
  y?: AxisDef;
  /** Defaults to "third" (roughly the old xl:grid-cols-3 sizing) when unset. */
  width?: TileWidth;
}

/** A free-standing text block a user can drop onto the dashboard grid —
 * headings, section labels, or plain notes, independent of any chart.
 * `card` controls whether it renders inside a bordered box (easier to spot
 * and grab) or as bare text sitting directly on the canvas, Notion-style. */
export interface DashboardTextBlockDef {
  id: string;
  size: "h1" | "h2" | "h3" | "body" | "caption";
  content: string;
  /** Defaults to "full" when unset. */
  width?: TileWidth;
  /** Defaults to false (plain text, no card chrome) when unset. */
  card?: boolean;
}

const TEXT_SIZE_OPTIONS: { value: DashboardTextBlockDef["size"]; label: string; className: string }[] = [
  { value: "h1",      label: "Heading",    className: "text-2xl font-bold" },
  { value: "h2",      label: "Subheading", className: "text-xl font-semibold" },
  { value: "h3",      label: "Label",      className: "text-base font-semibold" },
  { value: "body",    label: "Body",       className: "text-sm" },
  { value: "caption", label: "Caption",    className: "text-xs text-muted-foreground" },
];

function textSizeClass(size: DashboardTextBlockDef["size"]): string {
  return TEXT_SIZE_OPTIONS.find((o) => o.value === size)?.className ?? "text-sm";
}

function isTextBlockId(id: string): boolean {
  return id.startsWith("text-");
}

type SidebarView = { tab: "list" } | { tab: "add" } | { tab: "add-text" } | { tab: "edit"; id: string } | { tab: "edit-text"; id: string };

/** Full dashboard customization payload persisted to the backend. */
export interface DashboardLayoutPayload {
  order: string[];
  hidden_ids: string[];
  hints: Record<string, string>;
  overrides: Record<string, ChartOverride>;
  custom_charts: ChartDef[];
  text_blocks: DashboardTextBlockDef[];
}

function layoutFromPayload(layout?: DashboardLayout | null): {
  order: string[];
  overrides: Record<string, ChartOverride>;
  customCharts: ChartDef[];
  textBlocks: DashboardTextBlockDef[];
  removedIds: Set<string>;
  hints: Record<string, string>;
} {
  if (!layout) {
    return { order: [], overrides: {}, customCharts: [], textBlocks: [], removedIds: new Set(), hints: {} };
  }
  return {
    order: layout.order ?? [],
    overrides: (layout.overrides ?? {}) as Record<string, ChartOverride>,
    customCharts: (layout.custom_charts ?? []) as ChartDef[],
    // `text_blocks` requires a matching field on the backend DashboardLayout
    // schema to survive reloads — see save_dashboard_layout / the archived
    // version snapshot, both of which just round-trip layout.model_dump().
    textBlocks: ((layout as unknown as { text_blocks?: DashboardTextBlockDef[] }).text_blocks ?? []),
    removedIds: new Set(layout.hidden_ids ?? []),
    hints: layout.hints ?? {},
  };
}

function payloadFromState(
  order: string[],
  overrides: Record<string, ChartOverride>,
  customCharts: ChartDef[],
  textBlocks: DashboardTextBlockDef[],
  removedIds: Set<string>,
  hints: Record<string, string>,
): DashboardLayoutPayload {
  return {
    order,
    hidden_ids: Array.from(removedIds),
    hints,
    overrides,
    custom_charts: customCharts,
    text_blocks: textBlocks,
  };
}

function notebookChartToDef(chart: {
  id: string; title: string; plot_type: string;
  x?: { column?: string; label?: string; type?: string };
  y?: { column?: string; label?: string; type?: string };
  group_by?: string | null; observation?: string; data?: Record<string, unknown>[];
  hint?: string;
}): ChartDef {
  return {
    id: chart.id,
    title: chart.title,
    description: "",
    plot_type: chart.plot_type,
    analytical_type: "",
    x: {
      column: chart.x?.column ?? "",
      label: chart.x?.label ?? chart.x?.column ?? "",
      type: chart.x?.type ?? "nominal",
    },
    y: {
      column: chart.y?.column ?? "",
      label: chart.y?.label ?? chart.y?.column ?? "",
      type: chart.y?.type ?? "quantitative",
    },
    group_by: chart.group_by ?? null,
    observation: chart.observation ?? "",
    data: chart.data ?? [],
    hint: chart.hint,
  };
}

/** A data source the "Add chart" panel can pull a raw preview from. */
export interface DashboardDataSourceRef {
  id: string;
  label: string;
  table_name: string;
}

/** Fetches a sample of rows (and their column names) directly from a
 * notebook data source, independent of any chart that's already been
 * generated against it. Typically backed by the same preview endpoint the
 * "View data" modal uses. */
export type FetchSourcePreview = (
  sourceId: string
) => Promise<{ columns: string[]; rows: Record<string, unknown>[] }>;

/** Keys actually present in a chart's own rows — the only columns safe to
 * re-map a chart onto, since chart.data is already the scoped SQL result. */
function dataColumns(chart: ChartDef): string[] {
  const row = chart.data[0];
  return row ? Object.keys(row) : [];
}

function applyOverride(chart: ChartDef, override?: ChartOverride): ChartDef {
  if (!override) return chart;
  return {
    ...chart,
    title: override.title ?? chart.title,
    description: override.description ?? chart.description,
    plot_type: override.plot_type ?? chart.plot_type,
    observation: override.observation ?? chart.observation,
    group_by: override.group_by !== undefined ? override.group_by : chart.group_by,
    x: override.x ?? chart.x,
    y: override.y ?? chart.y,
  };
}

const PLOT_TYPE_OPTIONS = [
  "bar", "line", "scatter", "heatmap", "boxplot", "table", "pie", "radar", "area",
  "bullet", "bump", "calendar", "chord", "circle-packing", "funnel", "geo", "marimekko",
  "network", "parallel-coordinates", "radial-bar", "sankey", "stream", "sunburst",
  "swarmplot", "treemap", "voronoi", "waffle"
] as const;

// Only offer swaps that make sense for the axis types the SQL actually
// produced — e.g. a bar chart's categorical x-axis can become a line, but a
// scatter's two numeric axes can't safely become a heatmap without a third
// aggregation column, so it stays fixed. This restricted set powers the
// quick in-tile switcher; the sidebar's edit form allows any plot type.
const COMPATIBLE_PLOT_TYPES: Record<string, string[]> = {
  bar: ["bar", "line", "area", "bullet"],
  line: ["line", "bar", "area"],
  scatter: ["scatter"],
  heatmap: ["heatmap"],
  boxplot: ["boxplot"],
  table: ["table"],
  pie: ["pie", "waffle"],
  radar: ["radar"],
  area: ["area", "line", "bar"],
  bullet: ["bullet", "bar", "waffle"],
  bump: ["line"],
  calendar: ["calendar"],
  chord: ["chord"],
  circlePacking: ["circle-packing"],
  funnel: ["funnel"],
  geo: ["geo"],
  marimekko: ["marimekko"],
  network: ["network"],
  parallelCoordinates: ["parallel-coordinates"],
  radialBar: ["radial-bar"],
  sankey: ["sankey"],
  stream: ["line"],
  sunburst: ["sunburst"],
  swarmplot: ["swarmplot"],
  treemap: ["treemap"],
  voronoi: ["voronoi"],
  waffle: ["waffle", "pie", "bullet"],
};

// ── Per-chart notes ───────────────────────────────────────────────────────────
function HintEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="rounded-lg border border-dashed border-border bg-secondary/20 px-3 py-2">
      <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
        Your notes
      </label>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft !== value) onChange(draft); }}
        placeholder="Add a note about this chart…"
        rows={2}
        className="w-full resize-none rounded-md bg-transparent px-1 py-1 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border focus:border-input focus:bg-background focus:px-2 focus:py-1.5"
      />
    </div>
  );
}

// ── Dashboard tile (draggable) — delegates entirely into ChartBlock so the
// reorder/edit/hide buttons, plot-type switcher, and width switcher render
// as part of the card's own header/sub-header, and the note renders inside
// the card's body instead of a separate element bolted on beneath it. ──────
function DashboardTile({
  chart, editable, readOnly = false, dragging, dragOver, dragOverSide, hint, onHintChange,
  width, onWidthChange,
  onDragStart, onDragOver, onDragLeave, onDrop, onDragEnd,
  onEdit, onHide,
}: {
  chart: ChartDef;
  editable: boolean;
  readOnly?: boolean;
  dragging: boolean;
  dragOver: boolean;
  dragOverSide?: "before" | "after" | null;
  hint: string;
  onHintChange: (value: string) => void;
  width: TileWidth;
  onWidthChange: (w: TileWidth) => void;
  onDragStart: (e: DragEvent<HTMLButtonElement>) => void;
  onDragOver: (e: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDrop: (e: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
  onEdit: () => void;
  onHide: () => void;
}) {
  const [plotType, setPlotType] = useState(chart.plot_type);
  useEffect(() => setPlotType(chart.plot_type), [chart.plot_type, chart.id]);
  const options = COMPATIBLE_PLOT_TYPES[chart.plot_type] ?? [chart.plot_type];
  const effectiveChart = plotType === chart.plot_type ? chart : { ...chart, plot_type: plotType };
  const showSubHeader = !readOnly || options.length > 1;

  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`relative h-full rounded-xl transition-all ${dragging ? "opacity-40" : ""} ${
        dragOver && !dragOverSide ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : ""
      }`}
    >
      {dragOver && dragOverSide === "before" && (
        <div className="pointer-events-none absolute -left-2.5 top-1 bottom-1 z-10 w-1 rounded-full bg-primary" />
      )}
      {dragOver && dragOverSide === "after" && (
        <div className="pointer-events-none absolute -right-2.5 top-1 bottom-1 z-10 w-1 rounded-full bg-primary" />
      )}
      <ChartBlock
        chart={effectiveChart}
        toolbarExtra={editable ? (
          <>
            <button
              draggable
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              title="Drag to reorder"
              className="cursor-grab rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground active:cursor-grabbing"
            >
              <GripVertical className="h-3.5 w-3.5" />
            </button>
            <button onClick={onEdit} title="Edit chart"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button onClick={onHide} title="Remove from dashboard"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
              <X className="h-3.5 w-3.5" />
            </button>
          </>
        ) : undefined}
        subHeader={showSubHeader ? (
          <>
            {options.length > 1 && options.map((opt) => (
              <button key={opt} onClick={() => setPlotType(opt)}
                className={`rounded-md px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                  plotType === opt ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-secondary"
                }`}>
                {opt}
              </button>
            ))}
            {options.length > 1 && !readOnly && <div className="mx-1 h-3 w-px bg-border" />}
            {!readOnly && WIDTH_OPTIONS.map((w) => (
              <button key={w.value} onClick={() => onWidthChange(w.value)} title={w.label}
                className={`rounded-md px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                  width === w.value ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-secondary"
                }`}>
                {w.short}
              </button>
            ))}
          </>
        ) : undefined}
        footer={
          !readOnly
            ? <HintEditor value={hint} onChange={onHintChange} />
            : hint.trim()
              ? (
                <div className="rounded-lg border border-dashed border-border bg-secondary/20 px-3 py-2 text-xs text-muted-foreground italic">
                  {hint}
                </div>
              )
              : undefined
        }
      />
    </div>
  );
}

// ── Text block tile (draggable) — `card` toggles between a bordered box
// (easy to spot and grab, like the old default) and bare text sitting
// directly on the canvas. In plain mode the controls live in a small
// floating toolbar that only appears on hover, so the text itself stays
// unadorned but is still fully editable/movable.
//
// Text is editable two ways: (1) the small pencil icon opens the full
// sidebar form (size/width/card + text), or (2) clicking directly into the
// rendered text — or landing here fresh from the "+" insert bar — drops it
// into inline edit mode: a borderless textarea sitting exactly where the
// text will render, so typing happens at the point on the canvas the user
// clicked, with no modal or sidebar in the way. Blurring an empty inline
// edit removes the block instead of leaving a dead tile behind. ───────────
function TextBlockTile({
  block, editable, dragging, dragOver, dragOverSide, width, card, isEditing,
  onDragStart, onDragOver, onDragLeave, onDrop, onDragEnd, onEdit, onHide, onWidthChange, onCardToggle,
  onStartInlineEdit, onCommitInlineEdit, onSplit,
}: {
  block: DashboardTextBlockDef;
  editable: boolean;
  dragging: boolean;
  dragOver: boolean;
  dragOverSide?: "before" | "after" | null;
  width: TileWidth;
  card: boolean;
  isEditing: boolean;
  onDragStart: (e: DragEvent<HTMLButtonElement>) => void;
  onDragOver: (e: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDrop: (e: DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
  onEdit: () => void;
  onHide: () => void;
  onWidthChange: (w: TileWidth) => void;
  onCardToggle: () => void;
  onStartInlineEdit: () => void;
  onCommitInlineEdit: (content: string) => void;
  /** Enter (no Shift) hands the current draft off here instead of adding a
   * newline in place: this block keeps everything up to the caret, and a
   * fresh sibling block is created (and focused) right after it — the
   * parent owns the actual split since it has to touch both `order` and
   * `textBlocks`. Shift+Enter bypasses this entirely and behaves like a
   * normal textarea newline. */
  onSplit: (content: string) => void;
}) {
  const [draft, setDraft] = useState(block.content);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Enter calls onSplit() and then blurs the textarea to exit edit mode;
  // that blur would otherwise also fire onCommitInlineEdit with the same
  // content, racing the split (and, for a now-empty leading block, wrongly
  // deleting it). This just tells the blur handler "already handled."
  const splitInFlightRef = useRef(false);

  useEffect(() => { if (isEditing) setDraft(block.content); }, [isEditing, block.content]);

  // Autofocus + place the caret at the end (rather than selecting
  // everything) whenever inline editing starts, so it behaves like clicking
  // into existing text rather than replacing it.
  useEffect(() => {
    if (!isEditing) return;
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    const len = el.value.length;
    el.setSelectionRange(len, len);
    autoGrow(el);
  }, [isEditing]);

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  const containerCls = card
    ? `flex h-full flex-col gap-1.5 rounded-xl border border-border bg-card p-4 shadow-sm transition-all ${
        dragging ? "opacity-40" : ""
      } ${dragOver ? "ring-2 ring-primary ring-offset-2 ring-offset-background" : ""}`
    : `group relative flex h-full flex-col gap-1.5 rounded-lg p-1.5 -m-1.5 transition-all ${
        dragging ? "opacity-40" : ""
      } ${dragOver ? "bg-secondary/10 ring-2 ring-primary ring-offset-2 ring-offset-background" : ""}`;

  const toolbar = (
    <>
      {WIDTH_OPTIONS.map((w) => (
        <button key={w.value} onClick={() => onWidthChange(w.value)} title={w.label}
          className={`rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
            width === w.value ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-secondary"
          }`}>
          {w.short}
        </button>
      ))}
      <div className="mx-0.5 h-3 w-px bg-border" />
      <button onClick={onCardToggle} title={card ? "Remove card background" : "Add card background (easier to grab)"}
        className={`rounded-md p-1 ${card ? "text-primary" : "text-muted-foreground"} hover:bg-secondary hover:text-foreground`}>
        <Square className="h-3.5 w-3.5" />
      </button>
      <button
        draggable
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        title="Drag to reorder"
        className="cursor-grab rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground active:cursor-grabbing"
      >
        <GripVertical className="h-3.5 w-3.5" />
      </button>
      <button onClick={onEdit} title="Edit text"
        className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        <Pencil className="h-3.5 w-3.5" />
      </button>
      <button onClick={onHide} title="Remove from dashboard"
        className="rounded-md p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
        <X className="h-3.5 w-3.5" />
      </button>
    </>
  );

  return (
    <div onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop} className={containerCls}>
      {dragOver && dragOverSide === "before" && (
        <div className="pointer-events-none absolute -left-2.5 top-1 bottom-1 z-10 w-1 rounded-full bg-primary" />
      )}
      {dragOver && dragOverSide === "after" && (
        <div className="pointer-events-none absolute -right-2.5 top-1 bottom-1 z-10 w-1 rounded-full bg-primary" />
      )}
      {editable && !isEditing && (
        card ? (
          <div className="mb-1 flex flex-wrap items-center justify-end gap-1">{toolbar}</div>
        ) : (
          <div className="absolute -top-2 right-1 z-10 flex items-center gap-1 rounded-lg border border-border bg-card px-1 py-1 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
            {toolbar}
          </div>
        )
      )}
      {isEditing ? (
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => { setDraft(e.target.value); autoGrow(e.currentTarget); }}
          onBlur={() => {
            if (splitInFlightRef.current) { splitInFlightRef.current = false; return; }
            onCommitInlineEdit(draft);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); onCommitInlineEdit(block.content); return; }
            // Enter splits into a new block (Notion-style) rather than
            // growing this one with a newline — a tall multi-line block was
            // the thing pushing its own later lines out of view. Shift+Enter
            // is left alone so a genuine soft line break is still possible.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              splitInFlightRef.current = true;
              onSplit(draft);
              (e.currentTarget as HTMLTextAreaElement).blur();
            }
          }}
          placeholder="Type something…"
          rows={1}
          // box-border so scrollHeight (used by autoGrow) includes padding —
          // a content-box/border-box mismatch here is what causes the last
          // line to get clipped as you type. min-h avoids a 0-height flash
          // before the mount effect's first autoGrow() call runs. Height is
          // otherwise owned entirely by autoGrow via inline style — no `h-*`
          // class should ever sit on this element, or it'll fight the JS.
          className={`box-border w-full min-h-[1.5em] resize-none overflow-hidden bg-transparent outline-none placeholder:italic placeholder:text-muted-foreground ${textSizeClass(block.size)}`}
        />
      ) : (
        <p
          onClick={() => !dragging && editable && onStartInlineEdit()}
          className={`whitespace-pre-wrap break-words text-foreground ${textSizeClass(block.size)} ${
            editable ? "cursor-text rounded-sm hover:bg-secondary/30" : ""
          }`}
        >
          {block.content || <span className="italic text-muted-foreground">Click to add text…</span>}
        </p>
      )}
    </div>
  );
}

// ── "+" insertion bar — a thin hover strip that sits between tiles (and at
// the very top/bottom of the grid) so a new text block can be dropped in at
// the exact spot the cursor is, without opening the Customize sidebar. It
// doubles as a drop target for reordering: dragging any tile over the strip
// and releasing inserts it at that position. ────────────────────────────────
function InsertTextBar({
  onInsert, dragOver, onDragOver, onDragLeave, onDrop,
}: {
  onInsert: () => void;
  dragOver: boolean;
  onDragOver: (e: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDrop: (e: DragEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className="group/insert relative col-span-6 -my-1.5 flex h-5 items-center"
    >
      <div className={`h-px w-full transition-colors ${dragOver ? "bg-primary" : "bg-transparent group-hover/insert:bg-border"}`} />
      <button
        onClick={onInsert}
        title="Add text here"
        className={`absolute left-1/2 flex h-6 w-6 -translate-x-1/2 items-center justify-center rounded-full border bg-card text-muted-foreground opacity-0 shadow-sm transition-opacity hover:border-primary/50 hover:text-primary group-hover/insert:opacity-100 ${
          dragOver ? "opacity-100 border-primary/50 text-primary" : "border-border"
        }`}
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Sidebar building blocks ───────────────────────────────────────────────────
function SidebarHeader({ title, onBack, onClose }: { title: string; onBack?: () => void; onClose: () => void }) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
      {onBack && (
        <button onClick={onBack} title="Back"
          className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
          <ChevronLeft className="h-4 w-4" />
        </button>
      )}
      <h3 className="flex-1 truncate text-sm font-semibold text-foreground">{title}</h3>
      <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return <label className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">{children}</label>;
}

const inputCls = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring";
const inputClsSm = "w-full rounded-lg border border-input bg-background px-2 py-2 text-xs text-foreground outline-none focus:ring-2 focus:ring-ring";

function ChartEditForm({
  chart, columns, width, onSave, onCancel,
}: { chart: ChartDef; columns: string[]; width: TileWidth; onSave: (patch: ChartOverride) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(chart.title);
  const [description, setDescription] = useState(chart.description ?? "");
  const [plotType, setPlotType] = useState(chart.plot_type);
  const [chartWidth, setChartWidth] = useState<TileWidth>(width);
  const [xColumn, setXColumn] = useState(chart.x.column);
  const [xLabel, setXLabel] = useState(chart.x.label);
  const [yColumn, setYColumn] = useState(chart.y.column);
  const [yLabel, setYLabel] = useState(chart.y.label);
  const [groupBy, setGroupBy] = useState(chart.group_by ?? "");

  const colOptions = columns.length ? columns : [chart.x.column, chart.y.column].filter(Boolean);

  const handleSave = () => {
    onSave({
      title: title.trim() || chart.title,
      description,
      plot_type: plotType,
      x: { ...chart.x, column: xColumn, label: xLabel.trim() || xColumn },
      y: { ...chart.y, column: yColumn, label: yLabel.trim() || yColumn },
      group_by: groupBy || null,
      width: chartWidth,
    });
  };

  return (
    <div className="space-y-3">
      <div>
        <FieldLabel>Title</FieldLabel>
        <input value={title} onChange={(e) => setTitle(e.target.value)} className={inputCls} />
      </div>
      <div>
        <FieldLabel>Description</FieldLabel>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
          className={`resize-none ${inputCls}`} />
      </div>
      <div>
        <FieldLabel>Width</FieldLabel>
        <WidthPicker value={chartWidth} onChange={setChartWidth} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Tiles that add up to a full row (e.g. two "1/2" charts) sit side by side.
        </p>
      </div>
      <div>
        <FieldLabel>Chart type</FieldLabel>
        <select value={plotType} onChange={(e) => setPlotType(e.target.value)} className={inputCls}>
          {PLOT_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <FieldLabel>X column</FieldLabel>
          <select value={xColumn} onChange={(e) => setXColumn(e.target.value)} className={inputClsSm}>
            {colOptions.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <FieldLabel>X label</FieldLabel>
          <input value={xLabel} onChange={(e) => setXLabel(e.target.value)} className={inputClsSm} />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <FieldLabel>Y column</FieldLabel>
          <select value={yColumn} onChange={(e) => setYColumn(e.target.value)} className={inputClsSm}>
            {colOptions.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <FieldLabel>Y label</FieldLabel>
          <input value={yLabel} onChange={(e) => setYLabel(e.target.value)} className={inputClsSm} />
        </div>
      </div>
      <div>
        <FieldLabel>Group by</FieldLabel>
        <select value={groupBy ?? ""} onChange={(e) => setGroupBy(e.target.value)} className={inputCls}>
          <option value="">None</option>
          {colOptions.filter((c) => c !== xColumn).map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="flex gap-2 pt-1">
        <button onClick={handleSave}
          className="flex-1 rounded-lg bg-primary-gradient px-3 py-2 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90">
          Save changes
        </button>
        <button onClick={onCancel}
          className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Text block forms ──────────────────────────────────────────────────────────
function TextSizePicker({ value, onChange }: { value: DashboardTextBlockDef["size"]; onChange: (v: DashboardTextBlockDef["size"]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {TEXT_SIZE_OPTIONS.map((opt) => (
        <button key={opt.value} onClick={() => onChange(opt.value)}
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            value === opt.value ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"
          }`}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function StylePicker({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex gap-1.5">
      <button onClick={() => onChange(false)}
        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
          !value ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"
        }`}>
        Plain text
      </button>
      <button onClick={() => onChange(true)}
        className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
          value ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:bg-secondary"
        }`}>
        Card
      </button>
    </div>
  );
}

function TextBlockEditForm({
  block, onSave, onCancel,
}: { block: DashboardTextBlockDef; onSave: (patch: Partial<DashboardTextBlockDef>) => void; onCancel: () => void }) {
  const [content, setContent] = useState(block.content);
  const [size, setSize] = useState<DashboardTextBlockDef["size"]>(block.size);
  const [width, setWidth] = useState<TileWidth>(block.width ?? "full");
  const [card, setCard] = useState<boolean>(block.card ?? false);

  return (
    <div className="space-y-3">
      <div>
        <FieldLabel>Size</FieldLabel>
        <TextSizePicker value={size} onChange={setSize} />
      </div>
      <div>
        <FieldLabel>Width</FieldLabel>
        <WidthPicker value={width} onChange={setWidth} />
      </div>
      <div>
        <FieldLabel>Style</FieldLabel>
        <StylePicker value={card} onChange={setCard} />
      </div>
      <div>
        <FieldLabel>Text</FieldLabel>
        <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={5}
          placeholder="Add a heading, note, or context for this section…"
          className={`resize-none ${inputCls}`} />
      </div>
      <div className="flex gap-2 pt-1">
        <button onClick={() => onSave({ content, size, width, card })}
          className="flex-1 rounded-lg bg-primary-gradient px-3 py-2 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90">
          Save changes
        </button>
        <button onClick={onCancel}
          className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary">
          Cancel
        </button>
      </div>
    </div>
  );
}

function AddTextForm({ onAdd, onCancel }: { onAdd: (block: DashboardTextBlockDef) => void; onCancel: () => void }) {
  const [content, setContent] = useState("");
  const [size, setSize] = useState<DashboardTextBlockDef["size"]>("h2");
  const [width, setWidth] = useState<TileWidth>("full");
  const [card, setCard] = useState(false);

  return (
    <div className="space-y-3">
      <div>
        <FieldLabel>Size</FieldLabel>
        <TextSizePicker value={size} onChange={setSize} />
      </div>
      <div>
        <FieldLabel>Width</FieldLabel>
        <WidthPicker value={width} onChange={setWidth} />
      </div>
      <div>
        <FieldLabel>Style</FieldLabel>
        <StylePicker value={card} onChange={setCard} />
      </div>
      <div>
        <FieldLabel>Text</FieldLabel>
        <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={5}
          placeholder="e.g. 'Q3 Revenue Overview' or a note for your team…"
          className={`resize-none ${inputCls}`} />
      </div>
      <div className="flex gap-2 pt-1">
        <button
          onClick={() => onAdd({ id: makeId("text"), size, content: content.trim(), width, card })}
          disabled={!content.trim()}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-2 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
          <Plus className="h-3.5 w-3.5" />Add to dashboard
        </button>
        <button onClick={onCancel}
          className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Add chart: from an existing chart's data, or straight from a notebook
// data source ────────────────────────────────────────────────────────────
function AddChartForm({
  baseCharts, dataSources, onFetchSourcePreview, onAdd, onCancel,
}: {
  baseCharts: ChartDef[];
  dataSources?: DashboardDataSourceRef[];
  onFetchSourcePreview?: FetchSourcePreview;
  onAdd: (chart: ChartDef) => void;
  onCancel: () => void;
}) {
  const hasSourceMode = Boolean(dataSources?.length && onFetchSourcePreview);
  const [mode, setMode] = useState<"chart" | "source">(baseCharts.length > 0 ? "chart" : "source");

  // ── "From existing chart" ──
  const [baseId, setBaseId] = useState(baseCharts[0]?.id ?? "");
  const baseChart = baseCharts.find((c) => c.id === baseId) ?? baseCharts[0];

  // ── "From notebook data" ──
  const [sourceId, setSourceId] = useState(dataSources?.[0]?.id ?? "");
  const [sourceRows, setSourceRows] = useState<Record<string, unknown>[]>([]);
  const [sourceColumns, setSourceColumns] = useState<string[]>([]);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "source" || !sourceId || !onFetchSourcePreview) return;
    let cancelled = false;
    setSourceLoading(true);
    setSourceError(null);
    onFetchSourcePreview(sourceId)
      .then((preview) => {
        if (cancelled) return;
        setSourceColumns(preview.columns);
        setSourceRows(preview.rows);
      })
      .catch((err) => {
        if (!cancelled) setSourceError(err instanceof Error ? err.message : "Failed to load data.");
      })
      .finally(() => { if (!cancelled) setSourceLoading(false); });
    return () => { cancelled = true; };
  }, [mode, sourceId, onFetchSourcePreview]);

  const columns = mode === "chart" ? (baseChart ? dataColumns(baseChart) : []) : sourceColumns;

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [plotType, setPlotType] = useState<string>(baseChart?.plot_type ?? "bar");
  const [xColumn, setXColumn] = useState(baseChart?.x.column ?? "");
  const [yColumn, setYColumn] = useState(baseChart?.y.column ?? "");
  const [groupBy, setGroupBy] = useState(baseChart?.group_by ?? "");

  // Reset column choices whenever the base chart changes so stale columns
  // from a previous base chart aren't submitted.
  useEffect(() => {
    if (mode !== "chart" || !baseChart) return;
    setXColumn(baseChart.x.column);
    setYColumn(baseChart.y.column);
    setGroupBy(baseChart.group_by ?? "");
    setPlotType(baseChart.plot_type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, baseId]);

  // Reset column choices once a data-source preview loads (or changes).
  useEffect(() => {
    if (mode !== "source" || sourceColumns.length === 0) return;
    setXColumn((prev) => (sourceColumns.includes(prev) ? prev : sourceColumns[0]));
    setYColumn((prev) => (sourceColumns.includes(prev) ? prev : sourceColumns[1] ?? sourceColumns[0]));
    setGroupBy((prev) => (prev && sourceColumns.includes(prev) ? prev : ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sourceColumns]);

  if (baseCharts.length === 0 && !hasSourceMode) {
    return <p className="text-sm text-muted-foreground">No charts or data sources available to build a new view from yet.</p>;
  }

  const inferTypeFromBaseChart = (column: string): string => {
    if (!baseChart) return "nominal";
    if (column === baseChart.x.column) return baseChart.x.type;
    if (column === baseChart.y.column) return baseChart.y.type;
    return "nominal";
  };

  const handleAdd = () => {
    if (mode === "chart") {
      if (!baseChart) return;
      onAdd({
        id: makeId("custom"),
        title: title.trim() || `${baseChart.title} (custom view)`,
        description: description.trim(),
        plot_type: plotType,
        analytical_type: "custom",
        x: { column: xColumn, label: xColumn, type: inferTypeFromBaseChart(xColumn) },
        y: { column: yColumn, label: yColumn, type: inferTypeFromBaseChart(yColumn) },
        group_by: groupBy || null,
        observation: null,
        data: baseChart.data,
      });
      return;
    }

    const source = dataSources?.find((d) => d.id === sourceId);
    if (!source || sourceRows.length === 0 || !xColumn || !yColumn) return;
    onAdd({
      id: makeId("custom"),
      title: title.trim() || `${source.label} — custom chart`,
      description: description.trim(),
      plot_type: plotType,
      analytical_type: "custom",
      x: { column: xColumn, label: xColumn, type: inferColumnType(sourceRows, xColumn) },
      y: { column: yColumn, label: yColumn, type: inferColumnType(sourceRows, yColumn) },
      group_by: groupBy || null,
      observation: null,
      data: sourceRows,
    });
  };

  const canAdd = mode === "chart"
    ? Boolean(baseChart)
    : sourceRows.length > 0 && Boolean(xColumn) && Boolean(yColumn);

  return (
    <div className="space-y-3">
      {hasSourceMode && baseCharts.length > 0 && (
        <div className="mb-1 flex gap-1 rounded-lg bg-secondary p-1">
          <button onClick={() => setMode("chart")}
            className={`flex-1 rounded-md py-1.5 text-xs font-medium ${
              mode === "chart" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            }`}>
            From existing chart
          </button>
          <button onClick={() => setMode("source")}
            className={`flex-1 rounded-md py-1.5 text-xs font-medium ${
              mode === "source" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            }`}>
            From notebook data
          </button>
        </div>
      )}

      {mode === "chart" ? (
        <div>
          <FieldLabel>Based on</FieldLabel>
          <select value={baseId} onChange={(e) => setBaseId(e.target.value)} className={inputCls}>
            {baseCharts.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
          <p className="mt-1 text-[11px] text-muted-foreground">
            New charts re-map an existing chart's already-fetched data — they don't run a fresh query.
          </p>
        </div>
      ) : (
        <div>
          <FieldLabel>Data source</FieldLabel>
          <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className={inputCls}>
            {(dataSources ?? []).map((d) => <option key={d.id} value={d.id}>{d.label || d.table_name}</option>)}
          </select>
          {sourceLoading && (
            <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />Loading a sample of this data…
            </p>
          )}
          {sourceError && <p className="mt-1.5 text-[11px] text-destructive">{sourceError}</p>}
          {!sourceLoading && !sourceError && sourceColumns.length > 0 && (
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              Builds the chart from a sample of this table's rows — not a live query.
            </p>
          )}
        </div>
      )}

      {columns.length > 0 && (
        <>
          <div>
            <FieldLabel>Title</FieldLabel>
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder={mode === "chart" ? `${baseChart?.title ?? ""} (custom view)` : "Custom chart"}
              className={inputCls} />
          </div>
          <div>
            <FieldLabel>Description</FieldLabel>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
              className={`resize-none ${inputCls}`} />
          </div>
          <div>
            <FieldLabel>Chart type</FieldLabel>
            <select value={plotType} onChange={(e) => setPlotType(e.target.value)} className={inputCls}>
              {PLOT_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <FieldLabel>X column</FieldLabel>
              <select value={xColumn} onChange={(e) => setXColumn(e.target.value)} className={inputClsSm}>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <FieldLabel>Y column</FieldLabel>
              <select value={yColumn} onChange={(e) => setYColumn(e.target.value)} className={inputClsSm}>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div>
            <FieldLabel>Group by</FieldLabel>
            <select value={groupBy ?? ""} onChange={(e) => setGroupBy(e.target.value)} className={inputCls}>
              <option value="">None</option>
              {columns.filter((c) => c !== xColumn).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </>
      )}

      <div className="flex gap-2 pt-1">
        <button onClick={handleAdd} disabled={!canAdd}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary-gradient px-3 py-2 text-xs font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40">
          <Plus className="h-3.5 w-3.5" />Add to dashboard
        </button>
        <button onClick={onCancel}
          className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary">
          Cancel
        </button>
      </div>
    </div>
  );
}

function SidebarChartRow({
  chart, hidden, isCustom, onToggleHidden, onEdit, onDelete,
}: {
  chart: ChartDef; hidden: boolean; isCustom: boolean;
  onToggleHidden: () => void; onEdit: () => void; onDelete?: () => void;
}) {
  return (
    <div className={`flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-2 ${hidden ? "opacity-50" : ""}`}>
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{chart.title}</span>
      <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
        {chart.plot_type}
      </span>
      <button onClick={onToggleHidden} title={hidden ? "Show on dashboard" : "Hide from dashboard"}
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        {hidden ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
      </button>
      <button onClick={onEdit} title="Edit"
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        <Pencil className="h-3.5 w-3.5" />
      </button>
      {isCustom && onDelete && (
        <button onClick={onDelete} title="Delete"
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

function SidebarTextRow({
  block, hidden, onToggleHidden, onEdit, onDelete,
}: {
  block: DashboardTextBlockDef; hidden: boolean;
  onToggleHidden: () => void; onEdit: () => void; onDelete: () => void;
}) {
  return (
    <div className={`flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-2 ${hidden ? "opacity-50" : ""}`}>
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
        {block.content || "Text block"}
      </span>
      <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
        Text
      </span>
      <button onClick={onToggleHidden} title={hidden ? "Show on dashboard" : "Hide from dashboard"}
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        {hidden ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
      </button>
      <button onClick={onEdit} title="Edit"
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        <Pencil className="h-3.5 w-3.5" />
      </button>
      <button onClick={onDelete} title="Delete"
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function CustomizeSidebar({
  view, setView, orderedCharts, orderedTextBlocks, overrides, removedIds, dataSources, onFetchSourcePreview,
  onToggleHidden, onDeleteCustom, onSaveEdit, onAddChart,
  onToggleTextHidden, onDeleteText, onSaveTextEdit, onAddText,
  onClose,
}: {
  view: SidebarView; setView: (v: SidebarView) => void;
  orderedCharts: ChartDef[]; orderedTextBlocks: DashboardTextBlockDef[];
  overrides: Record<string, ChartOverride>; removedIds: Set<string>;
  dataSources?: DashboardDataSourceRef[];
  onFetchSourcePreview?: FetchSourcePreview;
  onToggleHidden: (id: string) => void; onDeleteCustom: (id: string) => void;
  onSaveEdit: (id: string, patch: ChartOverride) => void; onAddChart: (chart: ChartDef) => void;
  onToggleTextHidden: (id: string) => void; onDeleteText: (id: string) => void;
  onSaveTextEdit: (id: string, patch: Partial<DashboardTextBlockDef>) => void; onAddText: (block: DashboardTextBlockDef) => void;
  onClose: () => void;
}) {
  if (view.tab === "add") {
    return (
      <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
        <SidebarHeader title="Add chart" onBack={() => setView({ tab: "list" })} onClose={onClose} />
        <div className="flex-1 overflow-y-auto p-4">
          <AddChartForm
            baseCharts={orderedCharts}
            dataSources={dataSources}
            onFetchSourcePreview={onFetchSourcePreview}
            onAdd={onAddChart}
            onCancel={() => setView({ tab: "list" })}
          />
        </div>
      </div>
    );
  }

  if (view.tab === "add-text") {
    return (
      <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
        <SidebarHeader title="Add text" onBack={() => setView({ tab: "list" })} onClose={onClose} />
        <div className="flex-1 overflow-y-auto p-4">
          <AddTextForm onAdd={onAddText} onCancel={() => setView({ tab: "list" })} />
        </div>
      </div>
    );
  }

  if (view.tab === "edit") {
    const chart = orderedCharts.find((c) => c.id === view.id);
    if (!chart) {
      return (
        <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
          <SidebarHeader title="Edit chart" onBack={() => setView({ tab: "list" })} onClose={onClose} />
          <div className="flex-1 overflow-y-auto p-4 text-sm text-muted-foreground">This chart no longer exists.</div>
        </div>
      );
    }
    return (
      <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
        <SidebarHeader title="Edit chart" onBack={() => setView({ tab: "list" })} onClose={onClose} />
        <div className="flex-1 overflow-y-auto p-4">
          <ChartEditForm
            chart={chart}
            columns={dataColumns(chart)}
            width={overrides[view.id]?.width ?? "third"}
            onSave={(patch) => onSaveEdit(view.id, patch)}
            onCancel={() => setView({ tab: "list" })}
          />
        </div>
      </div>
    );
  }

  if (view.tab === "edit-text") {
    const block = orderedTextBlocks.find((b) => b.id === view.id);
    if (!block) {
      return (
        <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
          <SidebarHeader title="Edit text" onBack={() => setView({ tab: "list" })} onClose={onClose} />
          <div className="flex-1 overflow-y-auto p-4 text-sm text-muted-foreground">This text block no longer exists.</div>
        </div>
      );
    }
    return (
      <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
        <SidebarHeader title="Edit text" onBack={() => setView({ tab: "list" })} onClose={onClose} />
        <div className="flex-1 overflow-y-auto p-4">
          <TextBlockEditForm
            block={block}
            onSave={(patch) => onSaveTextEdit(view.id, patch)}
            onCancel={() => setView({ tab: "list" })}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-80 shrink-0 flex-col border-l border-border bg-card">
      <SidebarHeader title="Customize dashboard" onClose={onClose} />
      <div className="flex-1 space-y-2 overflow-y-auto p-4">
        <div className="mb-1 flex gap-2">
          <button onClick={() => setView({ tab: "add" })}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-xs font-medium text-foreground hover:border-primary/50 hover:bg-secondary">
            <Plus className="h-3.5 w-3.5" />Add chart
          </button>
          <button onClick={() => setView({ tab: "add-text" })}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-xs font-medium text-foreground hover:border-primary/50 hover:bg-secondary">
            <Type className="h-3.5 w-3.5" />Add text
          </button>
        </div>
        {orderedCharts.map((chart) => (
          <SidebarChartRow
            key={chart.id}
            chart={applyOverride(chart, overrides[chart.id])}
            hidden={removedIds.has(chart.id)}
            isCustom={chart.id.startsWith("custom-")}
            onToggleHidden={() => onToggleHidden(chart.id)}
            onEdit={() => setView({ tab: "edit", id: chart.id })}
            onDelete={chart.id.startsWith("custom-") ? () => onDeleteCustom(chart.id) : undefined}
          />
        ))}
        {orderedTextBlocks.map((block) => (
          <SidebarTextRow
            key={block.id}
            block={block}
            hidden={removedIds.has(block.id)}
            onToggleHidden={() => onToggleTextHidden(block.id)}
            onEdit={() => setView({ tab: "edit-text", id: block.id })}
            onDelete={() => onDeleteText(block.id)}
          />
        ))}
        <p className="pt-2 text-[11px] leading-relaxed text-muted-foreground">
          Hover between any two tiles on the dashboard and click the "+" that appears to drop in a new text block
          right there — no need to open this panel first. Drag the grip icon on any tile to reorder it; drop it on
          the left or right half of another tile to slot in before or after it. Use the S/M/L/Full buttons on a
          tile (or its edit panel) to resize it — tiles that add up to a full row sit side by side, so you can
          place charts beside each other. Text blocks can stay plain (no card, drop it anywhere like a caption or
          heading) or get a card background to make them easier to grab.
        </p>
      </div>
    </div>
  );
}

// ── PDF report export ─────────────────────────────────────────────────────────
// No PDF library dependency: charts are rasterized client-side (same
// canvas technique ChartBlock uses for its own PNG download) into a
// standalone HTML document, opened in a new tab, and handed to the
// browser's native print dialog — "Save as PDF" there produces the file.
// This also means the report always reflects exactly what's currently on
// screen: current order, edits, hidden tiles, custom tiles, text blocks,
// notes, and filters. The report itself stays single-column for print
// legibility even though the on-screen dashboard can lay tiles side by side.

function escapeHtml(s: string): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Serialize a rendered chart's <svg> to a white-background PNG data URL,
 * independent of the app's current (possibly dark) theme — print output
 * should stay legible on paper regardless of on-screen theme. */
async function svgToPngDataUrl(svgEl: SVGSVGElement): Promise<string | null> {
  const rect = svgEl.getBoundingClientRect();
  const width = rect.width || svgEl.viewBox.baseVal.width || 800;
  const height = rect.height || svgEl.viewBox.baseVal.height || 400;

  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));

  const svgString = new XMLSerializer().serializeToString(clone);
  const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl = URL.createObjectURL(svgBlob);

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale = 2; // fixed higher-res scale for crisp print output
      const canvas = document.createElement("canvas");
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext("2d");
      if (!ctx) { URL.revokeObjectURL(svgUrl); resolve(null); return; }
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, width, height);
      URL.revokeObjectURL(svgUrl);
      resolve(canvas.toDataURL("image/png"));
    };
    img.onerror = () => { URL.revokeObjectURL(svgUrl); resolve(null); };
    img.src = svgUrl;
  });
}

/** Build one chart's report section. Table-type charts are re-rendered as a
 * real HTML table from chart.data (crisper than a screenshot); every other
 * plot type is rasterized from its live <svg> found inside `tileEl`. */
async function buildChartSection(
  chart: ChartDef,
  tileEl: HTMLElement | null | undefined,
  hint?: string,
): Promise<string> {
  let mediaHtml: string;

  if (chart.plot_type === "table") {
    const cols = chart.data.length ? Object.keys(chart.data[0]) : [];
    mediaHtml = cols.length
      ? `<table class="pdf-table"><thead><tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>` +
        `<tbody>${chart.data.slice(0, 200).map((row) =>
          `<tr>${cols.map((c) => `<td>${escapeHtml(String(row[c] ?? ""))}</td>`).join("")}</tr>`
        ).join("")}</tbody></table>`
      : `<p class="pdf-empty">No data available.</p>`;
  } else {
    const svg = tileEl?.querySelector("svg") ?? null;
    const dataUrl = svg ? await svgToPngDataUrl(svg as SVGSVGElement) : null;
    mediaHtml = dataUrl
      ? `<img class="pdf-chart-img" src="${dataUrl}" />`
      : `<p class="pdf-empty">This chart couldn't be rendered for the report.</p>`;
  }

  return `
    <section class="pdf-chart">
      <h2>${escapeHtml(chart.title)}</h2>
      ${chart.description ? `<p class="pdf-desc">${escapeHtml(chart.description)}</p>` : ""}
      ${mediaHtml}
      ${chart.observation ? `<p class="pdf-observation">${escapeHtml(chart.observation)}</p>` : ""}
      ${hint && hint.trim() ? `<p class="pdf-hint"><strong>Note:</strong> ${escapeHtml(hint)}</p>` : ""}
    </section>`;
}

/** Render one text block as its own report section, matching its on-screen
 * size (h1/h2/h3/body/caption) with the closest print-friendly heading tag. */
function buildTextSection(block: DashboardTextBlockDef): string {
  const tagBySize: Record<DashboardTextBlockDef["size"], string> = {
    h1: "h1", h2: "h2", h3: "h3", body: "p", caption: "p",
  };
  const classBySize: Record<DashboardTextBlockDef["size"], string> = {
    h1: "pdf-text-h1", h2: "pdf-text-h2", h3: "pdf-text-h3", body: "pdf-text-body", caption: "pdf-text-caption",
  };
  const tag = tagBySize[block.size];
  const cls = classBySize[block.size];
  return `<section class="pdf-text ${cls}"><${tag}>${escapeHtml(block.content)}</${tag}></section>`;
}

const PDF_REPORT_CSS = `
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #1a1a1a; margin: 0; padding: 32px 40px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 0 0 6px; }
  .pdf-header { margin-bottom: 20px; border-bottom: 2px solid #1a1a1a; padding-bottom: 12px; }
  .pdf-meta { font-size: 11px; color: #666; margin: 0; }
  .pdf-filters { font-size: 11px; color: #666; margin: 4px 0 0; }
  .pdf-summary { margin-bottom: 24px; padding: 14px 16px; background: #f4f4f5; border-radius: 8px; }
  .pdf-summary p { margin: 0 0 8px; font-size: 13px; line-height: 1.5; }
  .pdf-findings { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.6; }
  .pdf-chart { break-inside: avoid; page-break-inside: avoid; margin-bottom: 28px; padding-bottom: 20px;
    border-bottom: 1px solid #e5e5e5; }
  .pdf-desc { font-size: 12px; color: #555; margin: 0 0 10px; }
  .pdf-chart-img { max-width: 100%; height: auto; border: 1px solid #e5e5e5; border-radius: 6px; }
  .pdf-observation { margin-top: 10px; font-size: 12px; line-height: 1.5; padding: 10px 12px;
    background: #f4f4f5; border-radius: 6px; }
  .pdf-hint { margin-top: 8px; font-size: 12px; line-height: 1.5; color: #444; font-style: italic; }
  .pdf-empty { font-size: 12px; color: #999; font-style: italic; }
  .pdf-table { border-collapse: collapse; width: 100%; font-size: 11px; }
  .pdf-table th, .pdf-table td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
  .pdf-table th { background: #f4f4f5; }
  .pdf-text { break-inside: avoid; page-break-inside: avoid; margin-bottom: 16px; }
  .pdf-text-h1 h1 { font-size: 24px; margin: 0; }
  .pdf-text-h2 h2 { font-size: 18px; margin: 0; }
  .pdf-text-h3 h3 { font-size: 14px; margin: 0; font-weight: 600; }
  .pdf-text-body p { font-size: 13px; margin: 0; line-height: 1.5; }
  .pdf-text-caption p { font-size: 11px; margin: 0; color: #777; }
  @media print { @page { margin: 16mm; } body { padding: 0; } }
`;

function VersionsDropdown({
  versions, viewingVersionId, onSelect,
}: {
  versions: DashboardVersion[];
  viewingVersionId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);

  const activeVersion = viewingVersionId ? versions.find((v) => v.id === viewingVersionId) : null;
  const activeLabel = activeVersion ? `Version ${activeVersion.version_number}` : "Current";

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium hover:bg-secondary ${
          viewingVersionId ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-foreground"
        }`}>
        <History className="h-3.5 w-3.5" />
        {activeLabel}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-72 w-64 overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-xl">
          <button
            onClick={() => { onSelect(null); setOpen(false); }}
            className={`flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-xs hover:bg-secondary ${
              !viewingVersionId ? "font-medium text-foreground" : "text-muted-foreground"
            }`}
          >
            <span>Current</span>
            {!viewingVersionId && <Check className="h-3 w-3 text-primary" />}
          </button>

          {versions.length === 0 && (
            <p className="px-2.5 py-2 text-xs text-muted-foreground">No archived versions yet.</p>
          )}

          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => { onSelect(v.id); setOpen(false); }}
              className={`flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left hover:bg-secondary ${
                viewingVersionId === v.id ? "bg-secondary" : ""
              }`}
            >
              <span className="flex w-full items-center justify-between gap-2 text-xs font-medium text-foreground">
                Version {v.version_number}
                {viewingVersionId === v.id && <Check className="h-3 w-3 shrink-0 text-primary" />}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {new Date(v.created_at).toLocaleString()}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Dashboard modal — rendered "in place" of the notebook workspace, not
// as a viewport-covering overlay. The root uses `absolute inset-0` and
// expects its parent (NotebookWorkspace's root) to be `position: relative`,
// so it fills the workspace area *below* the notebook header instead of
// hiding it. The chart-expand modal inside ChartBlock is unaffected — that
// one is a genuine full-screen zoom and keeps `fixed inset-0`. ─────────────
export function DashboardModal({
  charts, summary, loading = false, error = null, onClose, onRegenerate, regenerating = false,
  initialLayout, onLayoutChange, saveStatus = "idle",
  versions = [], dataSources, onFetchSourcePreview,
}: {
  charts: ChartDef[]; summary?: Summary; loading?: boolean; error?: string | null;
  onClose: () => void; onRegenerate: () => void; regenerating?: boolean;
  /** Restored from the backend when reopening a saved dashboard. */
  initialLayout?: DashboardLayout | null;
  /** Fires whenever customization state changes — parent should debounce-save. */
  onLayoutChange?: (layout: DashboardLayoutPayload) => void;
  /** Reflects the parent's last save attempt for a small status indicator. */
  saveStatus?: "idle" | "saving" | "saved" | "error";
  /** Archived snapshots from previous regenerations. */
  versions?: DashboardVersion[];
  dataSources?: DashboardDataSourceRef[];
  onFetchSourcePreview?: FetchSourcePreview;
}) {
  const [filters, setFilters] = useState<Record<string, Set<string>>>({});
  const [viewingVersionId, setViewingVersionId] = useState<string | null>(null);
  const [showAnalysis, setShowAnalysis] = useState(true);

  const parsedInitial = layoutFromPayload(initialLayout);

  // ── Customization state ──────────────────────────────────────────────────
  const [order, setOrder] = useState<string[]>(() =>
    parsedInitial.order.length > 0 ? parsedInitial.order : charts.map((c) => c.id)
  );
  const [overrides, setOverrides] = useState<Record<string, ChartOverride>>(() => parsedInitial.overrides);
  const [customCharts, setCustomCharts] = useState<ChartDef[]>(() => parsedInitial.customCharts);
  const [textBlocks, setTextBlocks] = useState<DashboardTextBlockDef[]>(() => parsedInitial.textBlocks);
  const [removedIds, setRemovedIds] = useState<Set<string>>(() => parsedInitial.removedIds);
  const [hints, setHints] = useState<Record<string, string>>(() => {
    const base = { ...parsedInitial.hints };
    for (const c of charts) {
      if (c.hint && !(c.id in base)) base[c.id] = c.hint;
    }
    return base;
  });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarView, setSidebarView] = useState<SidebarView>({ tab: "list" });
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [dragOverSide, setDragOverSide] = useState<"before" | "after" | null>(null);
  const [dragOverBar, setDragOverBar] = useState<string>(""); // key of the insertion bar being hovered ("" | "start" | id-before)
  // Newly-inserted-or-clicked text block currently being typed into inline,
  // directly on the canvas, without the sidebar involved.
  const [inlineEditingId, setInlineEditingId] = useState<string | null>(null);

  const setHint = (id: string, value: string) => setHints((prev) => ({ ...prev, [id]: value }));

  const viewingVersion = viewingVersionId
    ? versions.find((v) => v.id === viewingVersionId) ?? null
    : null;
  const isReadOnly = viewingVersion !== null;

  // When browsing a version, derive display state from that snapshot.
  const activeCharts = useMemo(() => {
    if (!viewingVersion) return charts;
    return viewingVersion.charts.filter((c) => !c.error).map(notebookChartToDef);
  }, [viewingVersion, charts]);

  const activeSummary = useMemo(() => {
    if (!viewingVersion) return summary;
    return { dataset_description: viewingVersion.reply, key_findings: [], recommended_next_steps: [] };
  }, [viewingVersion, summary]);

  const versionLayout = useMemo(
    () => (viewingVersion ? layoutFromPayload(viewingVersion.dashboard_layout) : null),
    [viewingVersion],
  );

  const effectiveOrder = isReadOnly && versionLayout
    ? (versionLayout.order.length > 0 ? versionLayout.order : activeCharts.map((c) => c.id))
    : order;
  const effectiveOverrides = isReadOnly && versionLayout ? versionLayout.overrides : overrides;
  const effectiveCustomCharts = isReadOnly && versionLayout ? versionLayout.customCharts : customCharts;
  const effectiveTextBlocks = isReadOnly && versionLayout ? versionLayout.textBlocks : textBlocks;
  const effectiveRemovedIds = isReadOnly && versionLayout ? versionLayout.removedIds : removedIds;
  const effectiveHints = isReadOnly && versionLayout ? versionLayout.hints : hints;

  // ── PDF export ────────────────────────────────────────────────────────────
  const tileRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);

  // Reconcile local order whenever the source chart list changes (e.g. a
  // fresh Regenerate) — keep the position of ids that still exist (original,
  // custom, or text-block), append any newly-generated ones at the end, and
  // prune overrides/hidden-flags/notes for ids that vanished so stale state
  // doesn't pile up. On regenerate, reset to a fresh layout (text blocks are
  // user-authored content unrelated to the underlying data, so they're
  // preserved across a regenerate rather than wiped).
  useEffect(() => {
    setViewingVersionId(null);
    const incomingIds = new Set(charts.map((c) => c.id));
    if (incomingIds.size === 0 && charts.length === 0 && !initialLayout) {
      setOrder([]);
      setOverrides({});
      setCustomCharts([]);
      setTextBlocks([]);
      setRemovedIds(new Set());
      setHints({});
      return;
    }
    if (!initialLayout) {
      setOrder(charts.map((c) => c.id));
      setOverrides({});
      setCustomCharts([]);
      setTextBlocks((prev) => prev); // preserve any text blocks added this session
      setRemovedIds(new Set());
      setHints(Object.fromEntries(charts.filter((c) => c.hint).map((c) => [c.id, c.hint as string])));
      return;
    }
    const parsed = layoutFromPayload(initialLayout);
    const customIds = new Set(parsed.customCharts.map((c) => c.id));
    const textIds = new Set(parsed.textBlocks.map((b) => b.id));
    setOrder(() => {
      const kept = parsed.order.filter((id) => incomingIds.has(id) || customIds.has(id) || textIds.has(id));
      const added = charts.map((c) => c.id).filter((id) => !kept.includes(id));
      return kept.length > 0 ? [...kept, ...added] : charts.map((c) => c.id);
    });
    setOverrides(parsed.overrides);
    setCustomCharts(parsed.customCharts);
    setTextBlocks(parsed.textBlocks);
    setRemovedIds(parsed.removedIds);
    setHints(() => {
      const next = { ...parsed.hints };
      for (const c of charts) {
        if (c.hint && !(c.id in next)) next[c.id] = c.hint;
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [charts]);

  const byId = useMemo(() => {
    const map = new Map<string, ChartDef>();
    for (const c of activeCharts) map.set(c.id, c);
    for (const c of effectiveCustomCharts) map.set(c.id, c);
    return map;
  }, [activeCharts, effectiveCustomCharts]);

  const textById = useMemo(() => {
    const map = new Map<string, DashboardTextBlockDef>();
    for (const b of effectiveTextBlocks) map.set(b.id, b);
    return map;
  }, [effectiveTextBlocks]);

  const orderedCharts = useMemo(
    () => effectiveOrder.filter((id) => byId.has(id)).map((id) => applyOverride(byId.get(id)!, effectiveOverrides[id])),
    [effectiveOrder, byId, effectiveOverrides]
  );

  const orderedTextBlocks = useMemo(
    () => effectiveOrder.filter((id) => textById.has(id)).map((id) => textById.get(id)!),
    [effectiveOrder, textById]
  );

  // Combined, order-preserving list of tile ids (charts + text blocks) —
  // drives the actual grid render below.
  const orderedTileIds = useMemo(
    () => effectiveOrder.filter((id) => byId.has(id) || textById.has(id)),
    [effectiveOrder, byId, textById]
  );

  const visibleTileIds = useMemo(
    () => orderedTileIds.filter((id) => !effectiveRemovedIds.has(id)),
    [orderedTileIds, effectiveRemovedIds]
  );

  const visibleCharts = useMemo(
    () => orderedCharts.filter((c) => !effectiveRemovedIds.has(c.id)),
    [orderedCharts, effectiveRemovedIds]
  );

  useEffect(() => {
    if (isReadOnly || loading) return;
    onLayoutChange?.(payloadFromState(order, overrides, customCharts, textBlocks, removedIds, hints));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order, overrides, customCharts, textBlocks, removedIds, hints, isReadOnly, loading]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const toggleFilterValue = (column: string, value: string) => {
    setFilters((prev) => {
      const set = new Set(prev[column] ?? []);
      set.has(value) ? set.delete(value) : set.add(value);
      return { ...prev, [column]: set };
    });
  };
  const clearFilters = () => setFilters({});
  const activeFilterCount = Object.values(filters).reduce((n, s) => n + s.size, 0);

  const filterDefs = useMemo(() => computeGlobalFilters(visibleCharts), [visibleCharts]);
  const filteredCharts = useMemo(
    () => visibleCharts.map((c) => applyGlobalFilters(c, filters)),
    [visibleCharts, filters]
  );
  // Charts, post-filter, keyed by id — text blocks aren't subject to
  // row-level filters, so the combined grid render below pulls from here
  // for chart tiles and from `textById` directly for text tiles.
  const filteredChartsById = useMemo(() => {
    const map = new Map<string, ChartDef>();
    for (const c of filteredCharts) map.set(c.id, c);
    return map;
  }, [filteredCharts]);

  // ── Drag & drop reordering ───────────────────────────────────────────────
  // Dropping directly on a tile inserts before/after it depending on which
  // half of the tile the cursor is over (a left-half drop slots the dragged
  // tile in before, right-half after) — finer-grained than always inserting
  // before the target. Dropping on one of the thin "+" bars between rows
  // inserts at that exact position regardless of horizontal cursor position.
  const handleDragStart = (id: string) => (e: DragEvent<HTMLButtonElement>) => {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  };
  const handleTileDragOver = (id: string) => (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (id === draggedId) return;
    setDragOverBar("");
    setDragOverId(id);
    const rect = e.currentTarget.getBoundingClientRect();
    setDragOverSide(e.clientX - rect.left < rect.width / 2 ? "before" : "after");
  };
  const handleTileDrop = (id: string) => (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const sourceId = draggedId ?? e.dataTransfer.getData("text/plain");
    const side = dragOverSide ?? "before";
    setDragOverId(null);
    setDragOverSide(null);
    setDraggedId(null);
    if (!sourceId || sourceId === id) return;
    setOrder((prev) => {
      const next = prev.filter((x) => x !== sourceId);
      let targetIdx = next.indexOf(id);
      if (targetIdx === -1) return prev;
      if (side === "after") targetIdx += 1;
      next.splice(targetIdx, 0, sourceId);
      return next;
    });
  };
  const handleDragEnd = () => { setDraggedId(null); setDragOverId(null); setDragOverSide(null); setDragOverBar(""); };

  // Insertion at a "+" bar — `beforeId` is the id the bar sits in front of
  // (null means the very end of the grid). Used both for dropping a
  // dragged tile at that exact spot and for the bar's own click-to-add.
  const insertionIndex = (beforeId: string | null, prev: string[]) => {
    if (!beforeId) return prev.length;
    const idx = prev.indexOf(beforeId);
    return idx === -1 ? prev.length : idx;
  };
  const handleBarDragOver = (barKey: string) => (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverId(null);
    setDragOverSide(null);
    setDragOverBar(barKey);
  };
  const handleBarDrop = (beforeId: string | null) => (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const sourceId = draggedId ?? e.dataTransfer.getData("text/plain");
    setDragOverBar("");
    setDraggedId(null);
    if (!sourceId) return;
    setOrder((prev) => {
      const next = prev.filter((x) => x !== sourceId);
      next.splice(insertionIndex(beforeId, next), 0, sourceId);
      return next;
    });
  };

  // ── Customization actions — charts ───────────────────────────────────────
  const toggleHidden = (id: string) => {
    setRemovedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };
  const deleteCustomChart = (id: string) => {
    setCustomCharts((prev) => prev.filter((c) => c.id !== id));
    setOrder((prev) => prev.filter((x) => x !== id));
    setOverrides((prev) => { const { [id]: _drop, ...rest } = prev; return rest; });
    setRemovedIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    setHints((prev) => { const { [id]: _drop, ...rest } = prev; return rest; });
  };
  const saveEdit = (id: string, patch: ChartOverride) => {
    setOverrides((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
    setSidebarView({ tab: "list" });
  };
  const updateChartWidth = (id: string, width: TileWidth) => {
    setOverrides((prev) => ({ ...prev, [id]: { ...prev[id], width } }));
  };
  const addChart = (chart: ChartDef) => {
    setCustomCharts((prev) => [...prev, chart]);
    setOrder((prev) => [...prev, chart.id]);
    setSidebarView({ tab: "list" });
  };

  // ── Customization actions — text blocks ──────────────────────────────────
  // Quick add: drops a blank text block at the given position and puts it
  // straight into inline edit mode, so typing starts exactly where the
  // cursor clicked — no sidebar round-trip required.
  const insertTextBlockAt = (beforeId: string | null) => {
    const block: DashboardTextBlockDef = { id: makeId("text"), size: "body", content: "", width: "full", card: false };
    setTextBlocks((prev) => [...prev, block]);
    setOrder((prev) => {
      const next = [...prev];
      next.splice(insertionIndex(beforeId, next), 0, block.id);
      return next;
    });
    setInlineEditingId(block.id);
  };
  const addTextBlock = (block: DashboardTextBlockDef) => {
    setTextBlocks((prev) => [...prev, block]);
    setOrder((prev) => [...prev, block.id]);
    setSidebarView({ tab: "list" });
  };
  const saveTextEdit = (id: string, patch: Partial<DashboardTextBlockDef>) => {
    setTextBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)));
    setSidebarView({ tab: "list" });
  };
  const updateTextWidth = (id: string, width: TileWidth) => {
    setTextBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, width } : b)));
  };
  const toggleTextCard = (id: string) => {
    setTextBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, card: !(b.card ?? false) } : b)));
  };
  const deleteTextBlock = (id: string) => {
    setTextBlocks((prev) => prev.filter((b) => b.id !== id));
    setOrder((prev) => prev.filter((x) => x !== id));
    setRemovedIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    setInlineEditingId((prev) => (prev === id ? null : prev));
  };
  // Blurring inline edit with no content removes the (freshly-created,
  // otherwise-empty) block rather than leaving a dead tile on the canvas;
  // otherwise the typed content is committed.
  const commitInlineEdit = (id: string, content: string) => {
    const trimmed = content.trim();
    if (!trimmed) { deleteTextBlock(id); return; }
    setTextBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, content: trimmed } : b)));
    setInlineEditingId((prev) => (prev === id ? null : prev));
  };

  const openSidebar = (view: SidebarView = { tab: "list" }) => {
    setSidebarView(view);
    setSidebarOpen(true);
  };

  const handleDownloadPdf = async () => {
    if (pdfBusy || visibleTileIds.length === 0) return;
    setPdfBusy(true);
    setPdfError(null);
    try {
      const sections = await Promise.all(
        visibleTileIds.map((id) => {
          const chart = filteredChartsById.get(id);
          if (chart) return buildChartSection(chart, tileRefs.current[id], hints[id]);
          const block = textById.get(id);
          return Promise.resolve(block ? buildTextSection(block) : "");
        })
      );

      const filterSummary = activeFilterCount > 0
        ? `<p class="pdf-filters">Filtered by: ${Object.entries(filters)
            .filter(([, s]) => s.size > 0)
            .map(([col, s]) => `${escapeHtml(col)} = ${Array.from(s).map(escapeHtml).join(", ")}`)
            .join(" · ")}</p>`
        : "";

      const findingsHtml = (summary?.key_findings ?? []).length
        ? `<ul class="pdf-findings">${summary!.key_findings.map((f) => `<li>${escapeHtml(f.finding)}</li>`).join("")}</ul>`
        : "";

      const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>Dashboard report</title>
<style>${PDF_REPORT_CSS}</style>
</head>
<body>
  <header class="pdf-header">
    <h1>Dashboard report</h1>
    <p class="pdf-meta">Generated ${escapeHtml(new Date().toLocaleString())}</p>
    ${filterSummary}
  </header>
  ${summary?.dataset_description
    ? `<section class="pdf-summary"><p>${escapeHtml(summary.dataset_description)}</p>${findingsHtml}</section>`
    : ""}
  <main>${sections.join("\n")}</main>
</body>
</html>`;

      const printWindow = window.open("", "_blank");
      if (!printWindow) {
        setPdfError("Your browser blocked the report window — allow pop-ups for this site and try again.");
        return;
      }
      printWindow.document.open();
      printWindow.document.write(html);
      printWindow.document.close();
      printWindow.focus();
      // Print on a short delay rather than window.onload — onload for the
      // initial blank popup often fires before document.write runs, so a
      // handler attached after write() may never be called and the tab
      // would just sit empty. The embedded chart images are base64 data
      // URLs (already fully loaded), so a brief delay is enough for layout.
      setTimeout(() => { printWindow.print(); }, 350);
    } catch (err) {
      setPdfError(err instanceof Error ? err.message : "Failed to build the report.");
    } finally {
      setPdfBusy(false);
    }
  };

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-background">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-card px-5 py-3">
        <div className="flex items-center gap-2 min-w-0">
          <LayoutDashboard className="h-4 w-4 shrink-0 text-primary" />
          <h2 className="text-sm font-semibold text-foreground shrink-0">Dashboard</h2>
          {!loading && !error && (
            <VersionsDropdown
              versions={versions}
              viewingVersionId={viewingVersionId}
              onSelect={setViewingVersionId}
            />
          )}
          {isReadOnly && (
            <span className="rounded-full border border-border bg-secondary px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
              Viewing archived version
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!loading && !error && !isReadOnly && (
            <>
              {saveStatus === "saving" && (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />Saving…
                </span>
              )}
              {saveStatus === "saved" && (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Cloud className="h-3 w-3" />Saved
                </span>
              )}
              {saveStatus === "error" && (
                <span className="flex items-center gap-1 text-[10px] text-destructive">
                  <CloudOff className="h-3 w-3" />Save failed
                </span>
              )}
            </>
          )}
          {!loading && !error && (
            <button onClick={() => setShowAnalysis((v) => !v)}
              title={showAnalysis ? "Hide agent analysis" : "Show agent analysis"}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary">
              {showAnalysis ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              {showAnalysis ? "Hide analysis" : "Show analysis"}
            </button>
          )}
          {!loading && !error && !isReadOnly && (
            <button onClick={() => (sidebarOpen ? setSidebarOpen(false) : openSidebar())}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-secondary ${
                sidebarOpen ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-foreground"
              }`}>
              {sidebarOpen ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
              Customize
            </button>
          )}
          {!loading && !error && !isReadOnly && (
            <button onClick={handleDownloadPdf} disabled={pdfBusy || visibleTileIds.length === 0}
              title="Opens a print-ready report — choose &quot;Save as PDF&quot; in the print dialog"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
              {pdfBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDown className="h-3.5 w-3.5" />}
              Download PDF
            </button>
          )}
          <button onClick={onRegenerate} disabled={regenerating || loading || isReadOnly}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-40">
            {regenerating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            Regenerate
          </button>
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {pdfError && (
        <div className="flex shrink-0 items-center justify-between border-b border-border bg-destructive/5 px-5 py-2 text-xs text-destructive">
          <span className="flex items-center gap-1.5"><AlertCircle className="h-3.5 w-3.5 shrink-0" />{pdfError}</span>
          <button onClick={() => setPdfError(null)} className="text-muted-foreground hover:text-foreground">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {filterDefs.length > 0 && !loading && (
        <div className="shrink-0 border-b border-border bg-secondary/30 px-5 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Filters</span>
            {filterDefs.map((f) => (
              <FilterDropdown key={f.column} filterDef={f}
                selected={filters[f.column] ?? new Set()}
                onToggle={(v) => toggleFilterValue(f.column, v)} />
            ))}
            {activeFilterCount > 0 && (
              <button onClick={clearFilters} className="text-xs text-primary hover:underline">
                Clear all ({activeFilterCount})
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <div className="flex-1 overflow-y-auto scrollbar-thin px-5 py-5">
          {loading ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
              Building your dashboard…
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4 shrink-0" />{error}
            </div>
          ) : (
            <>
              {showAnalysis && activeSummary?.dataset_description && (
                <div className="mb-4 rounded-xl border border-border bg-card p-4">
                  <p className="text-sm leading-relaxed text-muted-foreground">{activeSummary.dataset_description}</p>
                </div>
              )}
              {visibleTileIds.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-3 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2"><AlertCircle className="h-4 w-4 shrink-0" />
                    {effectiveRemovedIds.size > 0 ? "All tiles are hidden." : "No charts to display."}
                  </div>
                  {effectiveRemovedIds.size > 0 && !isReadOnly && (
                    <button onClick={() => openSidebar()} className="text-xs text-primary hover:underline">
                      Open Customize to bring one back
                    </button>
                  )}
                  {!isReadOnly && (
                    <button onClick={() => insertTextBlockAt(null)} className="text-xs text-primary hover:underline">
                      Or click here to add a text block
                    </button>
                  )}
                </div>
              ) : (
                // 6-unit dense grid: each tile's own width (S/M/L/Full) decides
                // how much of a row it takes, and `dense` packing lets smaller
                // tiles fill in beside each other instead of always starting a
                // fresh row — this is what lets two "half" charts, or a chart
                // and a text note, actually sit side by side. A thin "+"
                // insertion bar runs before the first tile, between every
                // pair, and after the last one, so a new text block can be
                // dropped in at any point without opening Customize.
                <div className="grid grid-cols-6 gap-4 [grid-auto-flow:dense]">
                  {!isReadOnly && (
                    <InsertTextBar
                      onInsert={() => insertTextBlockAt(visibleTileIds[0] ?? null)}
                      dragOver={dragOverBar === "start"}
                      onDragOver={handleBarDragOver("start")}
                      onDragLeave={() => setDragOverBar((prev) => (prev === "start" ? "" : prev))}
                      onDrop={handleBarDrop(visibleTileIds[0] ?? null)}
                    />
                  )}
                  {visibleTileIds.map((id, i) => {
                    const nextId = visibleTileIds[i + 1] ?? null;
                    const tile = (() => {
                      const chart = filteredChartsById.get(id);
                      if (chart) {
                        const chartWidth = effectiveOverrides[id]?.width ?? "third";
                        return (
                          <div key={id} ref={(el) => { tileRefs.current[id] = el; }} className={widthSpanClass(chartWidth)}>
                            <DashboardTile
                              chart={chart}
                              editable={sidebarOpen && !isReadOnly}
                              readOnly={isReadOnly}
                              dragging={draggedId === id}
                              dragOver={dragOverId === id}
                              dragOverSide={dragOverId === id ? dragOverSide : null}
                              hint={effectiveHints[id] ?? ""}
                              onHintChange={(v) => setHint(id, v)}
                              width={chartWidth}
                              onWidthChange={(w) => updateChartWidth(id, w)}
                              onDragStart={handleDragStart(id)}
                              onDragOver={handleTileDragOver(id)}
                              onDragLeave={() => setDragOverId((prev) => (prev === id ? null : prev))}
                              onDrop={handleTileDrop(id)}
                              onDragEnd={handleDragEnd}
                              onEdit={() => openSidebar({ tab: "edit", id })}
                              onHide={() => toggleHidden(id)}
                            />
                          </div>
                        );
                      }
                      const block = textById.get(id);
                      if (block) {
                        const textWidth = block.width ?? "full";
                        const textCard = block.card ?? false;
                        return (
                          <div key={id} className={widthSpanClass(textWidth)}>
                            <TextBlockTile
                              block={block}
                              editable={!isReadOnly}
                              dragging={draggedId === id}
                              dragOver={dragOverId === id}
                              dragOverSide={dragOverId === id ? dragOverSide : null}
                              width={textWidth}
                              card={textCard}
                              isEditing={inlineEditingId === id}
                              onDragStart={handleDragStart(id)}
                              onDragOver={handleTileDragOver(id)}
                              onDragLeave={() => setDragOverId((prev) => (prev === id ? null : prev))}
                              onDrop={handleTileDrop(id)}
                              onDragEnd={handleDragEnd}
                              onEdit={() => openSidebar({ tab: "edit-text", id })}
                              onHide={() => toggleHidden(id)}
                              onWidthChange={(w) => updateTextWidth(id, w)}
                              onCardToggle={() => toggleTextCard(id)}
                              onStartInlineEdit={() => setInlineEditingId(id)}
                              onCommitInlineEdit={(content) => commitInlineEdit(id, content)}
                            />
                          </div>
                        );
                      }
                      return null;
                    })();
                    return (
                      <Fragment key={id}>
                        {tile}
                        {!isReadOnly && (
                          <InsertTextBar
                            onInsert={() => insertTextBlockAt(nextId)}
                            dragOver={dragOverBar === id}
                            onDragOver={handleBarDragOver(id)}
                            onDragLeave={() => setDragOverBar((prev) => (prev === id ? "" : prev))}
                            onDrop={handleBarDrop(nextId)}
                          />
                        )}
                      </Fragment>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {sidebarOpen && !loading && !error && !isReadOnly && (
          <CustomizeSidebar
            view={sidebarView}
            setView={setSidebarView}
            orderedCharts={orderedCharts}
            orderedTextBlocks={orderedTextBlocks}
            overrides={overrides}
            removedIds={removedIds}
            dataSources={dataSources}
            onFetchSourcePreview={onFetchSourcePreview}
            onToggleHidden={toggleHidden}
            onDeleteCustom={deleteCustomChart}
            onSaveEdit={saveEdit}
            onAddChart={addChart}
            onToggleTextHidden={toggleHidden}
            onDeleteText={deleteTextBlock}
            onSaveTextEdit={saveTextEdit}
            onAddText={addTextBlock}
            onClose={() => setSidebarOpen(false)}
          />
        )}
      </div>
    </div>
  );
}
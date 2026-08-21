import { useState, useEffect, useMemo, useRef, ReactNode } from "react";
import { ResponsiveBar } from "@nivo/bar";
import { ResponsiveLine } from "@nivo/line";
import { ResponsiveScatterPlot } from "@nivo/scatterplot";
import { ResponsiveHeatMap } from "@nivo/heatmap";
import { ResponsivePie } from "@nivo/pie";
import { ResponsiveRadar } from "@nivo/radar";
import { ResponsiveBullet } from "@nivo/bullet";
import { ResponsiveCalendar } from "@nivo/calendar";
import { ResponsiveChord } from "@nivo/chord";
import { ResponsiveCirclePacking } from "@nivo/circle-packing";
import { ResponsiveFunnel } from "@nivo/funnel";
import { ResponsiveMarimekko } from "@nivo/marimekko";
import { ResponsiveNetwork } from "@nivo/network";
import { ResponsiveParallelCoordinates } from "@nivo/parallel-coordinates";
import { ResponsiveRadialBar } from "@nivo/radial-bar";
import { ResponsiveSankey } from "@nivo/sankey";
import { ResponsiveSunburst } from "@nivo/sunburst";
import { ResponsiveSwarmPlot } from "@nivo/swarmplot";
import { ResponsiveTreeMap } from "@nivo/treemap";
import { ResponsiveVoronoi } from "@nivo/voronoi";
import { ResponsiveWaffle } from "@nivo/waffle";
import { Maximize2, X, ChevronDown, ChevronUp, BarChart2, TrendingUp, ScatterChartIcon as ScatterIcon, Grid, AlertCircle, Info, Download, Table2, PieChart, Radar, MapPin, Loader, Moon, Sun, Heart, Monitor, Layout, Zap, Menu, Circle, Trash2, FileText } from "lucide-react";
import type { ChartDef } from "@/types";

// ── Theme tokens — reads from CSS vars so dark/light mode is automatic ────────
// Nivo needs concrete hex values; we read them from the document at render time.
function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function useThemeTokens() {
  const [t, setT] = useState(() => buildTokens());
  useEffect(() => {
    const obs = new MutationObserver(() => setT(buildTokens()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => obs.disconnect();
  }, []);
  return t;
}

function buildTokens() {
  return {
    bg:         cssVar("--background",        "#0f1117"),
    surface:    cssVar("--card",               "#181c27"),
    border:     cssVar("--border",             "#252a38"),
    text:       cssVar("--foreground",         "#e2e8f0"),
    textMuted:  cssVar("--muted-foreground",   "#94a3b8"),
    accent:     cssVar("--primary",            "#4f8ef7"),
  };
}

// Nivo chart theme built from tokens
function nivoTheme(t: ReturnType<typeof buildTokens>) {
  return {
    background: "transparent",
    text: { fill: t.textMuted, fontSize: 11, fontFamily: "inherit" },
    axis: {
      domain: { line: { stroke: t.border, strokeWidth: 1 } },
      ticks: { line: { stroke: t.border, strokeWidth: 1 }, text: { fill: t.textMuted, fontSize: 11 } },
      legend: { text: { fill: t.textMuted, fontSize: 12, fontWeight: 600 } },
    },
    grid: { line: { stroke: t.border, strokeDasharray: "3 3" } },
    legends: { text: { fill: t.textMuted, fontSize: 11 } },
    tooltip: {
      container: {
        background: t.surface, border: `1px solid ${t.border}`,
        borderRadius: 8, padding: "8px 12px", fontSize: 12,
        color: t.text, boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
      },
    },
    crosshair: { line: { stroke: t.textMuted, strokeWidth: 1, strokeOpacity: 0.5 } },
    labels: { text: { fill: t.text, fontSize: 11 } },
    dots: { text: { fill: t.textMuted, fontSize: 10 } },
  };
}

const PALETTE = ["#4f8ef7","#34d399","#fbbf24","#f87171","#a78bfa","#f472b6","#38bdf8","#fb923c"];

// Cap on distinct categories rendered by charts with no natural density
// control (pie, waffle, funnel, radial-bar, treemap/sunburst/circle-packing
// leaves) — past this, slices/segments become illegible slivers. The long
// tail is collapsed into a single "Other" bucket rather than silently
// dropped, so the chart stays honest about what it's summarizing.
const TOP_N_CATEGORIES = 20;

// ── Display formatting helpers ──────────────────────────────────────────────
// These are purely cosmetic (chart labels/ticks/tooltips) — they never touch
// the underlying chart.data, so CSV export and the raw table view still show
// full-precision numbers and untruncated strings.

/**
 * Compact a number for axis ticks / data labels: 1000 -> "1k", 1500000 ->
 * "1.5M". Falls back to a plain, comma-free numeric string under 1000.
 * Small magnitudes and non-finite values are returned as-is (formatted to
 * at most 2 decimal places) so percentages, ratios, etc. stay readable.
 */
function formatCompactNumber(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);

  const abs = Math.abs(n);
  if (abs < 1000) {
    return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100);
  }

  const units: { threshold: number; suffix: string }[] = [
    { threshold: 1e12, suffix: "T" },
    { threshold: 1e9, suffix: "B" },
    { threshold: 1e6, suffix: "M" },
    { threshold: 1e3, suffix: "k" },
  ];
  const unit = units.find((u) => abs >= u.threshold)!;
  const scaled = n / unit.threshold;
  // Two significant decimals under 100, whole numbers past that, so
  // "999.99k" doesn't collide visually with "1M".
  const rounded = Math.abs(scaled) < 100 ? Math.round(scaled * 10) / 10 : Math.round(scaled);
  return `${rounded}${unit.suffix}`;
}

/** Trim a string label to a max length for axis ticks / chart labels. */
function trimLabel(value: unknown, maxLen: number = 12): string {
  const s = String(value ?? "");
  return s.length > maxLen ? `${s.slice(0, Math.max(1, maxLen - 1))}…` : s;
}

/**
 * Convert a raw chart.data row's field into a neat display string. Numeric
 * fields (or numeric-looking strings) get compacted (1000 -> 1k); anything
 * else gets trimmed to maxLen characters.
 */
function formatCellForDisplay(value: unknown, maxLen: number = 24): string {
  if (typeof value === "number") return formatCompactNumber(value);
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return formatCompactNumber(Number(value));
  }
  return trimLabel(value, maxLen);
}

/**
 * Collapse a long tail of categories into a single "Other" bucket so
 * pie/waffle/funnel/etc. stay legible instead of rendering dozens of
 * slivers. Returns the trimmed list plus how many categories were folded
 * in, so callers can surface an honest notice instead of silently
 * dropping data.
 */
function aggregateTopN(
  rows: { key: string; value: number }[],
  n: number = TOP_N_CATEGORIES,
): { items: { key: string; value: number }[]; collapsedCount: number } {
  if (rows.length <= n) return { items: rows, collapsedCount: 0 };
  const sorted = [...rows].sort((a, b) => b.value - a.value);
  const top = sorted.slice(0, n);
  const rest = sorted.slice(n);
  const otherTotal = rest.reduce((sum, r) => sum + r.value, 0);
  return {
    items: [...top, { key: `Other (${rest.length})`, value: otherTotal }],
    collapsedCount: rest.length,
  };
}

// ── Shared UI atoms ────────────────────────────────────────────────────────────
function Observation({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="mt-4 flex gap-2.5 rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <p className="text-sm leading-relaxed text-foreground/80">{text}</p>
    </div>
  );
}

// Amber/caveat tone (distinct from the blue `Observation` insight box) —
// this communicates "here's what you're NOT seeing", not an analytical
// finding, so it gets its own visual register.
function DataNotice({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="mb-3 flex gap-2 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{text}</span>
    </div>
  );
}

/** Human-readable notice when a chart's `data` is a capped view of a larger result. */
function chartDataNotice(chart: ChartDef): string {
  if (chart.truncated && chart.total_row_count) {
    return `Showing ${chart.data.length.toLocaleString()} of ${chart.total_row_count.toLocaleString()} rows — download CSV for the full result set, not just this chart's capped view.`;
  }
  return "";
}

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-center">
      <AlertCircle className="h-5 w-5 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

// Nivo's Responsive* charts stretch to fill their parent — with lots of
// categories/rows that means bars/cells get crushed into illegibility.
// This wraps them in a horizontally scrollable track whose inner width
// scales with item count, so density stays readable and the chart just
// gets wider (scrollable) instead of squeezed. minWidth of 0 renders as a
// normal, non-scrolling full-width chart.
function ScrollableChart({
  minWidth, height, children,
}: { minWidth: number; height: number; children: ReactNode }) {
  return (
    <div className="overflow-y-hidden scrollbar-thin -mx-1 px-1">
      <div style={{ width: minWidth > 0 ? minWidth : "100%", minWidth: "100%", height }}>
        {children}
      </div>
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-5xl rounded-xl border border-border bg-card shadow-2xl"
        style={{ maxHeight: "92vh", overflowY: "auto" }}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

// ── Generic hierarchy builder ───────────────────────────────────────────────
// Shared by CirclePacking / Sunburst / Treemap — all three consume the same
// { name, children: [...] } shape from Nivo. Each level is passed through
// aggregateTopN so a high-cardinality leaf set collapses into "Other"
// instead of rendering hundreds of illegible slivers. Leaf/group names are
// trimmed here too, since these charts don't expose a per-label formatter.
function buildHierarchy(chart: ChartDef): { name: string; children: any[] } | null {
  const { x, y, group_by } = chart;
  if (!chart.data.length) return null;

  if (group_by) {
    const groups = new Map<string, { key: string; value: number }[]>();
    chart.data.forEach((d) => {
      const g = trimLabel(d[group_by], 18);
      const leaf = { key: trimLabel(d[x.column], 18), value: Number(d[y.column]) || 0 };
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g)!.push(leaf);
    });
    return {
      name: "root",
      children: Array.from(groups.entries()).map(([name, leaves]) => ({
        name,
        children: aggregateTopN(leaves, TOP_N_CATEGORIES).items.map((l) => ({ name: l.key, value: l.value })),
      })),
    };
  }

  const raw = chart.data.map((d) => ({
    key: trimLabel(d[x.column], 18),
    value: Number(d[y.column]) || 0,
  }));
  // Top-level (ungrouped) hierarchies can tolerate a slightly larger cap
  // since there's only one ring/level of slices to keep legible.
  return {
    name: "root",
    children: aggregateTopN(raw, TOP_N_CATEGORIES * 2).items.map((l) => ({ name: l.key, value: l.value })),
  };
}

// ── Bar chart ──────────────────────────────────────────────────────────────────
function BarPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const data = useMemo(() => {
    if (!group_by) {
      return chart.data.map((d) => ({ [x.column]: String(d[x.column]), [y.column]: Number(d[y.column]) || 0 }));
    }
    // Grouped: pivot so each x-value has keys per group
    const map = new Map<string, Record<string, number>>();
    chart.data.forEach((d) => {
      const xk = String(d[x.column]);
      const gk = String(d[group_by!]);
      if (!map.has(xk)) map.set(xk, { [x.column]: xk as any });
      map.get(xk)![gk] = Number(d[y.column]) || 0;
    });
    return Array.from(map.values());
  }, [chart.data, x.column, y.column, group_by]);

  const groupKeys = group_by
    ? [...new Set(chart.data.map((d) => String(d[group_by])))]
    : [y.column];

  if (!data.length) return <EmptyChart message="No data to display." />;

  // Minimum px per category so bars/labels stay legible; grouped bars need
  // more room per category since each group's series renders side by side.
  const perCategory = group_by ? Math.max(28, groupKeys.length * 22) : 34;
  const minWidth = data.length > 8 ? 200 + data.length * perCategory : 0;
  const dense = data.length > 8;

  return (
    <ScrollableChart minWidth={minWidth} height={height}>
      <ResponsiveBar
        data={data}
        keys={groupKeys}
        indexBy={x.column}
        theme={nivoTheme(t)}
        colors={PALETTE}
        padding={0.3}
        innerPadding={group_by ? 3 : 0}
        groupMode={group_by ? "grouped" : "stacked"}
        borderRadius={4}
        axisBottom={{ tickRotation: dense ? -20 : 0, legend: x.label, legendOffset: dense ? 58 : 36, legendPosition: "middle", format: (v) => trimLabel(v, dense ? 10 : 14) }}
        axisLeft={{ legend: y.label, legendOffset: -52, legendPosition: "middle", format: (v) => formatCompactNumber(v) }}
        margin={{ top: 12, right: group_by ? 140 : 16, bottom: dense ? 72 : 48, left: 56 }}
        enableLabel={data.length <= 15}
        label={(d) => formatCompactNumber(d.value)}
        labelSkipWidth={20}
        labelSkipHeight={20}
        labelTextColor={{ from: "color", modifiers: [["darker", 2.5]] }}
        animate
        motionConfig="gentle"
        legends={group_by ? [{
          dataFrom: "keys", anchor: "bottom-right", direction: "column",
          translateX: 120, itemWidth: 150, itemHeight: 18, itemTextColor: t.textMuted,
          symbolSize: 10, symbolShape: "circle",
        }] : []}
        tooltip={({ id, value, indexValue }) => (
          <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground shadow-xl">
            <span className="font-semibold">{trimLabel(indexValue, 40)}</span>
            <span className="mx-2 text-muted-foreground">·</span>
            {group_by && <span className="text-muted-foreground">{trimLabel(id, 24)}: </span>}
            <span className="font-bold">{formatCompactNumber(value)}</span>
          </div>
        )}
      />
    </ScrollableChart>
  );
}

// ── Line / Area chart ────────────────────────────────────────────────────────
function parseDateSafe(v: unknown): number | null {
  if (v == null) return null;
  const t = new Date(String(v)).getTime();
  return Number.isNaN(t) ? null : t;
}

function LinePlot({ chart, height, forceArea = false }: { chart: ChartDef; height: number; forceArea?: boolean }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;
  const isTemporal = x.type === "temporal";

  const nivoData = useMemo(() => {
    const toPoint = (d: Record<string, unknown>) => {
      if (!isTemporal) {
        return { x: d[x.column], y: Number(d[y.column]) || 0 };
      }
      const ts = parseDateSafe(d[x.column]);
      if (ts === null) return null; // skip unparseable dates instead of throwing
      // xScale below uses format:"native", which tells Nivo/d3 the x values
      // are already Date instances — handing it an ISO string instead makes
      // d3's time scale call .getTime() on a string and throw ("n2.getTime
      // is not a function").
      return { x: new Date(ts), y: Number(d[y.column]) || 0 };
    };

    if (!group_by) {
      const sorted = [...chart.data].sort((a, b) => {
        const av = isTemporal ? parseDateSafe(a[x.column]) ?? 0 : Number(a[x.column]) || 0;
        const bv = isTemporal ? parseDateSafe(b[x.column]) ?? 0 : Number(b[x.column]) || 0;
        return av - bv;
      });
      const data = sorted.map(toPoint).filter((p): p is { x: any; y: number } => p !== null);
      return [{ id: y.label, color: PALETTE[0], data }];
    }

    // Grouped series
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    return groups.map((g, i) => {
      const rows = chart.data
        .filter((d) => String(d[group_by]) === g)
        .sort((a, b) => {
          const av = isTemporal ? parseDateSafe(a[x.column]) ?? 0 : Number(a[x.column]) || 0;
          const bv = isTemporal ? parseDateSafe(b[x.column]) ?? 0 : Number(b[x.column]) || 0;
          return av - bv;
        });
      const data = rows.map(toPoint).filter((p): p is { x: any; y: number } => p !== null);
      return { id: g, color: PALETTE[i % PALETTE.length], data };
    });
  }, [chart.data, x.column, y.column, group_by, isTemporal]);

  if (!nivoData.length || !nivoData[0].data.length) return <EmptyChart message="No data to display." />;

  // Temporal (time-scale) lines compress fine at any density — only
  // categorical/point-scale x-axes get crowded with many distinct points.
  const pointCount = Math.max(...nivoData.map((s) => s.data.length));
  const needsScroll = !isTemporal && pointCount > 15;
  const minWidth = needsScroll ? 96 + pointCount * 36 : 0;

  return (
    <ScrollableChart minWidth={minWidth} height={height}>
      <ResponsiveLine
        data={nivoData}
        theme={nivoTheme(t)}
        colors={PALETTE}
        xScale={isTemporal ? { type: "time", format: "native", precision: "day" } : { type: "point" }}
        xFormat={isTemporal ? "time:%b %d %Y" : undefined}
        yScale={{ type: "linear", stacked: forceArea, nice: true }}
        axisBottom={{
          format: isTemporal ? "%b %y" : (v: unknown) => trimLabel(v, 12),
          legend: x.label, legendOffset: 36, legendPosition: "middle",
          tickRotation: isTemporal ? -30 : (needsScroll ? -40 : 0),
        }}
        axisLeft={{ legend: y.label, legendOffset: -44, legendPosition: "middle", format: (v) => formatCompactNumber(v) }}
        margin={{ top: 12, right: nivoData.length > 1 ? 140 : 16, bottom: 52, left: 60 }}
        curve="monotoneX"
        lineWidth={2.5}
        pointSize={nivoData[0].data.length < 40 ? 6 : 0}
        pointBorderWidth={2}
        pointBorderColor={{ from: "serieColor" }}
        pointColor={{ theme: "background" }}
        enableSlices="x"
        sliceTooltip={({ slice }) => (
          <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground shadow-xl space-y-1">
            {slice.points.map((p) => (
              <div key={p.id} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: p.serieColor }} />
                <span className="text-muted-foreground">{trimLabel(p.serieId, 24)}:</span>
                <span className="font-semibold">{formatCompactNumber(p.data.y)}</span>
              </div>
            ))}
          </div>
        )}
        legends={nivoData.length > 1 ? [{
          anchor: "bottom-right", direction: "column", translateX: 130,
          itemWidth: 120, itemHeight: 18, itemTextColor: t.textMuted,
          symbolSize: 10, symbolShape: "circle",
        }] : []}
        animate
        motionConfig="gentle"
        enableArea={forceArea || nivoData.length === 1}
        areaOpacity={forceArea ? 0.35 : 0.08}
      />
    </ScrollableChart>
  );
}

// ── Scatter plot ───────────────────────────────────────────────────────────────
function ScatterPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const MAX_POINTS_PER_SERIES = 1500;
  const toPoints = (rows: Record<string, unknown>[]) => {
    const pts = rows.map((d) => ({ x: Number(d[x.column]) || 0, y: Number(d[y.column]) || 0 }));
    if (pts.length <= MAX_POINTS_PER_SERIES) return pts;
    const stride = Math.ceil(pts.length / MAX_POINTS_PER_SERIES);
    return pts.filter((_, i) => i % stride === 0);
  };

  const nivoData = useMemo(() => {
    if (!group_by) {
      return [{ id: y.label, data: toPoints(chart.data) }];
    }
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    return groups.map((g, i) => ({
      id: g,
      data: toPoints(chart.data.filter((d) => String(d[group_by]) === g)),
    }));
  }, [chart.data, x.column, y.column, group_by]);

  const wasSampled = chart.data.length > nivoData.reduce((n, s) => n + s.data.length, 0);

  if (!nivoData.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {wasSampled && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Showing a sampled subset of points for readability.
        </p>
      )}
      <div style={{ height: wasSampled ? height - 20 : height }}>
        <ResponsiveScatterPlot
          data={nivoData}
          theme={nivoTheme(t)}
          colors={PALETTE}
          xScale={{ type: "linear", min: "auto", max: "auto", nice: true }}
          yScale={{ type: "linear", min: "auto", max: "auto", nice: true }}
          axisBottom={{ legend: x.label, legendOffset: 46, legendPosition: "middle", format: (v) => formatCompactNumber(v) }}
          axisLeft={{ legend: y.label, legendOffset: -66, legendPosition: "middle", format: (v) => formatCompactNumber(v) }}
          margin={{ top: 12, right: nivoData.length > 1 ? 140 : 16, bottom: 52, left: 80 }}
          nodeSize={7}
          blendMode="normal"
          tooltip={({ node }) => (
            <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground shadow-xl">
              <span className="font-semibold text-muted-foreground">{trimLabel(node.serieId, 24)}</span>
              <div className="mt-1 space-y-0.5">
                <div>{x.label}: <span className="font-bold">{formatCompactNumber(node.data.x)}</span></div>
                <div>{y.label}: <span className="font-bold">{formatCompactNumber(node.data.y)}</span></div>
              </div>
            </div>
          )}
          legends={nivoData.length > 1 ? [{
            anchor: "bottom-right", direction: "column", translateX: 130,
            itemWidth: 120, itemHeight: 18, itemTextColor: t.textMuted,
            symbolSize: 10, symbolShape: "circle",
          }] : []}
          animate
          motionConfig="gentle"
        />
      </div>
    </div>
  );
}

// ── Heatmap ────────────────────────────────────────────────────────────────────
function HeatMapPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const valueCol = useMemo(() => {
    if (group_by) return group_by;
    const sample = chart.data[0];
    if (!sample) return "value";
    return Object.keys(sample).find(
      (k) => k !== x.column && k !== y.column && typeof sample[k] === "number"
    ) ?? "value";
  }, [chart.data, x.column, y.column, group_by]);

  // Nivo heatmap expects: [{ id: rowVal, data: [{ x: colVal, y: numericVal }] }]
  const nivoData = useMemo(() => {
    const rowMap = new Map<string, Map<string, number>>();
    chart.data.forEach((d) => {
      const row = String(d[y.column]);
      const col = String(d[x.column]);
      const val = Number(d[valueCol]) || 0;
      if (!rowMap.has(row)) rowMap.set(row, new Map());
      rowMap.get(row)!.set(col, val);
    });
    return Array.from(rowMap.entries()).map(([id, cols]) => ({
      id,
      data: Array.from(cols.entries()).map(([x, y]) => ({ x, y })),
    }));
  }, [chart.data, x.column, y.column, valueCol]);

  if (!nivoData.length) return <EmptyChart message="No data to display." />;

  // Scale inner width by column count and inner height by row count so
  // cells keep a legible minimum size instead of getting squashed into a
  // fixed-size card when there are many rows/columns.
  const colCount = new Set(chart.data.map((d) => String(d[x.column]))).size;
  const rowCount = nivoData.length;
  const minWidth = colCount > 10 ? 116 + colCount * 44 : 0;
  const rowHeight = 28;
  const effectiveHeight = Math.max(height, 60 + rowCount * rowHeight);

  return (
    <ScrollableChart minWidth={minWidth} height={effectiveHeight}>
      <ResponsiveHeatMap
        data={nivoData}
        theme={nivoTheme(t)}
        colors={{ type: "sequential", scheme: "blues" }}
        axisTop={{ tickRotation: -30, legend: x.label, legendOffset: -46, format: (v: unknown) => trimLabel(v, 10) }}
        axisLeft={{ legend: y.label, legendOffset: -56, legendPosition: "middle", format: (v: unknown) => trimLabel(v, 14) }}
        margin={{ top: 60, right: 16, bottom: 24, left: 100 }}
        cellComponent="rect"
        borderWidth={1}
        borderColor={{ from: "color", modifiers: [["darker", 0.4]] }}
        animate
        motionConfig="gentle"
        label={(d) => formatCompactNumber(d.value)}
        tooltip={({ cell }) => (
          <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground shadow-xl">
            <div>{x.label}: <span className="font-bold">{trimLabel(cell.data.x, 40)}</span></div>
            <div>{y.label}: <span className="font-bold">{trimLabel(cell.id, 40)}</span></div>
            <div>Value: <span className="font-bold">{formatCompactNumber(cell.data.y)}</span></div>
          </div>
        )}
        emptyColor={t.border}
      />
    </ScrollableChart>
  );
}

// ── Boxplot (hand-rolled SVG — nivo boxplot had breaking API changes) ──────────
interface BoxStats {
  key: string; min: number; q1: number; median: number; q3: number;
  max: number; outliers: number[]; count: number; mean: number;
}

function computeBox(key: string, values: number[]): BoxStats {
  const s = [...values].sort((a, b) => a - b);
  const q = (p: number) => {
    const i = (s.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
    return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (i - lo);
  };
  const q1 = q(0.25), median = q(0.5), q3 = q(0.75), iqr = q3 - q1;
  const fence = [q1 - 1.5 * iqr, q3 + 1.5 * iqr];
  const inliers = s.filter((v) => v >= fence[0] && v <= fence[1]);
  return {
    key, q1, median, q3, count: s.length,
    mean: s.reduce((a, b) => a + b, 0) / s.length,
    min: inliers[0] ?? s[0],
    max: inliers[inliers.length - 1] ?? s[s.length - 1],
    outliers: s.filter((v) => v < fence[0] || v > fence[1]),
  };
}

function BoxPlot({ chart, height = 360 }: { chart: ChartDef; height?: number }) {
  const t = useThemeTokens();
  const { x, y } = chart;
  const [hovered, setHovered] = useState<string | null>(null);

  const groups = useMemo(() => {
    const map = new Map<string, number[]>();
    chart.data.forEach((d) => {
      const k = String(d[x.column]);
      const v = Number(d[y.column]);
      if (Number.isNaN(v)) return;
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(v);
    });
    return Array.from(map.entries())
      .filter(([, vs]) => vs.length > 0)
      .map(([k, vs]) => computeBox(k, vs))
      .sort((a, b) => a.median - b.median);
  }, [chart.data, x.column, y.column]);

  if (!groups.length) return <EmptyChart message="Not enough numeric data for distribution." />;

  const allVals = groups.flatMap((g) => [g.min, g.max, ...g.outliers]);
  const yMin = Math.min(...allVals), yMax = Math.max(...allVals);
  const pad = (yMax - yMin) * 0.08 || 1;
  const dMin = yMin - pad, dMax = yMax + pad;

  const mg = { top: 12, right: 16, bottom: 52, left: 60 };
  const svgW = Math.max(520, groups.length * 80);
  const plotW = svgW - mg.left - mg.right;
  const plotH = height - mg.top - mg.bottom;
  const bw = Math.min(44, (plotW / groups.length) * 0.42);
  const ys = (v: number) => mg.top + plotH - ((v - dMin) / (dMax - dMin || 1)) * plotH;
  const xs = (i: number) => mg.left + (plotW / groups.length) * (i + 0.5);
  const ticks = 5;
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => dMin + ((dMax - dMin) / ticks) * i);
  const active = groups.find((g) => g.key === hovered);

  return (
    <div className="overflow-x-auto scrollbar-thin">
      <svg width={svgW} height={height} style={{ minWidth: "100%", overflow: "visible" }}>
        {/* Grid + y-axis ticks */}
        {tickVals.map((tv, i) => (
          <g key={i}>
            <line x1={mg.left} x2={svgW - mg.right} y1={ys(tv)} y2={ys(tv)} stroke={t.border} strokeDasharray="3 3" />
            <text x={mg.left - 8} y={ys(tv)} textAnchor="end" dominantBaseline="middle" fontSize={10} fill={t.textMuted}>
              {formatCompactNumber(Math.round(tv * 100) / 100)}
            </text>
          </g>
        ))}
        {/* Y-axis label */}
        <text transform={`rotate(-90)`} x={-(mg.top + plotH / 2)} y={16}
          textAnchor="middle" fontSize={11} fill={t.textMuted} fontWeight={600}>{y.label}</text>

        {/* Boxes */}
        {groups.map((g, i) => {
          const cx = xs(i);
          const isActive = hovered === g.key;
          const color = isActive ? "#4f8ef7" : "#4f8ef755";
          const strokeColor = "#4f8ef7";
          return (
            <g key={g.key} onMouseEnter={() => setHovered(g.key)} onMouseLeave={() => setHovered(null)}
              style={{ cursor: "pointer" }}>
              {/* Whiskers */}
              <line x1={cx} x2={cx} y1={ys(g.max)} y2={ys(g.q3)} stroke={t.textMuted} strokeWidth={1.5} />
              <line x1={cx} x2={cx} y1={ys(g.q1)} y2={ys(g.min)} stroke={t.textMuted} strokeWidth={1.5} />
              <line x1={cx - bw / 4} x2={cx + bw / 4} y1={ys(g.max)} y2={ys(g.max)} stroke={t.textMuted} strokeWidth={1.5} />
              <line x1={cx - bw / 4} x2={cx + bw / 4} y1={ys(g.min)} y2={ys(g.min)} stroke={t.textMuted} strokeWidth={1.5} />
              {/* IQR box */}
              <rect x={cx - bw / 2} y={ys(g.q3)} width={bw}
                height={Math.max(1, ys(g.q1) - ys(g.q3))}
                fill={color} stroke={strokeColor} strokeWidth={1.5} rx={3} />
              {/* Median line */}
              <line x1={cx - bw / 2} x2={cx + bw / 2} y1={ys(g.median)} y2={ys(g.median)}
                stroke="#fff" strokeWidth={2} />
              {/* Mean dot */}
              <circle cx={cx} cy={ys(g.mean)} r={3} fill="#fbbf24" stroke="#0f1117" strokeWidth={1} />
              {/* Outlier dots */}
              {g.outliers.map((o, oi) => (
                <circle key={oi} cx={cx + (Math.random() - 0.5) * bw * 0.5} cy={ys(o)} r={2.5}
                  fill="#f87171" fillOpacity={0.7} />
              ))}
              {/* x-tick label */}
              <text x={cx} y={height - mg.bottom + 16} textAnchor="middle" fontSize={10} fill={t.textMuted}>
                {trimLabel(g.key, 10)}
              </text>
            </g>
          );
        })}
        {/* X-axis label */}
        <text x={mg.left + plotW / 2} y={height - 4} textAnchor="middle" fontSize={11} fill={t.textMuted} fontWeight={600}>{x.label}</text>
        {/* Legend */}
        <g transform={`translate(${svgW - mg.right - 100}, ${mg.top})`}>
          <circle cx={5} cy={5} r={4} fill="#fff" stroke="#4f8ef7" strokeWidth={1.5} />
          <text x={14} y={9} fontSize={10} fill={t.textMuted}>Median</text>
          <circle cx={5} cy={22} r={3} fill="#fbbf24" stroke="#0f1117" strokeWidth={1} />
          <text x={14} y={26} fontSize={10} fill={t.textMuted}>Mean</text>
          <circle cx={5} cy={39} r={2.5} fill="#f87171" fillOpacity={0.7} />
          <text x={14} y={43} fontSize={10} fill={t.textMuted}>Outlier</text>
        </g>
      </svg>

      {active && (
        <div className="mt-3 flex flex-wrap items-center gap-4 rounded-lg border border-border bg-card px-4 py-2.5 text-xs text-foreground">
          <span className="font-semibold">{trimLabel(active.key, 40)}</span>
          <span className="text-muted-foreground">min <span className="text-foreground font-medium">{formatCompactNumber(active.min)}</span></span>
          <span className="text-muted-foreground">Q1 <span className="text-foreground font-medium">{formatCompactNumber(active.q1)}</span></span>
          <span className="text-muted-foreground">median <span className="text-foreground font-medium">{formatCompactNumber(active.median)}</span></span>
          <span className="text-muted-foreground">Q3 <span className="text-foreground font-medium">{formatCompactNumber(active.q3)}</span></span>
          <span className="text-muted-foreground">max <span className="text-foreground font-medium">{formatCompactNumber(active.max)}</span></span>
          <span className="text-muted-foreground">n <span className="text-foreground font-medium">{active.count}</span></span>
          {active.outliers.length > 0 && (
            <span className="text-red-400">{active.outliers.length} outlier{active.outliers.length > 1 ? "s" : ""}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Download helpers ───────────────────────────────────────────────────────────

/** Trigger a browser file download for the given Blob. */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Export a DOM element's first `<svg>` child (or the element itself if it is
 * an `<svg>`) as a PNG by serializing it to an SVG data URL and drawing it
 * onto a hidden <canvas>. Works for all Nivo charts (which render SVG) and
 * for the hand-rolled BoxPlot SVG.
 *
 * Nivo's HTML-canvas-based charts (Voronoi, some interactive layers) may not
 * expose an <svg> — in that case this silently no-ops, same as before.
 */
async function exportChartAsPng(
  containerEl: HTMLElement,
  title: string,
): Promise<void> {
  const svg = containerEl.querySelector("svg") ?? (containerEl.tagName === "svg" ? containerEl : null);
  if (!svg) return;

  const svgEl = svg as SVGSVGElement;
  // Grab rendered dimensions — fall back to viewBox if getBoundingClientRect is zero
  const rect = svgEl.getBoundingClientRect();
  const width  = rect.width  || svgEl.viewBox.baseVal.width  || 800;
  const height = rect.height || svgEl.viewBox.baseVal.height || 400;

  // Clone and set explicit dimensions so the canvas renders at full size
  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("width",  String(width));
  clone.setAttribute("height", String(height));

  const serializer = new XMLSerializer();
  const svgString  = serializer.serializeToString(clone);
  const svgBlob    = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
  const svgUrl     = URL.createObjectURL(svgBlob);

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale  = window.devicePixelRatio || 1;
      const canvas = document.createElement("canvas");
      canvas.width  = width  * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext("2d")!;
      // Fill with the card background colour so transparent charts look right
      ctx.fillStyle = getComputedStyle(document.documentElement)
        .getPropertyValue("--card").trim() || "#181c27";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, width, height);
      URL.revokeObjectURL(svgUrl);
      canvas.toBlob((blob) => {
        if (blob) triggerDownload(blob, `${title}.png`);
        resolve();
      }, "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(svgUrl); resolve(); };
    img.src = svgUrl;
  });
}

/**
 * Convert chart.data rows to a CSV string and trigger a download. Uses the
 * raw, untrimmed/un-compacted values — CSV export is the "full precision"
 * escape hatch, so display-only trimming never applies here.
 */
function exportChartAsCsv(data: Record<string, unknown>[], title: string): void {
  if (!data.length) return;
  const keys = Object.keys(data[0]);
  const escape = (v: unknown) => {
    const s = String(v ?? "");
    return s.includes(",") || s.includes('"') || s.includes("\n")
      ? `"${s.replace(/"/g, '""')}"`
      : s;
  };
  const rows = [keys.join(","), ...data.map((row) => keys.map((k) => escape(row[k])).join(","))];
  const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  triggerDownload(blob, `${title}.csv`);
}

// ── Chart type metadata ────────────────────────────────────────────────────────
// Keys here MUST match the kebab-case `plot_type` strings emitted by
// TabularDataExploratoryAgent._PLOT_TYPES / ChartDef["plot_type"] — a
// mismatch here (e.g. the old "circlePacking" camelCase key) means the
// chart silently falls through to the "unsupported" EmptyChart branch even
// though a real component exists for it.
const CHART_META: Record<string, { icon: typeof BarChart2; label: string }> = {
  bar:                    { icon: BarChart2,   label: "Bar" },
  line:                   { icon: TrendingUp,  label: "Line" },
  area:                   { icon: TrendingUp,  label: "Area" },
  scatter:                { icon: ScatterIcon, label: "Scatter" },
  heatmap:                { icon: Grid,        label: "Heatmap" },
  boxplot:                { icon: BarChart2,   label: "Distribution" },
  table:                  { icon: Table2,      label: "View" },
  pie:                    { icon: PieChart,    label: "Pie" },
  radar:                  { icon: Radar,       label: "Radar" },
  bullet:                 { icon: BarChart2,   label: "Bullet" },
  bump:                   { icon: TrendingUp,  label: "Bump" },
  calendar:               { icon: Grid,        label: "Calendar" },
  chord:                  { icon: ScatterIcon, label: "Chord" },
  "circle-packing":       { icon: PieChart,    label: "Circle Packing" },
  funnel:                 { icon: TrendingUp,  label: "Funnel" },
  geo:                    { icon: MapPin,      label: "Geo" },
  marimekko:              { icon: BarChart2,   label: "Marimekko" },
  network:                { icon: ScatterIcon, label: "Network" },
  "parallel-coordinates": { icon: ScatterIcon, label: "Parallel Coordinates" },
  "radial-bar":           { icon: PieChart,    label: "Radial Bar" },
  sankey:                 { icon: Grid,        label: "Sankey" },
  stream:                 { icon: TrendingUp,  label: "Stream" },
  sunburst:               { icon: PieChart,    label: "Sunburst" },
  swarmplot:              { icon: ScatterIcon, label: "Swarmplot" },
  treemap:                { icon: PieChart,    label: "Treemap" },
  voronoi:                { icon: ScatterIcon, label: "Voronoi" },
  waffle:                 { icon: Grid,        label: "Waffle" },
};

// ── Table ──────────────────────────────────────────────────────────────────────
// Renders incrementally instead of dumping every row into the DOM at once —
// matters once a chart carries hundreds of rows (the "View data" preview can
// go up to 500). Starts at 100 rows and grows on demand via "Load more".
// Cell text is trimmed/compacted for display only (formatCellForDisplay) —
// the full raw value is kept in a `title` attribute so hovering (or CSV
// export, which reads chart.data directly) still gives full precision.
function TablePlot({ chart }: { chart: ChartDef }) {
  const rows = chart.data;
  const [visible, setVisible] = useState(100);

  // Reset the visible window when the underlying data changes (e.g. cell
  // re-run) so a stale "load more" position from a previous, larger result
  // doesn't linger.
  useEffect(() => { setVisible(100); }, [chart.id, rows]);

  if (!rows.length) return <EmptyChart message="No data to display." />;
  const columns = Object.keys(rows[0]);
  const shown = rows.slice(0, visible);
  const hasMore = visible < rows.length;

  return (
    <div>
      <div className="max-h-[420px] overflow-auto rounded-lg border border-border">
        <table className="min-w-full text-left font-mono text-xs">
          <thead className="sticky top-0 bg-secondary text-muted-foreground">
            <tr>{columns.map((c) => <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">{trimLabel(c, 24)}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i} className={`border-t border-border ${i % 2 === 0 ? "" : "bg-secondary/20"}`}>
                {columns.map((c) => (
                  <td key={c} className="whitespace-nowrap px-3 py-1.5 text-foreground" title={String(row[c] ?? "")}>
                    {formatCellForDisplay(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Showing {shown.length.toLocaleString()} of {rows.length.toLocaleString()} rows
        </p>
        {hasMore && (
          <button
            onClick={() => setVisible((v) => Math.min(rows.length, v + 200))}
            className="shrink-0 rounded-md border border-border px-2.5 py-1 font-sans text-xs font-medium text-foreground hover:bg-secondary"
          >
            Load 200 more
          </button>
        )}
      </div>
    </div>
  );
}

// ── Pie chart ──────────────────────────────────────────────────────────────────────
function PiePlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const { data, collapsedCount } = useMemo(() => {
    let raw: { key: string; value: number }[];
    if (!group_by) {
      // Simple pie: x as label, y as value
      raw = chart.data.map((d) => ({ key: trimLabel(d[x.column], 18), value: Number(d[y.column]) || 0 }));
    } else {
      // Grouped pie: group_by as id, aggregate y values
      const map = new Map<string, number>();
      chart.data.forEach((d) => {
        const key = trimLabel(d[group_by], 18);
        const value = Number(d[y.column]) || 0;
        map.set(key, (map.get(key) || 0) + value);
      });
      raw = Array.from(map.entries()).map(([key, value]) => ({ key, value }));
    }
    const { items, collapsedCount } = aggregateTopN(raw);
    return { data: items.map((i) => ({ id: i.key, value: i.value })), collapsedCount };
  }, [chart.data, x.column, y.column, group_by]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {collapsedCount > 0 && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Grouped the smallest {collapsedCount} categories into "Other" for readability.
        </p>
      )}
      <ResponsivePie
        data={data}
        margin={{ top: 40, right: 80, bottom: 40, left: 80 }}
        innerRadius={0.6}
        padAngle={0.7}
        cornerRadius={3}
        activeOuterRadiusOffset={8}
        borderWidth={0}
        borderColor={{ from: "color", modifiers: [["darker", 1.6]] }}
        arcLinkLabelsSkipAngle={5}
        arcLinkLabelsTextColor={{ from: "color", modifiers: [["darker", 2]] }}
        arcLinkLabelsThickness={2}
        arcLinkLabelsOffset={12}
        arcLinksTriangleSpacing={0}
        valueFormat={(v) => formatCompactNumber(v)}
        legends={[
          {
            dataFrom: "value",
            anchor: "bottom-center",
            direction: "row",
            justify: false,
            translateX: 0,
            translateY: 40,
            itemsSpacing: 0,
            itemDirection: "left-to-right",
            itemWidth: 80,
            itemHeight: 20,
            itemTextColor: t.textMuted,
            symbolSize: 12,
            symbolShape: "circle",
            effects: [
              {
                on: "hover",
                style: {
                  itemTextColor: t.text,
                },
              },
            ],
          },
        ]}
        theme={nivoTheme(t)}
        colors={PALETTE}
        isInteractive
        role="application"
      />
    </div>
  );
}

// ── Radar chart ────────────────────────────────────────────────────────────────
function RadarPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  // Nivo radar wants ONE row per axis-key, with each series as a sibling
  // column on that row: [{ key: 'Q1', seriesA: 10, seriesB: 20 }, ...].
  const { data, keys } = useMemo(() => {
    const xValues = [...new Set(chart.data.map((d) => String(d[x.column])))];
    if (!group_by) {
      const rows = xValues.map((xv) => {
        const match = chart.data.find((d) => String(d[x.column]) === xv);
        return { [x.column]: trimLabel(xv, 14), [y.column]: match ? Number(match[y.column]) || 0 : 0 };
      });
      return { data: rows, keys: [y.column] };
    }
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    const rows = xValues.map((xv) => {
      const row: Record<string, any> = { [x.column]: trimLabel(xv, 14) };
      groups.forEach((g) => {
        const match = chart.data.find((d) => String(d[x.column]) === xv && String(d[group_by]) === g);
        row[g] = match ? Number(match[y.column]) || 0 : 0;
      });
      return row;
    });
    return { data: rows, keys: groups };
  }, [chart.data, x.column, y.column, group_by]);

  if (!data.length || !keys.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveRadar
        data={data}
        keys={keys}
        indexBy={x.column}
        margin={{ top: 40, right: 80, bottom: 40, left: 80 }}
        borderColor={{ from: "color" }}
        gridLabelOffset={10}
        gridShape="circular"
        dotSize={6}
        dotColor={{ theme: "background" }}
        dotBorderWidth={2}
        legends={[
          {
            anchor: "bottom-center",
            direction: "row",
            translateX: 0,
            translateY: 40,
            itemsSpacing: 2,
            itemDirection: "left-to-right",
            itemWidth: 80,
            itemHeight: 20,
            itemTextColor: t.textMuted,
            symbolSize: 12,
            symbolShape: "circle",
          },
        ]}
        theme={{
          ...nivoTheme(t),
          grid: { line: { stroke: t.border } },
        }}
        colors={PALETTE}
        fillOpacity={0.2}
        role="application"
        animate
      />
    </div>
  );
}

// ── Bullet chart ─────────────────────────────────────────────────────────────
function BulletPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const buildItem = (id: string, value: number) => {
    // No explicit "target" concept in generic tabular data — approximate a
    // reasonable comparative range/marker from the measure itself so the
    // chart still reads meaningfully instead of using arbitrary constants.
    const magnitude = Math.max(Math.abs(value), 1);
    const max = magnitude * 1.5;
    const label = trimLabel(id, 20);
    return {
      id: label,
      title: label,
      ranges: [max * 0.33, max * 0.66, max],
      measures: [value],
      markers: [value * 0.9],
    };
  };

  const data = useMemo(() => {
    if (!group_by) {
      return chart.data.map((d) => buildItem(String(d[x.column]), Number(d[y.column]) || 0));
    }
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    return groups.map((g) => {
      const total = chart.data
        .filter((d) => String(d[group_by]) === g)
        .reduce((sum, r) => sum + (Number(r[y.column]) || 0), 0);
      return buildItem(g, total);
    });
  }, [chart.data, x.column, y.column, group_by]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height: Math.max(height, data.length * 56 + 40) }}>
      <ResponsiveBullet
        data={data}
        theme={nivoTheme(t)}
        margin={{ top: 20, right: 90, bottom: 40, left: 130 }}
        spacing={40}
        titleAlign="start"
        titleOffsetX={-110}
        measureSize={0.3}
        rangeColors={[t.border, t.surface, t.border]}
        measureColors={PALETTE[0]}
        markerColors={["#f87171"]}
        axisFormat={(v: unknown) => formatCompactNumber(v)}
        animate
      />
    </div>
  );
}


// ── Calendar chart ────────────────────────────────────────────────────────────
function CalendarPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y } = chart;

  const data = useMemo(() => {
    return chart.data
      .map((d) => ({ day: String(d[x.column]), value: Number(d[y.column]) || 0 }))
      .filter((d) => !Number.isNaN(Date.parse(d.day)))
      // Nivo calendar requires "day" as YYYY-MM-DD.
      .map((d) => ({ ...d, day: new Date(d.day).toISOString().slice(0, 10) }));
  }, [chart.data, x.column, y.column]);

  const { from, to } = useMemo(() => {
    if (!data.length) return { from: "", to: "" };
    const times = data.map((d) => new Date(d.day).getTime());
    return {
      from: new Date(Math.min(...times)).toISOString().slice(0, 10),
      to: new Date(Math.max(...times)).toISOString().slice(0, 10),
    };
  }, [data]);

  if (!data.length || !from) return <EmptyChart message="No date-based data to display." />;

  return (
    <div style={{ height: Math.max(height, 200) }}>
      <ResponsiveCalendar
        data={data}
        from={from}
        to={to}
        theme={nivoTheme(t)}
        emptyColor={t.border}
        colors={["#c6e0f7", "#8ec2ee", "#4f8ef7", "#2d5fc4"]}
        margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
        yearSpacing={40}
        monthBorderColor={t.surface}
        monthLegend={(_year, _month, date) =>
          date.toLocaleDateString(undefined, { month: "short" })
        }
        dayBorderWidth={1}
        dayBorderColor={t.surface}
        tooltip={({ day, value, color }) => (
          <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-foreground shadow-xl">
            <span className="font-semibold">{day}</span>
            <span className="mx-2 text-muted-foreground">·</span>
            <span className="font-bold">{formatCompactNumber(value)}</span>
          </div>
        )}
        legends={[{
          anchor: "bottom-right",
          direction: "row",
          translateY: 36,
          itemCount: 4,
          itemWidth: 42,
          itemHeight: 36,
          itemsSpacing: 14,
          itemDirection: "right-to-left",
        }]}
      />
    </div>
  );
}

// ── Chord diagram ──────────────────────────────────────────────────────────────
// Nivo's Chord component wants a square flow matrix (`data: number[][]`)
// plus a `keys: string[]` array naming each row/column, in the same order.
// x and y values may not describe the same set of entities, so the matrix
// is built over the UNION of both — any entity that only ever appears as a
// source (or only as a target) just gets an all-zero row/column, which
// Nivo renders fine (a thin/no arc for that entity). Keys are trimmed since
// Chord doesn't expose a separate label-formatter prop.
function ChordPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const { matrix, keys } = useMemo(() => {
    if (!x.column || !y.column || !chart.data.length) return { matrix: [] as number[][], keys: [] as string[] };

    const xValues = [...new Set(chart.data.map((d) => String(d[x.column])))];
    const yValues = [...new Set(chart.data.map((d) => String(d[y.column])))];
    const combined = [...new Set([...xValues, ...yValues])];
    const index = new Map(combined.map((v, i) => [v, i]));
    const size = combined.length;
    const m: number[][] = Array.from({ length: size }, () => Array(size).fill(0));

    chart.data.forEach((row) => {
      const xi = index.get(String(row[x.column]));
      const yi = index.get(String(row[y.column]));
      if (xi === undefined || yi === undefined) return;
      const value = group_by ? Number(row[group_by]) || 1 : 1;
      m[xi][yi] += value;
    });

    return { matrix: m, keys: combined.map((k) => trimLabel(k, 14)) };
  }, [chart.data, x.column, y.column, group_by]);

  if (!matrix.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveChord
        data={matrix}
        keys={keys}
        theme={nivoTheme(t)}
        margin={{ top: 40, right: 80, bottom: 40, left: 80 }}
        valueFormat={(v) => formatCompactNumber(v)}
        padAngle={0.02}
        innerRadiusRatio={0.9}
        innerRadiusOffset={0.02}
        colors={PALETTE}
        ribbonOpacity={0.7}
        labelTextColor={t.textMuted}
        animate
      />
    </div>
  );
}

// ── Circle packing ───────────────────────────────────────────────────────────
function CirclePackingPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const data = useMemo(() => buildHierarchy(chart), [chart.data, chart.x.column, chart.y.column, chart.group_by]);

  if (!data || !data.children?.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveCirclePacking
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 12, right: 12, bottom: 12, left: 12 }}
        id="name"
        value="value"
        padding={4}
        leavesOnly={!chart.group_by}
        labelsSkipRadius={10}
        labelTextColor={{ from: "color", modifiers: [["darker", 2.5]] }}
        borderWidth={1.5}
        borderColor={{ from: "color", modifiers: [["darker", 0.4]] }}
        animate
      />
    </div>
  );
}

// ── Funnel ────────────────────────────────────────────────────────────────────
function FunnelPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y } = chart;

  const { data, collapsedCount } = useMemo(() => {
    const raw = chart.data.map((d) => ({ key: trimLabel(d[x.column], 16), value: Number(d[y.column]) || 0 }));
    const { items, collapsedCount } = aggregateTopN(raw);
    return {
      data: items
        .map((i) => ({ id: i.key, value: i.value, label: `${i.key} (${formatCompactNumber(i.value)})` }))
        .sort((a, b) => b.value - a.value),
      collapsedCount,
    };
  }, [chart.data, x.column, y.column]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {collapsedCount > 0 && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Grouped the smallest {collapsedCount} stages into "Other" for readability.
        </p>
      )}
      <ResponsiveFunnel
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
        direction="horizontal"
        shapeBlending={0.7}
        borderWidth={20}
        borderColor={{ from: "color", modifiers: [["darker", 0.3]] }}
        labelColor={{ from: "color", modifiers: [["darker", 3]] }}
        beforeSeparatorLength={0}
        afterSeparatorLength={0}
        animate
      />
    </div>
  );
}

// ── Geo ───────────────────────────────────────────────────────────────────────
// A choropleth needs real GeoJSON feature geometry (country/region borders),
// which query results never carry — only a location code and a value. Rather
// than crash on missing `features`, fall back to a plain table of the same
// rows so the data is still visible; the download menu still exports CSV.
function GeoPlot({ chart, height }: { chart: ChartDef; height: number }) {
  if (!chart.data.length) return <EmptyChart message="No data to display." />;
  return (
    <div style={{ maxHeight: height, overflow: "auto" }}>
      <p className="mb-2 text-[10px] text-muted-foreground">
        Map boundaries aren't available for this data — showing the underlying values instead.
      </p>
      <TablePlot chart={chart} />
    </div>
  );
}

// ── Marimekko ─────────────────────────────────────────────────────────────────
function MarimekkoPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const { data, dimensions } = useMemo(() => {
    if (!group_by) {
      return {
        data: chart.data.map((d) => ({ id: trimLabel(d[x.column], 14), [y.column]: Number(d[y.column]) || 0 })),
        dimensions: [{ key: y.column, value: y.column }],
      };
    }
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    const map = new Map<string, Record<string, any>>();
    chart.data.forEach((d) => {
      const xk = trimLabel(d[x.column], 14);
      const gk = String(d[group_by]);
      if (!map.has(xk)) map.set(xk, { id: xk });
      map.get(xk)![gk] = (map.get(xk)![gk] || 0) + (Number(d[y.column]) || 0);
    });
    return {
      data: Array.from(map.values()),
      dimensions: groups.map((g) => ({ key: g, value: g })),
    };
  }, [chart.data, x.column, y.column, group_by]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveMarimekko
        data={data}
        id="id"
        value={group_by ? undefined : y.column}
        dimensions={dimensions as any}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 20, right: 120, bottom: 60, left: 60 }}
        axisBottom={{ tickRotation: -30 }}
        axisLeft={{ format: (v: unknown) => formatCompactNumber(v) }}
        borderWidth={1}
        borderColor={{ from: "color", modifiers: [["darker", 0.4]] }}
        animate
      />
    </div>
  );
}

// ── Network ───────────────────────────────────────────────────────────────────
function NetworkPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, group_by } = chart;

  const { nodes, links } = useMemo(() => {
    const nodeIds = [...new Set(chart.data.map((d) => String(d[x.column])))];
    const nodes = nodeIds.map((id, i) => ({
      id: trimLabel(id, 16),
      height: 1,
      size: 16,
      color: PALETTE[i % PALETTE.length],
    }));
    const links: { source: string; target: string; distance: number }[] = [];

    const trimmedId = (id: string) => trimLabel(id, 16);

    if (group_by) {
      const groups = new Map<string, string[]>();
      chart.data.forEach((d) => {
        const g = String(d[group_by]);
        const id = trimmedId(String(d[x.column]));
        if (!groups.has(g)) groups.set(g, []);
        if (!groups.get(g)!.includes(id)) groups.get(g)!.push(id);
      });
      groups.forEach((ids) => {
        for (let i = 0; i < ids.length - 1; i++) {
          links.push({ source: ids[i], target: ids[i + 1], distance: 60 });
        }
      });
    } else {
      const trimmedIds = nodeIds.map(trimmedId);
      for (let i = 0; i < trimmedIds.length - 1; i++) {
        links.push({ source: trimmedIds[i], target: trimmedIds[i + 1], distance: 60 });
      }
    }

    return { nodes, links };
  }, [chart.data, x.column, group_by]);

  if (!nodes.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveNetwork
        data={{ nodes, links }}
        theme={nivoTheme(t)}
        margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
        linkDistance={(l: any) => l.distance}
        centeringStrength={0.3}
        repulsivity={80}
        nodeColor={(n: any) => n.color}
        nodeBorderWidth={1}
        nodeBorderColor={{ from: "color", modifiers: [["darker", 0.8]] }}
        linkThickness={2}
        linkColor={t.border}
        animate
      />
    </div>
  );
}

// ── Parallel coordinates ─────────────────────────────────────────────────────
function ParallelCoordinatesPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();

  const { data, variables } = useMemo(() => {
    if (!chart.data.length) return { data: [] as Record<string, unknown>[], variables: [] as any[] };
    const sample = chart.data[0];
    const numericKeys = Object.keys(sample).filter((k) => {
      const v = sample[k];
      return typeof v === "number" || (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)));
    });
    const variables = numericKeys.slice(0, 8).map((key) => ({
      key,
      type: "linear" as const,
      min: "auto" as const,
      max: "auto" as const,
      ticksPosition: "before" as const,
      legend: trimLabel(key, 14),
      legendPosition: "start" as const,
      legendOffset: 20,
      format: (v: unknown) => formatCompactNumber(v),
    }));
    return { data: chart.data, variables };
  }, [chart.data]);

  if (!data.length || variables.length < 2) {
    return <EmptyChart message="Needs at least two numeric columns for a parallel coordinates plot." />;
  }

  return (
    <div style={{ height }}>
      <ResponsiveParallelCoordinates
        data={data as any}
        variables={variables}
        theme={nivoTheme(t)}
        colors={PALETTE[0]}
        margin={{ top: 40, right: 60, bottom: 40, left: 60 }}
        lineWidth={2}
        animate
      />
    </div>
  );
}

// ── Radial bar ────────────────────────────────────────────────────────────────
function RadialBarPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const { data, collapsedCount } = useMemo(() => {
    if (!group_by) {
      const raw = chart.data.map((d) => ({ key: trimLabel(d[x.column], 16), value: Number(d[y.column]) || 0 }));
      const { items, collapsedCount } = aggregateTopN(raw);
      return {
        data: items.map((i) => ({ id: i.key, data: [{ x: y.label || y.column, y: i.value }] })),
        collapsedCount,
      };
    }
    const groups = [...new Set(chart.data.map((d) => String(d[group_by])))];
    // Groups become separate rings, not slices — cap those too so the
    // legend/rings stay readable rather than aggregating within a group.
    const { items: groupItems, collapsedCount } = aggregateTopN(
      groups.map((g) => ({
        key: g,
        value: chart.data
          .filter((d) => String(d[group_by]) === g)
          .reduce((sum, r) => sum + (Number(r[y.column]) || 0), 0),
      })),
    );
    const keptGroups = new Set(groupItems.map((i) => i.key));
    return {
      data: groups
        .filter((g) => keptGroups.has(g))
        .map((g) => ({
          id: trimLabel(g, 16),
          data: chart.data
            .filter((d) => String(d[group_by]) === g)
            .map((d) => ({ x: trimLabel(d[x.column], 14), y: Number(d[y.column]) || 0 })),
        })),
      collapsedCount,
    };
  }, [chart.data, x.column, y.column, group_by, y.label]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {collapsedCount > 0 && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Grouped the smallest {collapsedCount} {group_by ? "series" : "categories"} into "Other" for readability.
        </p>
      )}
      <ResponsiveRadialBar
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 20, right: 100, bottom: 20, left: 20 }}
        padding={0.3}
        cornerRadius={2}
        enableRadialGrid
        enableCircularGrid
        radialAxisStart={{ tickSize: 5, tickPadding: 5, format: (v: unknown) => formatCompactNumber(v) }}
        circularAxisOuter={{ tickSize: 5, tickPadding: 12, format: (v: unknown) => trimLabel(v, 10) }}
        legends={[{
          anchor: "right", direction: "column", translateX: 90,
          itemWidth: 90, itemHeight: 18, symbolSize: 10, symbolShape: "circle",
          itemTextColor: t.textMuted,
        }]}
        animate
      />
    </div>
  );
}

// ── Sankey ────────────────────────────────────────────────────────────────────
function SankeyPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const { nodes, links } = useMemo(() => {
    if (!group_by) return { nodes: [] as { id: string }[], links: [] as any[] };
    const nodeSet = new Set<string>();
    const linkMap = new Map<string, number>();
    chart.data.forEach((d) => {
      const source = trimLabel(d[x.column], 18);
      const target = trimLabel(d[group_by], 18);
      if (source === target) return; // Sankey can't self-link a node
      const value = Number(d[y.column]) || 0;
      nodeSet.add(source);
      nodeSet.add(target);
      const key = `${source}__${target}`;
      linkMap.set(key, (linkMap.get(key) || 0) + value);
    });
    return {
      nodes: Array.from(nodeSet).map((id) => ({ id })),
      links: Array.from(linkMap.entries())
        .filter(([, value]) => value > 0)
        .map(([key, value]) => {
          const [source, target] = key.split("__");
          return { source, target, value };
        }),
    };
  }, [chart.data, x.column, y.column, group_by]);

  if (!nodes.length || !links.length) {
    return <EmptyChart message="Sankey diagrams need a grouping dimension to define flows between it and the x-axis." />;
  }

  return (
    <div style={{ height }}>
      <ResponsiveSankey
        data={{ nodes, links }}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 20, right: 150, bottom: 20, left: 20 }}
        align="justify"
        nodeOpacity={1}
        nodeThickness={16}
        nodeSpacing={20}
        nodeBorderWidth={0}
        linkOpacity={0.4}
        linkHoverOthersOpacity={0.1}
        labelPosition="outside"
        labelOrientation="horizontal"
        labelTextColor={t.text}
        valueFormat={(v) => formatCompactNumber(v)}
        animate
      />
    </div>
  );
}

// ── Sunburst ──────────────────────────────────────────────────────────────────
function SunburstPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const data = useMemo(() => buildHierarchy(chart), [chart.data, chart.x.column, chart.y.column, chart.group_by]);

  if (!data || !data.children?.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveSunburst
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 12, right: 12, bottom: 12, left: 12 }}
        id="name"
        value="value"
        cornerRadius={2}
        borderWidth={1}
        borderColor={{ theme: "background" }}
        childColor={{ from: "color", modifiers: [["brighter", 0.2]] }}
        animate
      />
    </div>
  );
}

// ── Swarmplot ─────────────────────────────────────────────────────────────────
function SwarmplotPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y, group_by } = chart;

  const MAX_POINTS = 2000;
  const data = useMemo(() => {
    let rows = chart.data;
    if (rows.length > MAX_POINTS) {
      const stride = Math.ceil(rows.length / MAX_POINTS);
      rows = rows.filter((_, i) => i % stride === 0);
    }
    return rows.map((d, i) => ({
      id: `${i}`,
      group: trimLabel(group_by ? d[group_by] : d[x.column], 14),
      value: Number(d[y.column]) || 0,
    }));
  }, [chart.data, x.column, y.column, group_by]);

  const wasSampled = chart.data.length > data.length;
  const groups = useMemo(() => [...new Set(data.map((d) => d.group))], [data]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {wasSampled && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Showing a sampled subset of points for readability.
        </p>
      )}
      <ResponsiveSwarmPlot
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        groups={groups}
        value="value"
        valueScale={{ type: "linear", min: "auto", max: "auto" }}
        size={8}
        spacing={2}
        margin={{ top: 20, right: 20, bottom: 40, left: 60 }}
        axisBottom={{ legend: group_by || x.label, legendPosition: "middle", legendOffset: 32 }}
        axisLeft={{ legend: y.label, legendPosition: "middle", legendOffset: -56, format: (v: unknown) => formatCompactNumber(v) }}
        animate
      />
    </div>
  );
}

// ── Treemap ───────────────────────────────────────────────────────────────────
function TreemapPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const data = useMemo(() => buildHierarchy(chart), [chart.data, chart.x.column, chart.y.column, chart.group_by]);

  if (!data || !data.children?.length) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      <ResponsiveTreeMap
        data={data}
        theme={nivoTheme(t)}
        colors={PALETTE}
        identity="name"
        value="value"
        margin={{ top: 12, right: 12, bottom: 12, left: 12 }}
        leavesOnly={!chart.group_by}
        labelSkipSize={16}
        labelTextColor={{ from: "color", modifiers: [["darker", 2.5]] }}
        borderColor={{ from: "color", modifiers: [["darker", 0.4]] }}
        animate
      />
    </div>
  );
}

// ── Voronoi ───────────────────────────────────────────────────────────────────
function VoronoiPlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y } = chart;

  const MAX_POINTS = 1000;
  const { data, wasSampled } = useMemo(() => {
    let rows = chart.data;
    let sampled = false;
    if (rows.length > MAX_POINTS) {
      const stride = Math.ceil(rows.length / MAX_POINTS);
      rows = rows.filter((_, i) => i % stride === 0);
      sampled = true;
    }
    return {
      data: rows.map((d) => ({ x: Number(d[x.column]) || 0, y: Number(d[y.column]) || 0 })),
      wasSampled: sampled,
    };
  }, [chart.data, x.column, y.column]);

  if (!data.length) return <EmptyChart message="No data to display." />;

  const xs = data.map((d) => d.x);
  const ys = data.map((d) => d.y);
  const xDomain: [number, number] = [Math.min(...xs), Math.max(...xs) || 1];
  const yDomain: [number, number] = [Math.min(...ys), Math.max(...ys) || 1];

  return (
    <div style={{ height }}>
      {wasSampled && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Showing a sampled subset of points for readability.
        </p>
      )}
      <ResponsiveVoronoi
        data={data}
        xDomain={xDomain}
        yDomain={yDomain}
        margin={{ top: 12, right: 12, bottom: 12, left: 12 }}
        enableLinks
        linkLineWidth={1}
        linkLineColor={t.border}
        cellLineWidth={1.5}
        cellLineColor={PALETTE[0]}
        pointSize={4}
        pointColor={PALETTE[0]}
      />
    </div>
  );
}

// ── Waffle ────────────────────────────────────────────────────────────────────
function WafflePlot({ chart, height }: { chart: ChartDef; height: number }) {
  const t = useThemeTokens();
  const { x, y } = chart;

  const { data, total, collapsedCount } = useMemo(() => {
    const raw = chart.data.map((d) => ({ key: trimLabel(d[x.column], 16), value: Number(d[y.column]) || 0 }));
    const { items, collapsedCount } = aggregateTopN(raw);
    const rows = items.map((i) => ({ id: i.key, label: `${i.key} (${formatCompactNumber(i.value)})`, value: i.value }));
    return { data: rows, total: rows.reduce((s, r) => s + r.value, 0), collapsedCount };
  }, [chart.data, x.column, y.column]);

  if (!data.length || total === 0) return <EmptyChart message="No data to display." />;

  return (
    <div style={{ height }}>
      {collapsedCount > 0 && (
        <p className="mb-1.5 text-[10px] text-muted-foreground">
          Grouped the smallest {collapsedCount} categories into "Other" for readability.
        </p>
      )}
      <ResponsiveWaffle
        data={data}
        total={total}
        rows={10}
        columns={20}
        theme={nivoTheme(t)}
        colors={PALETTE}
        margin={{ top: 12, right: 130, bottom: 12, left: 12 }}
        borderColor={{ from: "color", modifiers: [["darker", 0.4]] }}
        legends={[{
          anchor: "right", direction: "column", translateX: 110, itemWidth: 110,
          itemHeight: 20, itemTextColor: t.textMuted, symbolSize: 12,
        }]}
        animate
      />
    </div>
  );
}

// ── ChartBlock — container shared by every plot type ────────────────────────
export function ChartBlock({
  chart, toolbarExtra, subHeader, footer,
}: { chart: ChartDef; toolbarExtra?: ReactNode; subHeader?: ReactNode; footer?: ReactNode }) {  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const chartBodyRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const meta = CHART_META[chart.plot_type] ?? { icon: BarChart2, label: "Chart" };
  const Icon = meta.icon;

  // Close the download menu on outside clicks only (not on every click,
  // and not before the menu item's own onClick has a chance to fire).
  useEffect(() => {
    if (!showDownloadMenu) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowDownloadMenu(false);
      }
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [showDownloadMenu]);

  const handleDownloadPng = async () => {
    setShowDownloadMenu(false);
    // If the chart body is collapsed, there's no mounted SVG to read yet —
    // expand it first so the ref is populated, then export on the next tick.
    if (collapsed) {
      setCollapsed(false);
      await new Promise((r) => setTimeout(r, 0));
    }
    if (!chartBodyRef.current || downloading) return;
    setDownloading(true);
    try {
      await exportChartAsPng(chartBodyRef.current, chart.title || "chart");
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadCsv = () => {
    setShowDownloadMenu(false);
    exportChartAsCsv(chart.data, chart.title || "chart");
  };

  const notice = chartDataNotice(chart);

  const renderBody = (tall: boolean) => {
    const h = tall ? 480 : 300;
    switch (chart.plot_type) {
      case "bar":                    return <BarPlot chart={chart} height={h} />;
      case "line":                   return <LinePlot chart={chart} height={h} />;
      case "area":                   return <LinePlot chart={chart} height={h} forceArea />;
      case "scatter":                return <ScatterPlot chart={chart} height={h} />;
      case "heatmap":                return <HeatMapPlot chart={chart} height={h} />;
      case "boxplot":                return <BoxPlot chart={chart} height={tall ? 420 : 320} />;
      case "table":                  return <TablePlot chart={chart} />;
      case "pie":                    return <PiePlot chart={chart} height={h} />;
      case "radar":                  return <RadarPlot chart={chart} height={h} />;
      case "bullet":                 return <BulletPlot chart={chart} height={h} />;
      case "calendar":               return <CalendarPlot chart={chart} height={h} />;
      case "chord":                  return <ChordPlot chart={chart} height={h} />;
      case "circle-packing":         return <CirclePackingPlot chart={chart} height={h} />;
      case "funnel":                 return <FunnelPlot chart={chart} height={h} />;
      case "geo":                    return <GeoPlot chart={chart} height={h} />;
      case "marimekko":              return <MarimekkoPlot chart={chart} height={h} />;
      case "network":                return <NetworkPlot chart={chart} height={h} />;
      case "parallel-coordinates":   return <ParallelCoordinatesPlot chart={chart} height={h} />;
      case "radial-bar":             return <RadialBarPlot chart={chart} height={h} />;
      case "sankey":                 return <SankeyPlot chart={chart} height={h} />;
      case "sunburst":               return <SunburstPlot chart={chart} height={h} />;
      case "swarmplot":              return <SwarmplotPlot chart={chart} height={h} />;
      case "treemap":                return <TreemapPlot chart={chart} height={h} />;
      case "voronoi":                return <VoronoiPlot chart={chart} height={h} />;
      case "waffle":                 return <WafflePlot chart={chart} height={h} />;
      default:
        return <EmptyChart message={`Chart type "${chart.plot_type}" is not supported.`} />;
    }
  };

  return (
    <>
      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-border/60 px-4 py-3">
          <div className="flex min-w-0 items-start gap-2.5">
            <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border bg-secondary">
              <Icon className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold uppercase tracking-widest text-primary">{meta.label}</span>
              </div>
              <h3 className="mt-0.5 text-sm font-semibold text-foreground">{chart.title}</h3>
              {chart.description && !collapsed && (
                <p className="mt-1 text-xs text-muted-foreground">{chart.description}</p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 pt-0.5">
              {toolbarExtra && (
                <>
                  {toolbarExtra}
                  <div className="mx-0.5 h-4 w-px bg-border" />
                </>
              )}
            {/* Download dropdown */}
            <div className="relative" ref={menuRef}>
              <button
                onClick={(e) => { e.stopPropagation(); setShowDownloadMenu((v) => !v); }}
                disabled={downloading || chart.data.length === 0}
                title="Download"
                className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40"
              >
                <Download className={`h-3.5 w-3.5 ${downloading ? "animate-pulse" : ""}`} />
              </button>
              {showDownloadMenu && (
                <div
                  className="absolute right-0 top-full z-20 mt-1 w-36 overflow-hidden rounded-lg border border-border bg-card shadow-xl"
                  onClick={(e) => e.stopPropagation()}
                >
                  {chart.plot_type !== "table" && (
                    <button
                      onClick={handleDownloadPng}
                      className="flex w-full items-center gap-2 px-3 py-2 text-xs text-foreground hover:bg-secondary"
                    >
                      <Download className="h-3 w-3 shrink-0 text-muted-foreground" />
                      Download PNG
                    </button>
                  )}
                  <button
                    onClick={handleDownloadCsv}
                    className="flex w-full items-center gap-2 border-t border-border px-3 py-2 text-xs text-foreground hover:bg-secondary"
                  >
                    <Download className="h-3 w-3 shrink-0 text-muted-foreground" />
                    Download CSV
                  </button>
                </div>
              )}
            </div>

            <button onClick={() => setExpanded(true)} title="Expand"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
            <button onClick={() => setCollapsed((c) => !c)} title={collapsed ? "Show" : "Collapse"}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
              {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>

        {subHeader && !collapsed && (
          <div className="flex flex-wrap items-center gap-1 border-b border-border/60 bg-secondary/20 px-4 py-2">
            {subHeader}
          </div>
        )}

        {/* Body — kept mounted (just visually hidden) when collapsed, so
            chartBodyRef stays valid for PNG export. */}
        <div className={collapsed ? "hidden" : "p-4"} ref={chartBodyRef}>
          {chart.data.length === 0
            ? <EmptyChart message="No data was returned for this chart." />
            : (
              <>
                {chart.plot_type !== "table" && <DataNotice text={notice} />}
                {renderBody(false)}
              </>
            )}
          <Observation text={chart.observation} />
          {footer && <div className="mt-3">{footer}</div>}
        </div>
      </div>

      {expanded && (
        <Modal title={chart.title} onClose={() => setExpanded(false)}>
          {chart.description && <p className="mb-4 text-sm text-muted-foreground">{chart.description}</p>}
          {chart.data.length === 0
            ? <EmptyChart message="No data was returned for this chart." />
            : (
              <>
                {chart.plot_type !== "table" && <DataNotice text={notice} />}
                {renderBody(true)}
              </>
            )}
          <Observation text={chart.observation} />
        </Modal>
      )}
    </>
  );
}
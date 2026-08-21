import { useState } from "react";
import { AlertCircle } from "lucide-react";
import { ChartBlock } from "@/components/visual/ChartBlock"

// ── Types ─────────────────────────────────────────────────────────────────────
interface AxisDef { column: string; label: string; type: string; }
interface KeyFinding { finding: string; source_analyses: string[]; business_impact: string; }
interface Summary { dataset_description: string; key_findings: KeyFinding[]; recommended_next_steps: string[]; }
type DataRow = Record<string, unknown>;

interface ChartDef {
  id: string; title: string; description: string;
  plot_type: "scatter" | "heatmap" | "bar" | "line" | "boxplot" | string;
  analytical_type: string;
  x: AxisDef; y: AxisDef; group_by?: string | null;
  observation: string; data: DataRow[];
}

interface AnalysisBody { charts?: ChartDef[]; summary?: Summary; }
interface AnalyticsCardProps { body: AnalysisBody | null; layout?: "tabs" | "dashboard"; }

const IMPACT_COLOR: Record<string, string> = { high: "#f87171", medium: "#fbbf24", low: "#34d399" };

function ImpactBadge({ impact }: { impact: string }) {
  const color = IMPACT_COLOR[impact] ?? "#94a3b8";
  return (
    <span className="rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest"
      style={{ background: color + "22", color, border: `1px solid ${color}55` }}>
      {impact}
    </span>
  );
}

// ── Summary panel ──────────────────────────────────────────────────────────────
function SummaryPanel({ summary }: { summary: Summary }) {
  const findings = summary.key_findings ?? [];
  const nextSteps = summary.recommended_next_steps ?? [];

  return (
    <div className="space-y-5">
      {/* Dataset description */}
      {summary.dataset_description && (
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-primary mb-2">Dataset Overview</p>
          <p className="text-sm leading-relaxed text-muted-foreground">{summary.dataset_description}</p>
        </div>
      )}

      {/* Key findings grid */}
      {findings.length > 0 && (
        <div>
          <p className="text-[10px] font-bold uppercase tracking-widest text-primary mb-3">Key Findings</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {findings.map((f, i) => (
              <div key={i} className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-2.5">
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                    Finding {i + 1}
                  </span>
                  <ImpactBadge impact={f.business_impact} />
                </div>
                <p className="text-sm leading-relaxed text-foreground">{f.finding}</p>
                {f.source_analyses?.length > 0 && (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    Source: {f.source_analyses.join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next steps */}
      {nextSteps.length > 0 && (
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-primary mb-3">Recommended Next Steps</p>
          <ol className="space-y-2 pl-4 list-decimal">
            {nextSteps.map((s, i) => (
              <li key={i} className="text-sm leading-relaxed text-muted-foreground">{s}</li>
            ))}
          </ol>
        </div>
      )}

      {findings.length === 0 && nextSteps.length === 0 && (
        <p className="text-sm text-muted-foreground px-1">No key findings or next steps were generated.</p>
      )}
    </div>
  );
}

// ── Tab navigation ─────────────────────────────────────────────────────────────
interface TabItem { id: string; label: string; }

function TabNav({ tabs, active, onChange }: { tabs: TabItem[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="mb-4 flex flex-wrap gap-1.5 border-b border-border pb-3">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            active === t.id
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ── Root export ────────────────────────────────────────────────────────────────
export default function AnalyticsCard({ body, layout = "tabs" }: AnalyticsCardProps) {
  const [activeTab, setActiveTab] = useState("summary");

  if (!body) {
    return (
      <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
        <AlertCircle className="h-4 w-4 shrink-0" />
        No analysis data available.
      </div>
    );
  }

  const { charts = [], summary } = body;
  const hasSummary = !!(
    summary?.dataset_description ||
    summary?.key_findings?.length ||
    summary?.recommended_next_steps?.length
  );

  // ── Dashboard layout: every chart as a tile in a responsive grid ──────────
  if (layout === "dashboard") {
    if (charts.length === 0) {
      return (
        <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
          <AlertCircle className="h-4 w-4 shrink-0" />
          No charts were generated for this dashboard.
        </div>
      );
    }
    return (
      <div className="mt-3 space-y-4 font-sans">
        {summary?.dataset_description && (
          <div className="rounded-xl border border-border bg-card p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-primary mb-2">Dataset Overview</p>
            <p className="text-sm leading-relaxed text-muted-foreground">{summary.dataset_description}</p>
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {charts.map((chart) => (
            <ChartBlock key={chart.id} chart={chart} />
          ))}
        </div>
        {hasSummary && (summary?.key_findings?.length || summary?.recommended_next_steps?.length) ? (
          <SummaryPanel summary={summary!} />
        ) : null}
      </div>
    );
  }

  // ── Existing tabbed layout (unchanged below) ───────────────────────────────
  const tabs: TabItem[] = [
    ...(hasSummary ? [{ id: "summary", label: "Summary" }] : []),
    ...charts.map((c, i) => ({ id: c.id, label: c.title || `Chart ${i + 1}` })),
  ];

  const validTab = tabs.find((t) => t.id === activeTab) ? activeTab : tabs[0]?.id ?? "summary";

  return (
    <div className="mt-3 space-y-2 font-sans">
      {tabs.length > 1 && <TabNav tabs={tabs} active={validTab} onChange={setActiveTab} />}

      {validTab === "summary" && hasSummary && summary && (
        <SummaryPanel summary={summary} />
      )}

      {charts.map((chart) =>
        validTab === chart.id ? <ChartBlock key={chart.id} chart={chart} /> : null
      )}

      {tabs.length <= 0 && charts.map((chart) => (
        <ChartBlock key={chart.id} chart={chart} />
      ))}
    </div>
  );
}

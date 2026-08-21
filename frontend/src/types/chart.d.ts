export interface AxisDef {
  column: string;
  label: string;
  type: string;
}

export interface KeyFinding {
  finding: string;
  source_analyses: string[];
  business_impact: string;
}

export interface Summary {
  dataset_description: string;
  key_findings: KeyFinding[];
  recommended_next_steps: string[];
}

export interface ChartDef {
  id: string; title: string; description: string;
  plot_type: "scatter" | "heatmap" | "bar" | "line" | "boxplot" | "pie" | "radar" | "area" | "bullet" | "calendar" | "chord" | "circle-packing" | "funnel" | "geo" | "marimekko" | "network" | "parallel-coordinates" | "radial-bar" | "sankey" | "sunburst" | "swarmplot" | "treemap" | "voronoi" | "waffle" | string;
  analytical_type: string;
  x: AxisDef;
  y: AxisDef;
  group_by?: string | null;
  observation: string;
  data: Record<string, unknown>[];
  /** User-written note shown beneath the chart on the dashboard. */
  hint?: string;
  total_row_count?: number | null;
  truncated?: boolean;
}

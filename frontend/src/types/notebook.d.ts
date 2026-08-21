export type NotebookStatus = "empty" | "ready" | "error";

export interface NotebookDataSource {
  id: string;
  kind: "upload" | "connector";
  table_name: string;
  connector_id?: string | null;
  connector_type?: string | null;
  source_file_url?: string | null;
  original_filename?: string | null;
  row_count?: number | null;
  column_count?: number | null;
  label?: string | null;
}

export interface AxisSpec {
  column?: string;
  label?: string;
  type?: string;
}

export interface NotebookChart {
  id: string;
  title: string;
  question: string;
  plot_type: "line" | "bar" | "scatter" | "boxplot" | "heatmap" | "pie" | "radar" | "area" | string;
  sql: string;
  x: AxisSpec;
  y: AxisSpec;
  group_by?: string | null;
  data: Record<string, unknown>[];
  observation?: string | null;
  error?: string | null;
  total_row_count?: number | null;
  truncated?: boolean;
}

export interface DashboardLayout {
  order: string[];
  hidden_ids: string[];
  hints: Record<string, string>;
  overrides: Record<string, Record<string, unknown>>;
  custom_charts: Record<string, unknown>[];
  text_blocks: Record<string, unknown>[];
}

export interface DashboardVersion {
  id: string;
  cell_id: string;
  notebook_id: string;
  version_number: number;
  reply: string;
  charts: NotebookChart[];
  dashboard_layout?: DashboardLayout | null;
  created_at: string;
}

export interface NotebookCell {
  id: string;
  notebook_id: string;
  type: "eda" | "question" | "dashboard" | "markdown";
  query: string;
  data_source_ids: string[];
  status: "running" | "complete" | "error";
  reply?: string | null;
  charts: NotebookChart[];
  dashboard_layout?: DashboardLayout | null;
  error?: string | null;
  created_at: string;
  response_time_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
}


export interface Notebook {
  id: string;
  name: string;
  description?: string | null;
  status: "empty" | "ready" | "error";
  data_sources: NotebookDataSource[];
  cell_count: number;
  created_at: string;
  updated_at: string;
}
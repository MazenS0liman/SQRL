import React from "react";
import { authHeaders } from "@/lib/auth";

// ————————————————————————————————————————————————————————————
// Types

export type WorkspaceDataType = "structured" | "image" | "text" | "audio" | "other";

export interface DataSource {
  source_id: string;
  kind: "upload" | "connector";
  name: string;
  connector_id?: string | null;
  file_type?: string | null;
  file_url?: string | null;
  columns?: string[] | null;
  all_columns?: string[] | null;
  table?: string | null;
  row_count?: number | null;
  query?: string;
}

export type WorkspaceStatus =
  | "created"
  | "uploaded"
  | "preprocessing"
  | "modeling"
  | "completed"
  | "failed";

export interface Workspace {
  workspace_id: string;
  name: string;
  status: WorkspaceStatus;
  data_type: WorkspaceDataType;
  target_column?: string | null;
  input_sources: DataSource[];
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadedSourceOut {
  source_id: string;
  file_name: string;
  file_type: string;
  columns?: string[] | null;
  row_count?: number | null;
  preview?: Record<string, unknown>[] | null;
  metadata?: Record<string, unknown> | null;
}

export interface UploadResponse {
  workspace_id: string;
  uploaded: UploadedSourceOut[];
  sources: DataSource[];
}

export interface ModelMetric {
  model_key: string;
  metric_name: string;
  mean: number;
  std?: number | null;
}

export interface ModelSummary {
  task_type?: string;
  overall_assessment?: string;
  models_trained?: string[];
  models_failed?: string[];
  best_model?: string;
  best_model_rationale?: string;
  recommendations?: string[];
  warnings?: string[];
}

export interface ModelFile {
  model_key: string;
  file_url: string;
}

export interface BuildResponse {
  workspace_id: string;
  status: WorkspaceStatus;
  preprocessing_summary: Record<string, unknown>;
  model_summary: ModelSummary;
  model_comparison: ModelMetric[];
  best_model: string | null;
  output_file_urls: string[];
  model_files: ModelFile[];
}

export interface ModelsResponse {
  workspace_id: string;
  status: WorkspaceStatus;
  target_column?: string | null;
  preprocessing_summary?: Record<string, unknown> | null;
  model_summary?: ModelSummary;
  model_comparison: ModelMetric[];
  best_model?: string | null;
  output_file_urls: string[];
  model_files: ModelFile[];
}

export interface PreprocessedDataResponse {
  workspace_id: string;
  target_column?: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  file_url?: string | null;
}

export interface ConnectorSummary {
  connector_id: string;
  name: string;
  type: string;
}

export const API_BASE = import.meta.env.VITE_BACKEND_API_BASE_URL || "/api";

export const DATA_TYPE_OPTIONS: { value: WorkspaceDataType; label: string }[] = [
  { value: "structured", label: "Structured (CSV)" },
  { value: "image", label: "Image" },
  { value: "text", label: "Text" },
  { value: "audio", label: "Audio" },
  { value: "other", label: "Other" },
];

// ————————————————————————————————————————————————————————————
// API helpers

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders({ "Content-Type": "application/json", ...(init?.headers || {}) }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail;
    if (detail && typeof detail === "object") {
      const err = new Error(detail.message || "Request failed.") as Error & {
        conflicting_columns?: string[];
      };
      err.conflicting_columns = detail.conflicting_columns;
      throw err;
    }
    throw new Error(typeof detail === "string" ? detail : "Request failed.");
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function downloadOutputFile(
  workspaceId: string,
  fileUrl: string,
  suggestedName: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/workspace/${workspaceId}/download?file_url=${encodeURIComponent(fileUrl)}`,
    { headers: authHeaders() }
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "Download failed.");
  }
  const blob = await res.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = suggestedName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

// ————————————————————————————————————————————————————————————
// Status → visual meaning. One source of truth so the folder tab color,
// the pipeline rail, and any badges never drift from each other.
// stage: 0=not started, 1=ingest, 2=prep, 3=train, 4=compare(done), -1=failed
export function statusMeta(status: WorkspaceStatus): {
  label: string;
  chipClass: string;
  tabClass: string;
  stage: number;
} {
  switch (status) {
    case "completed":
      return {
        label: "Completed",
        chipClass: "bg-primary-gradient text-primary-foreground",
        tabClass: "bg-primary-gradient",
        stage: 4,
      };
    case "failed":
      return {
        label: "Failed",
        chipClass: "bg-destructive text-destructive-foreground",
        tabClass: "bg-destructive",
        stage: -1,
      };
    case "modeling":
      return {
        label: "Training",
        chipClass: "bg-accent text-accent-foreground",
        tabClass: "bg-accent",
        stage: 3,
      };
    case "preprocessing":
      return {
        label: "Preparing",
        chipClass: "bg-accent text-accent-foreground",
        tabClass: "bg-accent",
        stage: 2,
      };
    case "uploaded":
      return {
        label: "Uploaded",
        chipClass: "bg-secondary text-foreground",
        tabClass: "bg-secondary border border-border",
        stage: 1,
      };
    default:
      return {
        label: "New",
        chipClass: "bg-muted text-muted-foreground",
        tabClass: "bg-muted border border-border",
        stage: 0,
      };
  }
}

export function formatPercent(value: number): string {
  if (value >= 0 && value <= 1) {
    return `${(value * 100).toFixed(1)}%`;
  }
  return value.toFixed(4);
}

// Turns an arbitrary summary dict (preprocessing summaries have no fixed
// shape) into readable label/value rows without assuming structure.
export function formatSummaryValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map((v) => formatSummaryValue(v)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return String(value);
}

export function formatSummaryLabel(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function SummaryKeyValueList({
  data,
}: {
  data: Record<string, unknown>;
}): JSX.Element | null {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-baseline justify-between gap-3 border-b border-border/60 pb-1.5">
          <dt className="text-xs text-muted-foreground">{formatSummaryLabel(key)}</dt>
          <dd className="truncate font-mono text-xs text-foreground" title={formatSummaryValue(value)}>
            {formatSummaryValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ————————————————————————————————————————————————————————————
// Pipeline Rail — literal readout of the four real backend stages.

export function PipelineRail({ status }: { status: WorkspaceStatus }): JSX.Element {
  const meta = statusMeta(status);
  const stages = [
    { n: "01", label: "Ingest" },
    { n: "02", label: "Prep" },
    { n: "03", label: "Train" },
    { n: "04", label: "Compare" },
  ];
  const failed = meta.stage === -1;

  return (
    <div className="flex overflow-hidden rounded-md border border-border">
      {stages.map((s, i) => {
        const idx = i + 1;
        const isDone = !failed && meta.stage > idx;
        const isActive = !failed && meta.stage === idx;

        let cellClass = "bg-muted/40 text-muted-foreground";
        if (isDone) cellClass = "bg-primary/10 text-primary border-primary/20";
        if (isActive) cellClass = "bg-accent text-accent-foreground";
        if (failed) cellClass = "bg-destructive/10 text-destructive";

        return (
          <div
            key={s.n}
            className={`flex flex-1 items-center gap-2 px-3 py-2 ${cellClass} ${
              i === 0 ? "" : "border-l border-border"
            }`}
          >
            <span className="font-mono text-[10px] opacity-70">{s.n}</span>
            <span className="text-[11px] font-medium uppercase tracking-wide">{s.label}</span>
            {isActive && (
              <span className="ml-auto h-1.5 w-1.5 animate-pulse rounded-full bg-accent-foreground" />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function StepLabel({ n, children }: { n: string; children: React.ReactNode }): JSX.Element {
  return (
    <h3 className="mb-3 flex items-baseline gap-2">
      <span className="font-mono text-xs font-medium text-primary">{n}</span>
      <span className="text-[13px] font-semibold uppercase tracking-wide text-foreground">
        {children}
      </span>
    </h3>
  );
}
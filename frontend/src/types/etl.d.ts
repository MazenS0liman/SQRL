export type PipelineStatus = "draft" | "pending_approval" | "approved" | "archived" | string;
export type PipelineRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | string;

export type SourceSpec =
  | { kind: "connector"; connector_id?: string | null; table?: string | null; columns?: string[] }
  | { kind: "file"; file_url?: string | null; columns?: string[] }
  | { kind: "table"; table_name?: string | null; columns?: string[] };

export interface TransformSpec {
  op: "select" | "filter" | "rename" | "cast" | "dedupe" | "limit" | "aggregate" | "join" | string;
  columns?: string[] | null;
  column?: string | null;
  comparison?: string | null;
  value?: unknown;
  values?: unknown[] | null;
  mapping?: Record<string, string> | null;
  casts?: Record<string, string> | null;
  count?: number | null;
  group_by?: string[] | null;
  aggregations?: Array<{ column?: string | null; function: string; alias: string }> | null;
  right?: string | null;
  on?: Array<{ left: string; right: string }> | null;
  how?: string | null;
}

export interface SinkSpec {
  kind: "postgres" | "minio" | string;
  table?: string | null;
  mode?: "replace" | "append" | string;
  object_key?: string | null;
  format?: "csv" | string;
}

export interface PipelineStep {
  id: string;
  type: "source" | "transform" | "sink" | string;
  input?: string | null;
  source?: SourceSpec | null;
  transform?: TransformSpec | null;
  sink?: SinkSpec | null;
}

export interface PipelineDefinition {
  steps: PipelineStep[];
  materialize?: { notebook_id: string; create_dashboard_cell?: boolean } | null;
}

export interface CreatePipelineRequest {
  name: string;
  description?: string | null;
  definition: PipelineDefinition;
}

export interface UpdatePipelineRequest {
  name?: string;
  description?: string | null;
  definition?: PipelineDefinition;
}

export interface ApprovePipelineRequest {
  comment?: string | null;
}

export interface RunPipelineRequest {
  dry_run?: boolean;
}

export interface MaterializePipelineRequest {
  notebook_id: string;
}

export interface PlanPipelineRequest {
  query: string;
  connector_id?: string;
  table?: string;
  file_urls?: string[];
  sink_table?: string;
  notebook_id?: string;
  context?: Record<string, unknown>;
}

export interface PipelineRead {
  id: string;
  owner_user_id?: string | null;
  name: string;
  description?: string | null;
  status: PipelineStatus;
  version: number;
  definition: PipelineDefinition;
  approved_at?: string | null;
  approved_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PipelineRunRead {
  id: string;
  pipeline_id: string;
  owner_user_id?: string | null;
  job_id?: string | null;
  status: PipelineRunStatus;
  dry_run: boolean;
  row_count?: number | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface JobEventRead {
  id: number;
  job_id: string;
  level: string;
  message: string;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface PipelinePlanRead {
  name: string;
  description?: string | null;
  definition: PipelineDefinition;
  validation: Record<string, unknown>;
}

export interface EtlChatBody {
  plan?: PipelinePlanRead;
  [key: string]: unknown;
}

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

/** One feature column a trained model expects at inference time. */
export interface ModelInputField {
  name: string;
  dtype?: string | null;
  required?: boolean;
  example?: unknown;
}

export interface ModelHistoryPoint {
  x: number;
  y: number;
}

export interface ModelHistorySeries {
  name: string;
  points: ModelHistoryPoint[];
}

/** Loss / learning curve attached to a model card. */
export interface ModelTrainingHistory {
  kind?: string | null;
  x_label?: string | null;
  y_label?: string | null;
  series: ModelHistorySeries[];
}

/** Everything the model details dialog renders. */
export interface ModelDetails {
  task_type?: string | null;
  algorithm?: string | null;
  target?: string | null;
  input_schema?: ModelInputField[];
  preprocessing?: string[];
  training_history?: ModelTrainingHistory | null;
  n_features?: number | null;
  n_samples?: number | null;
  refit?: boolean;
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
  model_details?: ModelDetails | null;
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
  model_details?: ModelDetails | null;
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
  model_details?: ModelDetails | null;
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

export type TablePreviewEntry = {
  table: string;
  columns: string[];
  preview: Record<string, unknown>[];
  error?: string;
};

export type PredictResponse = {
  workspace_id: string;
  model_key: string;
  predictions: unknown[];
  probabilities?: number[][] | null;
  classes?: string[] | null;
};

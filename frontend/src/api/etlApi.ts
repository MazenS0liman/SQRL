import { authHeaders, notifyAuthExpired } from "@/lib/auth";
import type {
  ApprovePipelineRequest,
  CreatePipelineRequest,
  JobEventRead,
  MaterializePipelineRequest,
  PipelinePlanRead,
  PipelineRead,
  PipelineRunRead,
  PlanPipelineRequest,
  RunPipelineRequest,
  UpdatePipelineRequest,
} from "@/types";

const API_BASE = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, "");

export class EtlApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "EtlApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}/pipelines${path}`, {
    ...init,
    headers: authHeaders({
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    }),
  });

  if (response.status === 401) notifyAuthExpired();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((entry: { msg?: string }) => entry.msg || "Invalid request").join("; ")
        : `Pipeline request failed (${response.status}).`;
    throw new EtlApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const etlApi = {
  list: () => request<PipelineRead[]>(""),
  get: (id: string) => request<PipelineRead>(`/${encodeURIComponent(id)}`),
  create: (payload: CreatePipelineRequest) => request<PipelineRead>("", { method: "POST", body: JSON.stringify(payload) }),
  update: (id: string, payload: UpdatePipelineRequest) => request<PipelineRead>(`/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: (id: string) => request<void>(`/${encodeURIComponent(id)}`, { method: "DELETE" }),
  approve: (id: string, payload: ApprovePipelineRequest = {}) => request<PipelineRead>(`/${encodeURIComponent(id)}/approve`, json(payload)),
  runs: (id: string) => request<PipelineRunRead[]>(`/${encodeURIComponent(id)}/runs`),
  run: (id: string, payload: RunPipelineRequest = {}) => request<PipelineRunRead>(`/${encodeURIComponent(id)}/runs`, json(payload)),
  getRun: (id: string) => request<PipelineRunRead>(`/runs/${encodeURIComponent(id)}`),
  events: (id: string) => request<JobEventRead[]>(`/runs/${encodeURIComponent(id)}/events`),
  plan: (payload: PlanPipelineRequest) => request<PipelinePlanRead>("/plan", { method: "POST", body: JSON.stringify(payload) }),
  materialize: (id: string, payload: MaterializePipelineRequest) => request<Record<string, unknown>>(`/${encodeURIComponent(id)}/materialize`, json(payload)),
};

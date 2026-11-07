import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { authHeaders, notifyAuthExpired } from "@/lib/auth";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ————————————————————————————————————————————————————————————
// API helpers

export const API_BASE = import.meta.env.VITE_BACKEND_API_BASE_URL || "/api";

export class ApiError extends Error {
  status: number;
  conflicting_columns?: string[];

  constructor(message: string, status = 0, conflictingColumns?: string[]) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.conflicting_columns = conflictingColumns;
  }
}

async function handleErrorResponse(res: Response): Promise<never> {
  const body = await res.json().catch(() => ({}));
  const detail = body?.detail;
  if (detail && typeof detail === "object") {
    throw new ApiError(
      detail.message || "Request failed.",
      res.status,
      detail.conflicting_columns,
    );
  }
  throw new ApiError(typeof detail === "string" ? detail : "Request failed.", res.status);
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  responseType?: "json"
): Promise<T>;
export async function apiFetch(
  path: string,
  init: RequestInit | undefined,
  responseType: "blob"
): Promise<Blob>;
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  responseType: "json" | "blob" = "json"
): Promise<T | Blob> {
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders(isFormData ? init?.headers : { "Content-Type": "application/json", ...(init?.headers || {}) }),
  });
  if (res.status === 401) notifyAuthExpired();
  if (!res.ok) return handleErrorResponse(res);

  if (responseType === "blob") return res.blob();
  if (res.status === 204) return undefined as T;
  return res.json();
}
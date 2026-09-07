import { apiFetch } from "@/lib/utils";
import type { WorkspaceDataType, WorkspaceStatus } from "@/types";

export const DATA_TYPE_OPTIONS: { value: WorkspaceDataType; label: string }[] = [
  { value: "structured", label: "Structured (CSV)" },
];

// ————————————————————————————————————————————————————————————
// API helpers

export async function downloadOutputFile(
  workspaceId: string,
  fileUrl: string,
  suggestedName: string
): Promise<void> {
  const blob = await apiFetch(
    `/workspace/${workspaceId}/download?file_url=${encodeURIComponent(fileUrl)}`,
    undefined,
    "blob"
  );
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
// Display helpers
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
import { apiFetch } from "@/lib/utils";
import { etlApi } from "@/api/etlApi";
import {
  connectorsApi,
  type DataConnection,
} from "@/pages/connectors/shared";
import type { SourceSpec, SourceTableRead } from "@/types";

/** A file row returned by GET /file/. */
export interface ListedFile {
  fileUrl: string;
  fileName: string;
  fileType?: string | null;
  size?: number | null;
  uploadedAt?: string | null;
  workspaceId?: string | null;
  sourceId?: string | null;
  notebookId?: string | null;
}

interface FilesListResponse {
  success: boolean;
  files: ListedFile[];
}

interface UploadResponse {
  success: boolean;
  outputFiles: Record<string, ListedFile> | null;
}

export const sourceCatalog = {
  /** App tables the compiler accepts as `source.kind = "table"`. */
  tables: (): Promise<SourceTableRead[]> => etlApi.sourceTables(),

  /** Files already stored for the current user. */
  files: async (): Promise<ListedFile[]> => {
    const response = await apiFetch<FilesListResponse>("/file/");
    return response.success ? response.files : [];
  },

  /** Configured connectors (Postgres, Databricks, Lakebase). */
  connectors: (): Promise<DataConnection[]> => connectorsApi.list(),

  connectorTables: (connectorId: string): Promise<string[]> =>
    connectorsApi.listTables(connectorId),

  /** Real columns for a draft source, used by the column picker. */
  columns: async (source: SourceSpec): Promise<string[]> => {
    const response = await etlApi.resolveSource(source);
    return response.columns;
  },

  /** Upload one or more files and return their stored records. */
  upload: async (files: File[]): Promise<ListedFile[]> => {
    if (files.length === 0) return [];
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const response = await apiFetch<UploadResponse>("/file/upload-multiple", {
      method: "POST",
      body: form,
    });
    return response.outputFiles ? Object.values(response.outputFiles) : [];
  },
};

export function sourceKindLabel(kind: SourceSpec["kind"]): string {
  if (kind === "table") return "App table";
  if (kind === "file") return "File";
  return "Connector";
}

export function sourceSummary(source: SourceSpec | null | undefined): string {
  if (!source) return "Not configured";
  if (source.kind === "table") return source.table_name || "Choose a table";
  if (source.kind === "file") {
    const url = source.file_url || "";
    return url.split("/").pop() || "Choose a file";
  }
  return source.table ? `${source.table}` : "Choose a connector table";
}

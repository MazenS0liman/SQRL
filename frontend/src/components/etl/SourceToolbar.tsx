import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Database,
  FileText,
  FileUp,
  Loader2,
  Plug,
  RefreshCw,
  Search,
  Table2,
  Upload,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import {
  sourceCatalog,
  type ListedFile,
} from "@/components/etl/sourceCatalog";
import type { DataConnection } from "@/pages/connectors/shared";
import type { SourceSpec, SourceTableRead } from "@/types";

type SourceTab = "tables" | "files" | "connectors";

interface SourceToolbarProps {
  onAdd: (source: SourceSpec) => void;
  disabled?: boolean;
  className?: string;
}

function formatBytes(value?: number | null): string {
  if (!value) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function SourceToolbar({ onAdd, disabled, className }: SourceToolbarProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<SourceTab>("tables");
  const [tables, setTables] = useState<SourceTableRead[]>([]);
  const [files, setFiles] = useState<ListedFile[]>([]);
  const [connectors, setConnectors] = useState<DataConnection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [activeConnector, setActiveConnector] = useState<string | null>(null);
  const [connectorTables, setConnectorTables] = useState<Record<string, string[]>>({});
  const [loadingTables, setLoadingTables] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [tableResult, fileResult, connectorResult] = await Promise.allSettled([
      sourceCatalog.tables(),
      sourceCatalog.files(),
      sourceCatalog.connectors(),
    ]);
    if (tableResult.status === "fulfilled") setTables(tableResult.value);
    if (fileResult.status === "fulfilled") setFiles(fileResult.value);
    if (connectorResult.status === "fulfilled") setConnectors(connectorResult.value);

    const failure = [tableResult, fileResult, connectorResult].find(
      (result) => result.status === "rejected"
    ) as PromiseRejectedResult | undefined;
    if (failure) {
      setError(
        failure.reason instanceof Error
          ? failure.reason.message
          : "Some sources could not be loaded."
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    void refresh();
  }, [open, refresh]);

  const openTab = (next: SourceTab) => {
    setTab(next);
    setOpen(true);
  };

  const choose = (source: SourceSpec) => {
    onAdd(source);
    setOpen(false);
    setSearch("");
    setActiveConnector(null);
  };

  const loadConnectorTables = async (connectorId: string) => {
    if (activeConnector === connectorId) {
      setActiveConnector(null);
      return;
    }
    setActiveConnector(connectorId);
    if (connectorTables[connectorId]) return;
    setLoadingTables(connectorId);
    try {
      const next = await sourceCatalog.connectorTables(connectorId);
      setConnectorTables((current) => ({ ...current, [connectorId]: next }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not list connector tables.");
    } finally {
      setLoadingTables(null);
    }
  };

  const uploadFiles = async (fileList: FileList | null) => {
    const selected = Array.from(fileList ?? []);
    if (selected.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const uploaded = await sourceCatalog.upload(selected);
      setFiles((current) => [...uploaded, ...current]);
      if (uploaded[0]) {
        choose({ kind: "file", file_url: uploaded[0].fileUrl, columns: [] });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const filteredTables = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return tables;
    return tables.filter(
      (table) =>
        table.table_name.toLowerCase().includes(query) ||
        table.columns.some((column) => column.name.toLowerCase().includes(query))
    );
  }, [tables, search]);

  const filteredFiles = useMemo(() => {
    const query = search.trim().toLowerCase();
    return files.filter((file) => {
      const isCsv =
        file.fileName.toLowerCase().endsWith(".csv") ||
        (file.fileType ?? "").toLowerCase() === "csv";
      if (!isCsv) return false;
      return !query || file.fileName.toLowerCase().includes(query);
    });
  }, [files, search]);

  const filteredConnectors = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return connectors;
    return connectors.filter(
      (connector) =>
        connector.name.toLowerCase().includes(query) ||
        connector.type.toLowerCase().includes(query)
    );
  }, [connectors, search]);

  return (
    <>
      <aside
        className={cn(
          "flex w-[74px] shrink-0 flex-col gap-1 rounded-xl border border-border bg-card p-2",
          className
        )}
      >
        <p className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Sources
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => openTab("tables")}
          className="h-auto flex-col gap-1 px-1 py-2 text-[10px] font-medium"
        >
          <Database className="h-4 w-4 text-primary" />
          App table
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => openTab("files")}
          className="h-auto flex-col gap-1 px-1 py-2 text-[10px] font-medium"
        >
          <FileUp className="h-4 w-4 text-primary" />
          File
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => openTab("connectors")}
          className="h-auto flex-col gap-1 px-1 py-2 text-[10px] font-medium"
        >
          <Plug className="h-4 w-4 text-primary" />
          Connector
        </Button>
        <div className="mt-auto border-t border-border pt-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => void refresh()}
            className="h-auto w-full flex-col gap-1 px-1 py-2 text-[10px] font-medium"
            title="Refresh source lists"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </aside>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Add a source</DialogTitle>
            <DialogDescription>
              Add as many sources as you need, then join or transform them in the graph.
              Nothing is saved until you save the pipeline.
            </DialogDescription>
          </DialogHeader>

          <Tabs value={tab} onValueChange={(value) => setTab(value as SourceTab)}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="tables">App tables</TabsTrigger>
              <TabsTrigger value="files">Files</TabsTrigger>
              <TabsTrigger value="connectors">Connectors</TabsTrigger>
            </TabsList>

            <div className="mt-3 flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search sources..."
                  className="pl-8"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label="Refresh"
                onClick={() => void refresh()}
              >
                <RefreshCw className={cn(loading && "animate-spin")} />
              </Button>
            </div>

            {error && (
              <div className="mt-3 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                {error}
              </div>
            )}

            <TabsContent value="tables">
              {loading ? (
                <LoadingRows />
              ) : filteredTables.length === 0 ? (
                <EmptyState
                  icon={Table2}
                  title="No app tables found"
                  body="Tables with the etl_ prefix (or names allowed by ETL_ALLOWED_SOURCE_TABLES) appear here."
                />
              ) : (
                <ScrollArea className="mt-3 h-80 pr-2">
                  <div className="space-y-2">
                    {filteredTables.map((table) => (
                      <button
                        key={table.table_name}
                        type="button"
                        onClick={() =>
                          choose({ kind: "table", table_name: table.table_name, columns: [] })
                        }
                        className="w-full rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-2 font-mono text-sm font-medium text-foreground">
                            <Table2 className="h-4 w-4 text-primary" />
                            {table.table_name}
                          </span>
                          <Badge variant="outline">
                            {table.columns.length} column{table.columns.length === 1 ? "" : "s"}
                          </Badge>
                        </div>
                        <p className="mt-1 truncate text-xs text-muted-foreground">
                          {table.columns.map((column) => column.name).join(", ") || "No columns"}
                        </p>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </TabsContent>

            <TabsContent value="files">
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  Upload a CSV or choose one you already stored.
                </p>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".csv,text/csv"
                  multiple
                  className="hidden"
                  onChange={(event) => void uploadFiles(event.target.files)}
                />
                <Button
                  type="button"
                  size="sm"
                  disabled={uploading}
                  onClick={() => fileInput.current?.click()}
                >
                  {uploading ? <Loader2 className="animate-spin" /> : <Upload />} Upload CSV
                </Button>
              </div>
              {loading ? (
                <LoadingRows />
              ) : filteredFiles.length === 0 ? (
                <EmptyState
                  icon={FileText}
                  title="No CSV files yet"
                  body="Upload a CSV here, or add one from the Files page."
                />
              ) : (
                <ScrollArea className="mt-3 h-80 pr-2">
                  <div className="space-y-2">
                    {filteredFiles.map((file) => (
                      <button
                        key={file.fileUrl}
                        type="button"
                        onClick={() =>
                          choose({ kind: "file", file_url: file.fileUrl, columns: [] })
                        }
                        className="flex w-full items-center gap-3 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/5"
                      >
                        <FileText className="h-5 w-5 shrink-0 text-primary" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-foreground">
                            {file.fileName}
                          </span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {[formatBytes(file.size), file.uploadedAt ? new Date(file.uploadedAt).toLocaleDateString() : ""]
                              .filter(Boolean)
                              .join(" · ") || file.fileUrl}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </TabsContent>

            <TabsContent value="connectors">
              {loading ? (
                <LoadingRows />
              ) : filteredConnectors.length === 0 ? (
                <EmptyState
                  icon={Plug}
                  title="No connectors configured"
                  body="Create a connection on the Data Connectors page, then return here."
                />
              ) : (
                <ScrollArea className="mt-3 h-80 pr-2">
                  <div className="space-y-2">
                    {filteredConnectors.map((connector) => {
                      const expanded = activeConnector === connector.connector_id;
                      const tablesForConnector = connectorTables[connector.connector_id] ?? [];
                      return (
                        <div
                          key={connector.connector_id}
                          className="rounded-lg border border-border bg-card"
                        >
                          <button
                            type="button"
                            onClick={() => void loadConnectorTables(connector.connector_id)}
                            className="flex w-full items-center gap-3 p-3 text-left"
                          >
                            <Plug className="h-4 w-4 shrink-0 text-primary" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-foreground">
                                {connector.name}
                              </span>
                              <span className="block truncate text-xs capitalize text-muted-foreground">
                                {connector.type} · {connector.status}
                              </span>
                            </span>
                            {loadingTables === connector.connector_id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Badge variant="outline">{expanded ? "Hide" : "Tables"}</Badge>
                            )}
                          </button>
                          {expanded && (
                            <div className="border-t border-border p-2">
                              {loadingTables === connector.connector_id ? (
                                <p className="p-2 text-xs text-muted-foreground">Loading tables…</p>
                              ) : tablesForConnector.length === 0 ? (
                                <p className="p-2 text-xs text-muted-foreground">
                                  No tables reported by this connector.
                                </p>
                              ) : (
                                <div className="max-h-48 space-y-1 overflow-y-auto">
                                  {tablesForConnector.map((table) => (
                                    <button
                                      key={table}
                                      type="button"
                                      onClick={() =>
                                        choose({
                                          kind: "connector",
                                          connector_id: connector.connector_id,
                                          table,
                                          columns: [],
                                        })
                                      }
                                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-xs text-foreground hover:bg-secondary"
                                    >
                                      <Table2 className="h-3.5 w-3.5 text-muted-foreground" />
                                      {table}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </ScrollArea>
              )}
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>
    </>
  );
}

function LoadingRows(): JSX.Element {
  return (
    <div className="mt-6 flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> Loading sources…
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  body,
}: {
  icon: LucideIcon;
  title: string;
  body: string;
}): JSX.Element {
  return (
    <div className="mt-6 flex flex-col items-center justify-center rounded-lg border border-dashed border-border p-8 text-center">
      <Icon className="h-8 w-8 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium text-foreground">{title}</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">{body}</p>
    </div>
  );
}

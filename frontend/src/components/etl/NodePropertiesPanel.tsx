import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Database,
  FileText,
  HardDrive,
  Loader2,
  Plug,
  RefreshCw,
  Save,
  Shuffle,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import {
  sourceCatalog,
  type ListedFile,
} from "@/components/etl/sourceCatalog";
import type { DataConnection } from "@/pages/connectors/shared";
import type {
  PipelineDefinition,
  PipelineStep,
  SinkSpec,
  SourceSpec,
  SourceTableRead,
} from "@/types";

interface NodePropertiesPanelProps {
  open: boolean;
  step: PipelineStep | null;
  definition: PipelineDefinition | null;
  onOpenChange: (open: boolean) => void;
  onSave: (step: PipelineStep) => void;
  onDelete: (stepId: string) => void;
}

const SOURCE_KINDS: { value: SourceSpec["kind"]; label: string; icon: LucideIcon }[] = [
  { value: "table", label: "App table", icon: Database },
  { value: "file", label: "File (CSV)", icon: FileText },
  { value: "connector", label: "Connector", icon: Plug },
];

function isSourceComplete(source: SourceSpec): boolean {
  if (source.kind === "table") return Boolean(source.table_name);
  if (source.kind === "file") return Boolean(source.file_url);
  return Boolean(source.connector_id && source.table);
}

export function NodePropertiesPanel({
  open,
  step,
  definition,
  onOpenChange,
  onSave,
  onDelete,
}: NodePropertiesPanelProps): JSX.Element {
  const [sourceKind, setSourceKind] = useState<SourceSpec["kind"]>("table");
  const [tableName, setTableName] = useState("");
  const [fileUrl, setFileUrl] = useState("");
  const [connectorId, setConnectorId] = useState("");
  const [connectorTable, setConnectorTable] = useState("");
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [readAll, setReadAll] = useState(true);

  const [sinkKind, setSinkKind] = useState<SinkSpec["kind"]>("postgres");
  const [sinkTable, setSinkTable] = useState("");
  const [sinkMode, setSinkMode] = useState<"replace" | "append">("replace");
  const [objectKey, setObjectKey] = useState("");

  const [tables, setTables] = useState<SourceTableRead[]>([]);
  const [files, setFiles] = useState<ListedFile[]>([]);
  const [connectors, setConnectors] = useState<DataConnection[]>([]);
  const [connectorTables, setConnectorTables] = useState<string[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [availableColumns, setAvailableColumns] = useState<string[]>([]);
  const [columnsLoading, setColumnsLoading] = useState(false);
  const [columnsError, setColumnsError] = useState<string | null>(null);
  const [columnsReload, setColumnsReload] = useState(0);

  const isSource = step?.type === "source";
  const isSink = step?.type === "sink";
  const isTransform = step?.type === "transform";

  /* Reset the form when a different node is opened. */
  useEffect(() => {
    if (!open || !step) return;
    if (step.type === "source") {
      const source = step.source ?? { kind: "table", columns: [] };
      setSourceKind(source.kind);
      setTableName(source.kind === "table" ? source.table_name ?? "" : "");
      setFileUrl(source.kind === "file" ? source.file_url ?? "" : "");
      setConnectorId(source.kind === "connector" ? source.connector_id ?? "" : "");
      setConnectorTable(source.kind === "connector" ? source.table ?? "" : "");
      setSelectedColumns(source.columns ?? []);
      setReadAll((source.columns ?? []).length === 0);
    }
    if (step.type === "sink") {
      const sink = step.sink ?? { kind: "postgres", mode: "replace" };
      setSinkKind(sink.kind);
      setSinkTable(sink.kind === "postgres" ? sink.table ?? "" : "");
      setSinkMode(sink.kind === "postgres" ? ((sink.mode as "replace" | "append") ?? "replace") : "replace");
      setObjectKey(sink.kind === "minio" ? sink.object_key ?? "" : "");
    }
    setAvailableColumns([]);
    setColumnsError(null);
  }, [open, step]);

  /* Load the catalogs used by the pickers. */
  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError(null);
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
      setCatalogError(
        failure.reason instanceof Error ? failure.reason.message : "Could not load sources."
      );
    }
    setCatalogLoading(false);
  }, []);

  useEffect(() => {
    if (!open || !isSource) return;
    void loadCatalog();
  }, [open, isSource, loadCatalog]);

  /* Load tables for the selected connector. */
  useEffect(() => {
    if (!open || sourceKind !== "connector" || !connectorId) {
      setConnectorTables([]);
      return;
    }
    let cancelled = false;
    void sourceCatalog
      .connectorTables(connectorId)
      .then((next) => !cancelled && setConnectorTables(next))
      .catch((err) => !cancelled && setCatalogError(err instanceof Error ? err.message : "Could not list tables."));
    return () => {
      cancelled = true;
    };
  }, [open, sourceKind, connectorId]);

  /* Resolve real columns for the current draft source (debounced). */
  const draftSource = useMemo<SourceSpec>(() => {
    const columns = readAll ? [] : selectedColumns;
    if (sourceKind === "table") {
      return { kind: "table", table_name: tableName.trim() || null, columns };
    }
    if (sourceKind === "file") {
      return { kind: "file", file_url: fileUrl.trim() || null, columns };
    }
    return {
      kind: "connector",
      connector_id: connectorId || null,
      table: connectorTable.trim() || null,
      columns,
    };
  }, [sourceKind, tableName, fileUrl, connectorId, connectorTable, readAll, selectedColumns]);

  useEffect(() => {
    if (!open || !isSource || !isSourceComplete(draftSource)) {
      setAvailableColumns([]);
      return;
    }
    let cancelled = false;
    setColumnsLoading(true);
    setColumnsError(null);
    const timer = window.setTimeout(() => {
      void sourceCatalog
        .columns(draftSource)
        .then((columns) => {
          if (cancelled) return;
          setAvailableColumns(columns);
          setSelectedColumns((current) => {
            if (current.length === 0) return current;
            const valid = current.filter((column) => columns.includes(column));
            return valid.length === current.length ? current : valid;
          });
        })
        .catch((err) => {
          if (!cancelled) {
            setColumnsError(err instanceof Error ? err.message : "Could not read this source.");
          }
        })
        .finally(() => !cancelled && setColumnsLoading(false));
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, isSource, draftSource, columnsReload]);

  if (!step) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-full sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Nothing selected</SheetTitle>
            <SheetDescription>Select a source or sink node in the graph.</SheetDescription>
          </SheetHeader>
        </SheetContent>
      </Sheet>
    );
  }

  const Icon = isSource ? Database : isSink ? HardDrive : Shuffle;
  const canDelete = !isSink;
  const sourceComplete = isSourceComplete(draftSource);
  const sinkComplete =
    sinkKind === "postgres" ? Boolean(sinkTable.trim()) : Boolean(objectKey.trim());
  const canSave = isSource ? sourceComplete : isSink ? sinkComplete : true;
  const saveHint = !canSave
    ? isSource
      ? "Choose a source first."
      : "Enter a destination table or object key."
    : undefined;

  const save = () => {
    if (isSource) {
      onSave({ ...step, source: draftSource });
    } else if (isSink) {
      const sink: SinkSpec =
        sinkKind === "postgres"
          ? {
              kind: "postgres",
              table: sinkTable.trim() || null,
              mode: sinkMode,
            }
          : {
              kind: "minio",
              object_key: objectKey.trim() || null,
              format: "csv",
            };
      onSave({ ...step, sink });
    } else {
      onSave(step);
    }
    onOpenChange(false);
  };

  const toggleColumn = (column: string, checked: boolean) => {
    setSelectedColumns((current) =>
      checked ? Array.from(new Set([...current, column])) : current.filter((item) => item !== column)
    );
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-lg">
        <SheetHeader className="border-b border-border px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
              <Icon className="h-5 w-5 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <SheetTitle className="flex items-center gap-2 font-mono text-base">
                {step.id}
                <Badge variant="outline">{step.type}</Badge>
              </SheetTitle>
              <SheetDescription>
                {isSource
                  ? "Choose where this branch reads its rows from."
                  : isSink
                    ? "Choose where the pipeline writes its final result."
                    : "This stage is generated from its description."}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {catalogError && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {catalogError}
            </div>
          )}

          {isSource && (
            <>
              <div className="space-y-2">
                <Label>Source type</Label>
                <div className="grid grid-cols-3 gap-2">
                  {SOURCE_KINDS.map((kind) => {
                    const KindIcon = kind.icon;
                    return (
                      <button
                        key={kind.value}
                        type="button"
                        onClick={() => setSourceKind(kind.value)}
                        className={cn(
                          "flex flex-col items-center gap-1.5 rounded-lg border p-3 text-xs font-medium transition-colors",
                          sourceKind === kind.value
                            ? "border-primary bg-primary/5 text-foreground"
                            : "border-border text-muted-foreground hover:bg-secondary"
                        )}
                      >
                        <KindIcon className="h-4 w-4 text-primary" />
                        {kind.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {sourceKind === "table" && (
                <div className="space-y-2">
                  <Label htmlFor="source-table">App table</Label>
                  <Select value={tableName} onValueChange={setTableName}>
                    <SelectTrigger id="source-table">
                      <SelectValue placeholder={catalogLoading ? "Loading tables…" : "Choose a table"} />
                    </SelectTrigger>
                    <SelectContent>
                      {tables.map((table) => (
                        <SelectItem key={table.table_name} value={table.table_name}>
                          {table.table_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Only allow-listed app tables (default prefix <code>etl_</code>) can be read.
                  </p>
                </div>
              )}

              {sourceKind === "file" && (
                <div className="space-y-2">
                  <Label htmlFor="source-file">Stored CSV file</Label>
                  <Select value={fileUrl} onValueChange={setFileUrl}>
                    <SelectTrigger id="source-file">
                      <SelectValue placeholder={catalogLoading ? "Loading files…" : "Choose a file"} />
                    </SelectTrigger>
                    <SelectContent>
                      {files
                        .filter(
                          (file) =>
                            file.fileName.toLowerCase().endsWith(".csv") ||
                            (file.fileType ?? "").toLowerCase() === "csv"
                        )
                        .map((file) => (
                          <SelectItem key={file.fileUrl} value={file.fileUrl}>
                            {file.fileName}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Upload new CSVs from the source toolbar or the Files page.
                  </p>
                </div>
              )}

              {sourceKind === "connector" && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="source-connector">Connection</Label>
                    <Select
                      value={connectorId}
                      onValueChange={(value) => {
                        setConnectorId(value);
                        setConnectorTable("");
                      }}
                    >
                      <SelectTrigger id="source-connector">
                        <SelectValue placeholder={catalogLoading ? "Loading connections…" : "Choose a connection"} />
                      </SelectTrigger>
                      <SelectContent>
                        {connectors.map((connector) => (
                          <SelectItem key={connector.connector_id} value={connector.connector_id}>
                            {connector.name} ({connector.type})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="source-connector-table">Table</Label>
                    <Select value={connectorTable} onValueChange={setConnectorTable}>
                      <SelectTrigger id="source-connector-table">
                        <SelectValue placeholder={connectorId ? "Choose a table" : "Choose a connection first"} />
                      </SelectTrigger>
                      <SelectContent>
                        {connectorTables.map((table) => (
                          <SelectItem key={table} value={table}>
                            {table}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Columns</Label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!sourceComplete || columnsLoading}
                    onClick={() => setColumnsReload((value) => value + 1)}
                  >
                    <RefreshCw className={cn(columnsLoading && "animate-spin")} /> Refresh
                  </Button>
                </div>
                <label className="flex items-center gap-2 rounded-lg border border-border p-2.5 text-sm">
                  <Checkbox
                    checked={readAll}
                    onCheckedChange={(checked) => {
                      const next = checked === true;
                      setReadAll(next);
                      if (!next) setSelectedColumns(availableColumns);
                    }}
                  />
                  <span>Read all columns</span>
                </label>
                {!sourceComplete ? (
                  <p className="text-xs text-muted-foreground">
                    Choose a source to load its columns.
                  </p>
                ) : columnsLoading ? (
                  <p className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading columns…
                  </p>
                ) : columnsError ? (
                  <p className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                    {columnsError}
                  </p>
                ) : availableColumns.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No columns reported.</p>
                ) : (
                  <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
                    {availableColumns.map((column) => (
                      <label
                        key={column}
                        className="flex items-center gap-2 rounded px-1 py-0.5 font-mono text-xs hover:bg-secondary"
                      >
                        <Checkbox
                          checked={readAll || selectedColumns.includes(column)}
                          disabled={readAll}
                          onCheckedChange={(checked) => toggleColumn(column, checked === true)}
                        />
                        {column}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {isSink && (
            <>
              <div className="space-y-2">
                <Label>Destination type</Label>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { value: "postgres" as const, label: "Postgres table", icon: Database },
                    { value: "minio" as const, label: "MinIO CSV", icon: HardDrive },
                  ].map((option) => {
                    const OptionIcon = option.icon;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setSinkKind(option.value)}
                        className={cn(
                          "flex items-center gap-2 rounded-lg border p-3 text-xs font-medium transition-colors",
                          sinkKind === option.value
                            ? "border-primary bg-primary/5 text-foreground"
                            : "border-border text-muted-foreground hover:bg-secondary"
                        )}
                      >
                        <OptionIcon className="h-4 w-4 text-primary" />
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {sinkKind === "postgres" ? (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="sink-table">Destination table</Label>
                    <Input
                      id="sink-table"
                      value={sinkTable}
                      onChange={(event) => setSinkTable(event.target.value)}
                      placeholder="etl_daily_revenue"
                      className="font-mono"
                    />
                    <p className="text-xs text-muted-foreground">
                      Must start with <code>etl_</code> unless an allow-list permits the name.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label>Write mode</Label>
                    <Select value={sinkMode} onValueChange={(value) => setSinkMode(value as "replace" | "append")}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="replace">Replace existing rows</SelectItem>
                        <SelectItem value="append">Append rows</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </>
              ) : (
                <div className="space-y-2">
                  <Label htmlFor="sink-object">Object key</Label>
                  <Input
                    id="sink-object"
                    value={objectKey}
                    onChange={(event) => setObjectKey(event.target.value)}
                    placeholder="etl/daily_revenue.csv"
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    Must start with <code>etl/</code>. Format is always CSV.
                  </p>
                </div>
              )}
            </>
          )}

          {isTransform && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">
                  {step.transform?.op?.replaceAll("_", " ") ?? "transform"}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  Reads from <code className="font-mono">{step.input ?? "—"}</code>
                </span>
              </div>
              <div className="space-y-2">
                <Label>Compiled configuration</Label>
                <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-muted p-3 font-mono text-[11px] leading-5">
                  {JSON.stringify(step.transform ?? {}, null, 2)}
                </pre>
              </div>
              <p className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">
                To change this stage, delete it and insert a new one from the <code>+</code> on the
                edge, or describe the change in the chat.
              </p>
            </div>
          )}

          {isTransform && definition && (
            <p className="text-xs text-muted-foreground">
              Step {definition.steps.findIndex((item) => item.id === step.id) + 1} of{" "}
              {definition.steps.length}
            </p>
          )}
        </div>

        <SheetFooter className="flex-row items-center justify-between gap-2 border-t border-border px-5 py-3">
          <div>
            {canDelete && (
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={() => {
                  onDelete(step.id);
                  onOpenChange(false);
                }}
              >
                <Trash2 /> Delete
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={save}
              disabled={!canSave}
              title={saveHint}
            >
              <Save /> Save changes
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

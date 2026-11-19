import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  DollarSign,
  Loader2,
  RefreshCw,
  Zap,
} from "lucide-react";
import { usageApi } from "@/api/usageApi";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import DotField from "@/components/background/DotField";
import type {
  UsageDailyPoint,
  UsageEventListResponse,
  UsageEventRead,
  UsageOverviewResponse,
} from "@/types";

const WINDOWS = [7, 30, 90, 365];

const PROVIDER_COLORS: Record<string, string> = {
  gemini: "hsl(var(--primary))",
  groq: "#f59e0b",
  openrouter: "#22c55e",
  openai: "#8b5cf6",
};

const FALLBACK_COLORS = ["#0ea5e9", "#ef4444", "#14b8a6", "#eab308", "#a855f7"];

function providerColor(provider: string, index: number): string {
  return PROVIDER_COLORS[provider] ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length];
}

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value ?? 0);
}

function formatCost(value: number | null | undefined): string {
  const amount = value ?? 0;
  if (amount > 0 && amount < 0.01) return `<$0.01`;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

/** Pivot the per-provider daily rows into one row per day for a stacked chart. */
function pivotDaily(daily: UsageDailyPoint[]) {
  const rows = new Map<string, Record<string, number | string>>();
  daily.forEach((point) => {
    const row = rows.get(point.day) ?? { day: point.day };
    row[point.provider] = (Number(row[point.provider]) || 0) + point.total_tokens;
    rows.set(point.day, row);
  });
  return Array.from(rows.values()).sort((a, b) => String(a.day).localeCompare(String(b.day)));
}

/**
 * Usage Page
 * ==========
 *
 * Per-user LLM token monitoring. Everything is scoped server-side to the
 * signed-in user, so this page only ever renders that user's spend.
 */
export default function UsagePage(): JSX.Element {
  const [windowDays, setWindowDays] = useState(30);
  const [overview, setOverview] = useState<UsageOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [events, setEvents] = useState<UsageEventListResponse | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [providerFilter, setProviderFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [page, setPage] = useState(0);
  const pageSize = 25;

  const loadOverview = async () => {
    setLoading(true);
    setError(null);
    try {
      setOverview(await usageApi.overview(windowDays));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowDays]);

  useEffect(() => {
    let cancelled = false;
    setEventsLoading(true);
    void (async () => {
      try {
        const data = await usageApi.events({
          limit: pageSize,
          offset: page * pageSize,
          provider: providerFilter === "all" ? undefined : providerFilter,
          source: sourceFilter === "all" ? undefined : sourceFilter,
          days: windowDays,
        });
        if (!cancelled) setEvents(data);
      } catch {
        if (!cancelled) setEvents(null);
      } finally {
        if (!cancelled) setEventsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page, providerFilter, sourceFilter, windowDays]);

  useEffect(() => {
    setPage(0);
  }, [providerFilter, sourceFilter, windowDays]);

  const dailyRows = useMemo(() => pivotDaily(overview?.daily ?? []), [overview?.daily]);
  const providers = useMemo(
    () => (overview?.by_provider ?? []).map((row) => row.provider),
    [overview?.by_provider],
  );
  const totals = overview?.totals;
  const totalPages = events ? Math.max(1, Math.ceil(events.total / pageSize)) : 1;

  return (
    <main className="relative h-full min-h-0 overflow-y-auto bg-background">
      {/* Dotted background. Lives inside the same bg-background container it
          paints on top of, and the content column below is un-capped
          (min-h-full, not h-full) so this stretches across the full
          scrollable content, not just the first screen. */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <DotField
          dotRadius={1.5}
          dotSpacing={14}
          bulgeStrength={50}
          glowRadius={0}
          sparkle={false}
          waveAmplitude={0}
          cursorRadius={100}
          cursorForce={0}
          bulgeOnly={true}
          gradientFrom="#592603"
          gradientTo="#613508"
          glowColor="#af4d08"
        />
      </div>
      <div className="relative mx-auto min-h-full max-w-6xl px-6 py-10">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
          <div>
            <h1 className="bg-primary-gradient bg-clip-text text-3xl font-semibold text-transparent">
              Usage
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Token consumption and estimated spend for every model call you make.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Select value={String(windowDays)} onValueChange={(value) => setWindowDays(Number(value))}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WINDOWS.map((days) => (
                  <SelectItem key={days} value={String(days)}>
                    Last {days} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" size="icon" onClick={() => void loadOverview()} aria-label="Refresh usage">
              <RefreshCw className={cn(loading && "animate-spin")} />
            </Button>
          </div>
        </header>

        {loading && !overview && (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading usage…
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        {overview && (
          <>
            <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <StatCard
                icon={<Zap className="h-3.5 w-3.5" />}
                label="Total tokens"
                value={formatNumber(totals?.total_tokens)}
              />
              <StatCard
                icon={<ArrowDownToLine className="h-3.5 w-3.5" />}
                label="Prompt tokens"
                value={formatNumber(totals?.prompt_tokens)}
              />
              <StatCard
                icon={<ArrowUpFromLine className="h-3.5 w-3.5" />}
                label="Completion tokens"
                value={formatNumber(totals?.completion_tokens)}
              />
              <StatCard
                icon={<Activity className="h-3.5 w-3.5" />}
                label="Requests"
                value={formatNumber(totals?.requests)}
              />
              <StatCard
                icon={<DollarSign className="h-3.5 w-3.5" />}
                label="Estimated spend"
                value={formatCost(totals?.estimated_cost_usd)}
                hint="Approximate"
              />
            </section>

            <section className="mt-6 rounded-xl border border-border bg-card p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-foreground">Daily tokens by provider</h2>
                <span className="font-mono text-[11px] text-muted-foreground">Last {overview.window_days} days</span>
              </div>
              {dailyRows.length > 0 ? (
                <div className="h-64 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={dailyRows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                      <XAxis
                        dataKey="day"
                        tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                        stroke="hsl(var(--border))"
                        tickFormatter={(value: string) => value.slice(5)}
                      />
                      <YAxis
                        tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                        stroke="hsl(var(--border))"
                        width={58}
                        tickFormatter={(value: number) => formatNumber(value)}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "hsl(var(--card))",
                          border: "1px solid hsl(var(--border))",
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                        labelStyle={{ color: "hsl(var(--muted-foreground))" }}
                        formatter={(value: number) => formatNumber(value)}
                      />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      {providers.map((provider, index) => (
                        <Bar
                          key={provider}
                          dataKey={provider}
                          stackId="tokens"
                          fill={providerColor(provider, index)}
                          radius={index === providers.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                        />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <EmptyState message="No token usage recorded in this window." />
              )}
            </section>

            <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <BreakdownCard
                title="By provider"
                rows={overview.by_provider.map((row) => ({
                  key: row.provider,
                  label: row.provider,
                  tokens: row.total_tokens,
                  requests: row.requests,
                  cost: row.estimated_cost_usd,
                }))}
                totalTokens={totals?.total_tokens ?? 0}
              />
              <BreakdownCard
                title="By feature"
                rows={overview.by_source.map((row) => ({
                  key: row.source,
                  label: row.source,
                  tokens: row.total_tokens,
                  requests: row.requests,
                  cost: row.estimated_cost_usd,
                }))}
                totalTokens={totals?.total_tokens ?? 0}
              />
            </div>

            <section className="mt-6 rounded-xl border border-border bg-card shadow-sm">
              <div className="border-b border-border px-4 py-3">
                <h2 className="text-sm font-semibold text-foreground">By model</h2>
              </div>
              {overview.by_model.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <thead className="bg-secondary font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-4 py-2 font-medium">Model</th>
                        <th className="px-4 py-2 font-medium">Provider</th>
                        <th className="px-4 py-2 text-right font-medium">Tokens</th>
                        <th className="px-4 py-2 text-right font-medium">Requests</th>
                        <th className="px-4 py-2 text-right font-medium">Est. cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.by_model.map((row) => (
                        <tr key={`${row.provider}-${row.model}`} className="border-t border-border hover:bg-secondary/40">
                          <td className="px-4 py-2 font-mono text-foreground">{row.model}</td>
                          <td className="px-4 py-2 text-muted-foreground">{row.provider}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-foreground">
                            {formatNumber(row.total_tokens)}
                          </td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-muted-foreground">
                            {formatNumber(row.requests)}
                          </td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-foreground">
                            {formatCost(row.estimated_cost_usd)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState message="No model usage in this window." />
              )}
            </section>

            <section className="mt-6 rounded-xl border border-border bg-card shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
                <h2 className="text-sm font-semibold text-foreground">Recent calls</h2>
                <div className="flex flex-wrap items-center gap-2">
                  <Select value={providerFilter} onValueChange={setProviderFilter}>
                    <SelectTrigger className="w-36">
                      <SelectValue placeholder="Provider" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All providers</SelectItem>
                      {Array.from(new Set([...providers, ...(overview.by_model.map((m) => m.provider) ?? [])])).map(
                        (provider) => (
                          <SelectItem key={provider} value={provider}>
                            {provider}
                          </SelectItem>
                        ),
                      )}
                    </SelectContent>
                  </Select>
                  <Select value={sourceFilter} onValueChange={setSourceFilter}>
                    <SelectTrigger className="w-36">
                      <SelectValue placeholder="Feature" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All features</SelectItem>
                      {overview.by_source.map((row) => (
                        <SelectItem key={row.source} value={row.source}>
                          {row.source}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {eventsLoading ? (
                <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading calls…
                </div>
              ) : events && events.items.length > 0 ? (
                <>
                  <EventTable events={events.items} />
                  <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-2 text-xs text-muted-foreground">
                    <span>
                      {events.total === 0
                        ? "No calls"
                        : `${events.offset + 1}–${Math.min(events.offset + pageSize, events.total)} of ${formatNumber(
                            events.total,
                          )}`}
                    </span>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page === 0}
                        onClick={() => setPage((current) => Math.max(0, current - 1))}
                      >
                        Previous
                      </Button>
                      <span className="font-mono">
                        {page + 1} / {totalPages}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page + 1 >= totalPages}
                        onClick={() => setPage((current) => current + 1)}
                      >
                        Next
                      </Button>
                    </div>
                  </div>
                </>
              ) : (
                <EmptyState message="No calls match these filters." />
              )}
            </section>

            <p className="mt-4 text-xs text-muted-foreground">
              Costs are estimated from provider-reported token counts using public list prices.
              Actual invoices may differ; models without a known price are counted as $0.
            </p>
          </>
        )}
      </div>
    </main>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  hint?: string;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 font-mono text-xl font-semibold tabular-nums text-foreground">{value}</div>
      {hint && <div className="text-[10px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

function BreakdownCard({
  title,
  rows,
  totalTokens,
}: {
  title: string;
  rows: { key: string; label: string; tokens: number; requests: number; cost: number }[];
  totalTokens: number;
}): JSX.Element {
  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-foreground">{title}</h2>
      {rows.length === 0 ? (
        <EmptyState message="Nothing recorded yet." />
      ) : (
        <ul className="space-y-3">
          {rows.map((row, index) => {
            const share = totalTokens > 0 ? row.tokens / totalTokens : 0;
            return (
              <li key={row.key}>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="flex items-center gap-2 font-medium text-foreground">
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ background: providerColor(row.key, index) }}
                    />
                    {row.label}
                  </span>
                  <span className="font-mono tabular-nums text-muted-foreground">
                    {formatNumber(row.tokens)} tok · {formatCost(row.cost)}
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-secondary">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(share * 100, row.tokens > 0 ? 2 : 0)}%`,
                      background: providerColor(row.key, index),
                    }}
                  />
                </div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {formatNumber(row.requests)} request{row.requests === 1 ? "" : "s"} ·{" "}
                  {(share * 100).toFixed(1)}%
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function EventTable({ events }: { events: UsageEventRead[] }): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-secondary font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">When</th>
            <th className="px-4 py-2 font-medium">Provider</th>
            <th className="px-4 py-2 font-medium">Model</th>
            <th className="px-4 py-2 font-medium">Feature</th>
            <th className="px-4 py-2 text-right font-medium">Prompt</th>
            <th className="px-4 py-2 text-right font-medium">Completion</th>
            <th className="px-4 py-2 text-right font-medium">Total</th>
            <th className="px-4 py-2 text-right font-medium">Est. cost</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className="border-t border-border hover:bg-secondary/40">
              <td className="whitespace-nowrap px-4 py-2 text-muted-foreground">{formatDate(event.created_at)}</td>
              <td className="px-4 py-2 text-muted-foreground">{event.provider}</td>
              <td className="px-4 py-2 font-mono text-foreground">{event.model}</td>
              <td className="px-4 py-2">
                <span className="rounded border border-border bg-secondary px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  {event.source}
                </span>
              </td>
              <td className="px-4 py-2 text-right font-mono tabular-nums text-muted-foreground">
                {formatNumber(event.prompt_tokens)}
              </td>
              <td className="px-4 py-2 text-right font-mono tabular-nums text-muted-foreground">
                {formatNumber(event.completion_tokens)}
              </td>
              <td className="px-4 py-2 text-right font-mono tabular-nums text-foreground">
                {formatNumber(event.total_tokens)}
              </td>
              <td className="px-4 py-2 text-right font-mono tabular-nums text-foreground">
                {formatCost(event.estimated_cost_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyState({ message }: { message: string }): JSX.Element {
  return (
    <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
      {message}
    </div>
  );
}

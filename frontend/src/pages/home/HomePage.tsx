import { useEffect, useMemo, useState } from 'react';
import {
  FolderPlus,
  Cpu,
  ArrowUpRight,
  AlertCircle,
  CalendarClock,
  ChevronRight,
  Gauge,
  Loader2,
  NotebookText,
  RefreshCw,
  Workflow,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type {
  ConnectorSummary,
  MLModelRead,
  Notebook,
  PipelineRead,
  UsageOverviewResponse,
  Workspace,
} from '@/types';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { usageApi } from '@/api/usageApi';
import { etlApi } from '@/api/etlApi';
import { apiFetch, connectorsApi, timeAgo, getConnectorStatus } from './shared';
import { statusMeta } from '@/pages/notebooks/shared';
import { Badge } from '@/components/ui/badge';
import LineWaves from '@/components/background/LineWaves';
import { FolderCard } from '@/components/common/FolderCard';

// ---------- UI Constants ----------
type DisplayStatus = 'ready' | 'running' | 'draft';
type SourceStatus = 'connected' | 'syncing' | 'error';

interface HomeData {
  notebooks: Notebook[];
  connectors: ConnectorSummary[];
  workspaces: Workspace[];
  pipelines: PipelineRead[];
  models: MLModelRead[];
}

const EMPTY_DATA: HomeData = {
  notebooks: [],
  connectors: [],
  workspaces: [],
  pipelines: [],
  models: [],
};

const STATUS_DOT: Record<DisplayStatus | SourceStatus, string> = {
  ready: 'bg-emerald-400',
  running: 'bg-amber-400 animate-pulse',
  draft: 'bg-stone-500',
  connected: 'bg-emerald-400',
  syncing: 'bg-amber-400 animate-pulse',
  error: 'bg-red-500',
};

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'approved') return 'default';
  if (status === 'archived') return 'secondary';
  return 'outline';
}

function byRecent<T extends { updated_at?: string | null; created_at?: string | null }>(a: T, b: T) {
  return (
    new Date(b.updated_at || b.created_at || 0).getTime() -
    new Date(a.updated_at || a.created_at || 0).getTime()
  );
}

export function HomePage() {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { user } = useAuth();
  const [now] = useState<Date>(() => new Date());
  const [data, setData] = useState<HomeData>(EMPTY_DATA);
  const [usage, setUsage] = useState<UsageOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const isDark = theme === 'dark';

  const firstName = user?.name?.trim().split(/\s+/)[0] ?? '';

  const greeting = useMemo(() => {
    const hour = now.getHours();
    if (hour < 5) return { part: 'LATE NIGHT LOG', line: 'Still up?' };
    if (hour < 12) return { part: 'MORNING LOG', line: 'Good morning' };
    if (hour < 18) return { part: 'AFTERNOON LOG', line: 'Good afternoon' };
    return { part: 'EVENING LOG', line: 'Good evening' };
  }, [now]);

  const today = useMemo(
    () => now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }).toUpperCase(),
    [now]
  );

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setFailed([]);

      // Each section loads independently: one failing integration should not
      // blank out the rest of the page.
      const [notebooks, connectors, workspaces, pipelines, models, overview] =
        await Promise.allSettled([
          apiFetch<Notebook[]>('/notebook'),
          connectorsApi.list(),
          apiFetch<Workspace[]>('/workspace'),
          etlApi.list(),
          etlApi.models(),
          usageApi.overview(30),
        ]);

      if (cancelled) return;

      const failures: string[] = [];
      const valueOr = <T,>(result: PromiseSettledResult<T>, label: string, fallback: T): T => {
        if (result.status === 'fulfilled') return result.value;
        console.error(`Failed to load ${label}:`, result.reason);
        failures.push(label);
        return fallback;
      };

      setData({
        notebooks: valueOr(notebooks, 'notebooks', []),
        connectors: valueOr(connectors, 'data sources', []),
        workspaces: valueOr(workspaces, 'workspaces', []),
        pipelines: valueOr(pipelines, 'pipelines', []),
        models: valueOr(models, 'models', []),
      });

      if (overview.status === 'fulfilled') {
        setUsage(overview.value);
      } else {
        console.error('Failed to load usage:', overview.reason);
        setUsage(null);
        failures.push('usage');
      }

      setFailed(failures);
      setLoading(false);
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const recentNotebooks = useMemo(
    () => [...data.notebooks].sort(byRecent).slice(0, 8),
    [data.notebooks]
  );
  const recentPipelines = useMemo(
    () => [...data.pipelines].sort(byRecent).slice(0, 4),
    [data.pipelines]
  );

  const readyNotebooks = data.notebooks.filter((n) => n.status === 'ready').length;
  const completedWorkspaces = data.workspaces.filter((w) => w.status === 'completed').length;
  const approvedPipelines = data.pipelines.filter((p) => p.status === 'approved').length;
  const totals = usage?.totals;

  return (
    <div className="flex h-full min-h-0 w-full overflow-y-auto rounded-xl font-sans">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
        .font-display { font-family: 'Fraunces', serif; }
        .font-body { font-family: 'Inter', sans-serif; }
        .font-data { font-family: 'JetBrains Mono', monospace; }
      `}</style>

      {/* Main */}
      <div className="relative flex-1">
        {/* Hero */}
        <section className="relative h-[400px] w-full overflow-hidden">
          <div className="absolute h-full w-[200%] -translate-x-1/4 overflow-hidden">
            <LineWaves
              speed={0.3}
              innerLineCount={32}
              outerLineCount={36}
              warpIntensity={1}
              rotation={-45}
              edgeFadeWidth={0}
              colorCycleSpeed={1}
              brightness={isDark ? 0.2 : 0.12}
              color1={isDark ? '#F97316' : '#FB923C'}
              color2={isDark ? '#F97316' : '#F59E0B'}
              color3={isDark ? '#ffffff' : '#FFF7ED'}
              enableMouseInteraction
              mouseInfluence={2}
            />
          </div>
          <div
            className={`absolute inset-0 bg-gradient-to-b ${
              isDark ? 'from-background/55 via-background/35 to-background' : 'from-background/10 via-background/35 to-background'
            }`}
          />
          <div className="relative z-10 flex h-full flex-col justify-center px-10">
            <span className="font-data text-xs tracking-[0.2em] text-muted-foreground">
              {greeting.part} · {today}
            </span>
            <h1 className="mt-3 font-display text-5xl font-medium leading-[1.05] text-foreground">
              {greeting.line}
              {firstName ? `, ${firstName}` : ''}.
            </h1>
            <p className="mt-2 max-w-md font-body text-sm text-muted-foreground">
              {loading
                ? 'Loading your workspace...'
                : `${data.notebooks.length} notebooks ready. Everything's exactly where you left it.`}
            </p>
          </div>
        </section>

        {/* Partial-failure banner */}
        {failed.length > 0 && (
          <section className="mt-8 px-10">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
              <span className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 shrink-0" />
                Some sections could not load: {failed.join(', ')}.
              </span>
              <button
                onClick={() => setReloadKey((key) => key + 1)}
                className="flex items-center gap-1.5 rounded-md border border-amber-500/40 px-3 py-1 font-data text-xs transition-colors hover:bg-amber-500/10"
              >
                <RefreshCw className="h-3 w-3" />
                Retry
              </button>
            </div>
          </section>
        )}

        {/* At a glance */}
        <section className="mt-10 grid grid-cols-2 gap-4 px-10 lg:grid-cols-4">
          <StatCard
            icon={NotebookText}
            label="Notebooks"
            value={formatNumber(data.notebooks.length)}
            hint={`${readyNotebooks} ready`}
            loading={loading}
            onClick={() => navigate('/notebooks')}
          />
          <StatCard
            icon={FolderPlus}
            label="Workspaces"
            value={formatNumber(data.workspaces.length)}
            hint={`${completedWorkspaces} completed`}
            loading={loading}
            onClick={() => navigate('/workspace')}
          />
          <StatCard
            icon={Workflow}
            label="Pipelines"
            value={formatNumber(data.pipelines.length)}
            hint={`${approvedPipelines} approved`}
            loading={loading}
            onClick={() => navigate('/pipelines')}
          />
          <StatCard
            icon={Cpu}
            label="Models"
            value={formatNumber(data.models.length)}
            hint="Trained by pipelines"
            loading={loading}
            onClick={() => navigate('/models')}
          />
        </section>

        {/* Recent notebooks rail */}
        <section className="mt-12 px-10">
          <div className="flex items-baseline justify-between">
            <h2 className="font-display text-xl font-medium text-foreground">Recent notebooks</h2>
            <button
              onClick={() => navigate('/notebooks')}
              className="flex items-center gap-1 font-data text-xs text-orange-500 transition-colors hover:text-orange-400"
            >
              View all <ArrowUpRight className="h-3 w-3" />
            </button>
          </div>
          <div className="mt-4 flex gap-4 overflow-x-auto pb-2 pl-3">
            {loading ? (
              <div className="w-full py-8 text-center text-muted-foreground">Loading notebooks...</div>
            ) : recentNotebooks.length === 0 ? (
              <div className="w-full py-8 text-center text-muted-foreground">
                <p className="text-sm">No notebooks yet.</p>
                <button
                  onClick={() => navigate('/notebooks')}
                  className="mt-2 text-orange-500 text-sm transition-colors hover:text-orange-400"
                >
                  Create your first notebook
                </button>
              </div>
            ) : (
              recentNotebooks.map((nb) => {
                const meta = statusMeta(nb.status);
                return (
                  <FolderCard
                    key={nb.id}
                    title={nb.name}
                    statusDotClass={meta.tabClass}
                    footerText={`${nb.cell_count} cell${nb.cell_count === 1 ? '' : 's'} · ${timeAgo(
                      nb.updated_at || nb.created_at || new Date().toISOString()
                    )}`}
                    onOpen={() => navigate(`/notebooks/${nb.id}`)}
                    className="w-[220px] shrink-0"
                  />
                );
              })
            )}
          </div>
        </section>

        {/* Pipelines + usage */}
        <section className="mt-12 grid grid-cols-1 gap-8 px-10 lg:grid-cols-3">
          {/* Recent pipelines */}
          <div className="lg:col-span-2">
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-xl font-medium text-foreground">Recent pipelines</h2>
              <button
                onClick={() => navigate('/pipelines')}
                className="flex items-center gap-1 font-data text-xs text-orange-500 transition-colors hover:text-orange-400"
              >
                View all <ArrowUpRight className="h-3 w-3" />
              </button>
            </div>
            <div className="mt-4 overflow-hidden rounded-2xl border border-border bg-card">
              {loading ? (
                <div className="space-y-3 p-4">
                  {[0, 1, 2].map((key) => (
                    <div key={key} className="h-12 animate-pulse rounded-lg bg-secondary/40" />
                  ))}
                </div>
              ) : recentPipelines.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">
                  <p className="text-sm">No pipelines yet.</p>
                  <button
                    onClick={() => navigate('/pipelines')}
                    className="mt-2 text-orange-500 text-sm transition-colors hover:text-orange-400"
                  >
                    Create a pipeline
                  </button>
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {recentPipelines.map((pipeline) => {
                    const taskCount = pipeline.definition.steps.length;
                    const updated = pipeline.updated_at || pipeline.created_at;
                    return (
                      <li key={pipeline.id}>
                        <button
                          type="button"
                          onClick={() => navigate(`/pipelines/${pipeline.id}`)}
                          className="group flex w-full flex-wrap items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-secondary/30 focus:outline-none focus-visible:bg-secondary/30"
                        >
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-orange-500/15 text-orange-400">
                            <Workflow className="h-4 w-4" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="truncate font-body text-sm font-medium text-foreground">
                                {pipeline.name}
                              </span>
                              <Badge variant={statusVariant(pipeline.status)}>
                                {pipeline.status.replaceAll('_', ' ')}
                              </Badge>
                              {pipeline.schedule_enabled && pipeline.schedule_mode !== 'manual' && (
                                <Badge variant="secondary" className="gap-1">
                                  <CalendarClock className="h-3 w-3" />
                                  {pipeline.schedule_mode}
                                </Badge>
                              )}
                            </span>
                            <span className="mt-0.5 block truncate font-data text-xs text-muted-foreground">
                              {taskCount} task{taskCount === 1 ? '' : 's'} · v{pipeline.version}
                              {updated ? ` · Updated ${timeAgo(updated)}` : ''}
                            </span>
                          </span>
                          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* Usage */}
          <div>
            <div className="flex items-baseline justify-between">
              <h2 className="font-display text-xl font-medium text-foreground">Usage · 30 days</h2>
              <button
                onClick={() => navigate('/usage')}
                className="flex items-center gap-1 font-data text-xs text-orange-500 transition-colors hover:text-orange-400"
              >
                Details <ArrowUpRight className="h-3 w-3" />
              </button>
            </div>
            <div className="mt-4 rounded-2xl border border-border bg-card p-5">
              {loading ? (
                <div className="space-y-3">
                  <div className="h-6 w-24 animate-pulse rounded bg-secondary/40" />
                  <div className="h-16 animate-pulse rounded-lg bg-secondary/40" />
                </div>
              ) : !usage || !totals ? (
                <div className="text-muted-foreground">
                  <p className="text-sm">No LLM usage recorded in the last 30 days.</p>
                  <button
                    onClick={() => navigate('/usage')}
                    className="mt-2 flex items-center gap-1.5 text-orange-500 text-sm transition-colors hover:text-orange-400"
                  >
                    <Gauge className="h-3.5 w-3.5" />
                    Open usage
                  </button>
                </div>
              ) : (
                <>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-display text-3xl font-medium tabular-nums text-foreground">
                      {formatNumber(totals.total_tokens)}
                    </span>
                    <span className="font-data text-xs text-muted-foreground">tokens</span>
                  </div>
                  <UsageSparkline daily={usage.daily} />
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3">
                    <div>
                      <dt className="font-data text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                        Requests
                      </dt>
                      <dd className="mt-0.5 font-data text-sm tabular-nums text-foreground">
                        {formatNumber(totals.requests)}
                      </dd>
                    </div>
                    <div>
                      <dt className="font-data text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                        Est. spend
                      </dt>
                      <dd className="mt-0.5 font-data text-sm tabular-nums text-foreground">
                        {formatCost(totals.estimated_cost_usd)}
                      </dd>
                    </div>
                  </dl>
                </>
              )}
            </div>
          </div>
        </section>

        {/* Data connectors */}
        <section className="mt-12 px-10 pb-10">
          <h2 className="font-display text-xl font-medium text-foreground">Connected sources</h2>
          <div className="mt-4 flex flex-wrap gap-3">
            {data.connectors.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No connectors configured yet.{' '}
                <button
                  onClick={() => navigate('/data-connectors')}
                  className="text-orange-500 transition-colors hover:text-orange-400"
                >
                  Connect a source
                </button>
              </p>
            ) : (
              data.connectors.map((src) => (
                <button
                  key={src.connector_id}
                  onClick={() => navigate('/data-connectors')}
                  className="flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 transition-colors hover:border-primary/30"
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[getConnectorStatus(src.status)]}`} />
                  <span className="font-data text-xs text-foreground">{src.name}</span>
                  <span className="font-data text-xs text-muted-foreground">· {src.type}</span>
                </button>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

// ---------- Pieces ----------

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  loading,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group rounded-2xl border border-border bg-card p-5 text-left transition-colors hover:border-primary/40 hover:bg-secondary/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 font-data text-[11px] uppercase tracking-[0.15em] text-muted-foreground">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-orange-500/15 text-orange-400">
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="truncate">{label}</span>
        </span>
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      <div className="mt-3 font-display text-3xl font-medium tabular-nums text-foreground">
        {loading ? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /> : value}
      </div>
      <p className="mt-0.5 font-body text-xs text-muted-foreground">{hint}</p>
    </button>
  );
}

function UsageSparkline({ daily }: { daily: UsageOverviewResponse['daily'] }) {
  const points = useMemo(() => {
    const byDay = new Map<string, number>();
    for (const point of daily) {
      byDay.set(point.day, (byDay.get(point.day) ?? 0) + point.total_tokens);
    }
    return [...byDay.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .slice(-14)
      .map(([day, tokens]) => ({ day, tokens }));
  }, [daily]);

  if (points.length === 0) {
    return <p className="mt-4 text-xs text-muted-foreground">No daily activity yet.</p>;
  }

  const peak = Math.max(...points.map((point) => point.tokens), 1);

  return (
    <div className="mt-4 flex h-16 items-end gap-1" aria-hidden="true">
      {points.map((point) => (
        <div
          key={point.day}
          className="flex-1 rounded-sm bg-orange-500/70 transition-colors hover:bg-orange-500"
          style={{ height: `${Math.max(6, Math.round((point.tokens / peak) * 100))}%` }}
          title={`${point.day}: ${formatNumber(point.tokens)} tokens`}
        />
      ))}
    </div>
  );
}

// ---------- Formatting ----------

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value ?? 0);
}

function formatCost(value: number | null | undefined): string {
  const amount = value ?? 0;
  if (amount > 0 && amount < 0.01) return '<$0.01';
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(amount);
}
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowRight,
  ArrowUpRight,
  CalendarClock,
  ChevronRight,
  Cpu,
  Database,
  FolderPlus,
  Gauge,
  Loader2,
  NotebookText,
  Plus,
  RefreshCw,
  Workflow,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type {
  ConnectorSummary,
  MLModelRead,
  Notebook,
  PipelineRead,
  UsageOverviewResponse,
  Workspace,
} from '@/types';
import { useAuth } from '@/contexts/AuthContext';
import { usageApi } from '@/api/usageApi';
import { etlApi } from '@/api/etlApi';
import { apiFetch, connectorsApi, getConnectorStatus, timeAgo } from './shared';
import { statusMeta } from '@/pages/notebooks/shared';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import DotField from '@/components/background/DotField';
import { FolderCard } from '@/components/common/FolderCard';
import { cn } from '@/lib/utils';

// ——————————————————————————————————————————————————————————————
// Types and constants

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

interface QuickAction {
  label: string;
  hint: string;
  icon: LucideIcon;
  to: string;
}

const QUICK_ACTIONS: QuickAction[] = [
  { label: 'New notebook', hint: 'Start from a blank canvas', icon: Plus, to: '/notebooks' },
  { label: 'New workspace', hint: 'Build and compare models', icon: FolderPlus, to: '/workspace' },
  { label: 'New pipeline', hint: 'Describe an ETL flow in chat', icon: Workflow, to: '/pipelines' },
  { label: 'Connect a source', hint: 'Link a database or API', icon: Database, to: '/data-connectors' },
];

const SOURCE_DOT: Record<string, string> = {
  connected: 'bg-emerald-500',
  syncing: 'bg-amber-500 animate-pulse',
  error: 'bg-destructive',
};

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'approved') return 'default';
  if (status === 'archived') return 'secondary';
  return 'outline';
}

// ——————————————————————————————————————————————————————————————
// Page

export function HomePage(): JSX.Element {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [now] = useState<Date>(() => new Date());

  const [data, setData] = useState<HomeData>(EMPTY_DATA);
  const [usage, setUsage] = useState<UsageOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string[]>([]);
  const [reloadKey, setReloadKey] = useState(0);

  const greeting = useMemo(() => {
    const hour = now.getHours();
    if (hour < 5) return { part: 'Late night', line: 'Working late' };
    if (hour < 12) return { part: 'Morning', line: 'Good morning' };
    if (hour < 18) return { part: 'Afternoon', line: 'Good afternoon' };
    return { part: 'Evening', line: 'Good evening' };
  }, [now]);

  const today = useMemo(
    () =>
      now.toLocaleDateString(undefined, {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
      }),
    [now],
  );

  const firstName = user?.name?.trim().split(/\s+/)[0] ?? '';

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setFailed([]);

      // Each section loads independently: a broken connector integration should
      // not blank out the notebooks or usage panels.
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
    () =>
      [...data.notebooks]
        .sort(
          (a, b) =>
            new Date(b.updated_at || b.created_at || 0).getTime() -
            new Date(a.updated_at || a.created_at || 0).getTime(),
        )
        .slice(0, 6),
    [data.notebooks],
  );

  const recentPipelines = useMemo(
    () =>
      [...data.pipelines]
        .sort(
          (a, b) =>
            new Date(b.updated_at || b.created_at || 0).getTime() -
            new Date(a.updated_at || a.created_at || 0).getTime(),
        )
        .slice(0, 4),
    [data.pipelines],
  );

  const readyNotebooks = data.notebooks.filter((notebook) => notebook.status === 'ready').length;
  const completedWorkspaces = data.workspaces.filter(
    (workspace) => workspace.status === 'completed',
  ).length;
  const approvedPipelines = data.pipelines.filter(
    (pipeline) => pipeline.status === 'approved',
  ).length;

  const totals = usage?.totals;

  return (
    <main className="relative h-full min-h-0 overflow-y-auto bg-background">
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
          bulgeOnly
          gradientFrom="#592603"
          gradientTo="#613508"
          glowColor="#af4d08"
        />
      </div>

      <div className="relative mx-auto min-h-full w-full max-w-6xl px-6 py-10">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-6">
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
              {greeting.part} · {today}
            </p>
            <h1 className="mt-2 bg-primary-gradient bg-clip-text text-3xl font-semibold tracking-tight text-transparent">
              {greeting.line}
              {firstName ? `, ${firstName}` : ''}
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              Pick up where you left off, or describe a new ETL pipeline and let SQRL build it.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => navigate('/data-connectors')}>
              <Database />
              Connect source
            </Button>
            <Button onClick={() => navigate('/notebooks')}>
              <Plus />
              New notebook
            </Button>
          </div>
        </header>

        {failed.length > 0 && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
            <span className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              Some sections could not load: {failed.join(', ')}.
            </span>
            <Button variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>
              <RefreshCw />
              Retry
            </Button>
          </div>
        )}

        <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
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

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <section className="rounded-xl border border-border bg-card shadow-sm">
              <SectionHeader
                icon={NotebookText}
                title="Recent notebooks"
                description="Your latest analysis canvases, newest first."
                action={
                  <Button variant="ghost" size="sm" onClick={() => navigate('/notebooks')}>
                    View all
                    <ArrowRight />
                  </Button>
                }
              />

              {loading ? (
                <div className="flex gap-3 px-5 pb-5 pt-4">
                  {[0, 1, 2].map((key) => (
                    <div
                      key={key}
                      className="h-[170px] w-[230px] shrink-0 animate-pulse rounded-xl border border-border bg-secondary/40"
                    />
                  ))}
                </div>
              ) : recentNotebooks.length === 0 ? (
                <EmptyState
                  icon={NotebookText}
                  title="No notebooks yet"
                  description="Create a notebook to explore data, run EDA, and pin dashboards."
                  actionLabel="Create your first notebook"
                  onAction={() => navigate('/notebooks')}
                />
              ) : (
                <div className="flex gap-3 overflow-x-auto px-5 pb-5 pt-4">
                  {recentNotebooks.map((notebook) => {
                    const meta = statusMeta(notebook.status);
                    return (
                      <FolderCard
                        key={notebook.id}
                        title={notebook.name}
                        statusDotClass={meta.tabClass}
                        footerText={`${notebook.cell_count} cell${
                          notebook.cell_count === 1 ? '' : 's'
                        } · ${timeAgo(notebook.updated_at || notebook.created_at)}`}
                        onOpen={() => navigate(`/notebooks/${notebook.id}`)}
                        className="w-[230px] shrink-0"
                      />
                    );
                  })}
                </div>
              )}
            </section>

            <section className="rounded-xl border border-border bg-card shadow-sm">
              <SectionHeader
                icon={Workflow}
                title="Recent pipelines"
                description="ETL flows and the state of their latest version."
                action={
                  <Button variant="ghost" size="sm" onClick={() => navigate('/pipelines')}>
                    View all
                    <ArrowRight />
                  </Button>
                }
              />

              {loading ? (
                <div className="space-y-3 px-5 pb-5 pt-4">
                  {[0, 1, 2].map((key) => (
                    <div key={key} className="h-12 animate-pulse rounded-lg bg-secondary/40" />
                  ))}
                </div>
              ) : recentPipelines.length === 0 ? (
                <EmptyState
                  icon={Workflow}
                  title="No pipelines yet"
                  description="Describe a source, transformations, and a destination in chat to build one."
                  actionLabel="Create a pipeline"
                  onAction={() => navigate('/pipelines')}
                />
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
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary">
                            <Workflow className="h-4 w-4 text-muted-foreground" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="truncate text-sm font-medium text-foreground">
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
                            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
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
            </section>
          </div>

          <div className="space-y-6">
            <section className="rounded-xl border border-border bg-card shadow-sm">
              <SectionHeader
                icon={Plus}
                title="Quick actions"
                description="The fastest way into each part of the app."
              />
              <div className="space-y-1 p-3">
                {QUICK_ACTIONS.map(({ label, hint, icon: Icon, to }) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => navigate(to)}
                    className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-secondary/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                      <Icon className="h-4 w-4 text-muted-foreground" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">
                        {label}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">{hint}</span>
                    </span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </button>
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-border bg-card shadow-sm">
              <SectionHeader
                icon={Database}
                title="Connected sources"
                description="Databases and APIs available to notebooks and pipelines."
                action={
                  <Button variant="ghost" size="sm" onClick={() => navigate('/data-connectors')}>
                    Manage
                    <ArrowRight />
                  </Button>
                }
              />

              {loading ? (
                <div className="space-y-2 px-5 pb-5 pt-4">
                  {[0, 1].map((key) => (
                    <div key={key} className="h-8 animate-pulse rounded-lg bg-secondary/40" />
                  ))}
                </div>
              ) : data.connectors.length === 0 ? (
                <div className="px-5 pb-5 pt-4">
                  <p className="text-sm text-muted-foreground">
                    No connectors configured yet.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-3"
                    onClick={() => navigate('/data-connectors')}
                  >
                    <Plus />
                    Connect a source
                  </Button>
                </div>
              ) : (
                <ul className="space-y-1 px-3 pb-3">
                  {data.connectors.slice(0, 5).map((source) => {
                    const status = getConnectorStatus(source.status);
                    return (
                      <li key={source.connector_id}>
                        <button
                          type="button"
                          onClick={() => navigate('/data-connectors')}
                          className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-secondary/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <span
                            aria-hidden="true"
                            className={cn('h-2 w-2 shrink-0 rounded-full', SOURCE_DOT[status])}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-foreground">
                              {source.name}
                            </span>
                            <span className="block truncate text-xs capitalize text-muted-foreground">
                              {source.type} · {status}
                            </span>
                          </span>
                          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="rounded-xl border border-border bg-card shadow-sm">
              <SectionHeader
                icon={Gauge}
                title="Usage · 30 days"
                description="LLM tokens and estimated spend across your features."
                action={
                  <Button variant="ghost" size="sm" onClick={() => navigate('/usage')}>
                    Details
                    <ArrowRight />
                  </Button>
                }
              />

              {loading ? (
                <div className="space-y-3 px-5 pb-5 pt-4">
                  <div className="h-6 w-24 animate-pulse rounded bg-secondary/40" />
                  <div className="h-16 animate-pulse rounded-lg bg-secondary/40" />
                </div>
              ) : !usage || !totals ? (
                <div className="px-5 pb-5 pt-4">
                  <p className="text-sm text-muted-foreground">
                    No LLM usage recorded in the last 30 days.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-3"
                    onClick={() => navigate('/usage')}
                  >
                    <Gauge />
                    Open usage
                  </Button>
                </div>
              ) : (
                <div className="px-5 pb-5 pt-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-2xl font-semibold tabular-nums text-foreground">
                      {formatNumber(totals.total_tokens)}
                    </span>
                    <span className="text-xs text-muted-foreground">tokens</span>
                  </div>
                  <UsageSparkline daily={usage.daily} />
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3">
                    <div>
                      <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                        Requests
                      </dt>
                      <dd className="mt-0.5 text-sm font-medium tabular-nums text-foreground">
                        {formatNumber(totals.requests)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                        Est. spend
                      </dt>
                      <dd className="mt-0.5 text-sm font-medium tabular-nums text-foreground">
                        {formatCost(totals.estimated_cost_usd)}
                      </dd>
                    </div>
                  </dl>
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}

// ——————————————————————————————————————————————————————————————
// Pieces

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
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-colors hover:border-primary/40 hover:bg-secondary/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-secondary">
            <Icon className="h-3.5 w-3.5 text-primary" />
          </span>
          <span className="truncate">{label}</span>
        </span>
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums text-foreground">
        {loading ? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /> : value}
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
    </button>
  );
}

function SectionHeader({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary">
          <Icon className="h-4 w-4 text-primary" />
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description && (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          )}
        </div>
      </div>
      {action}
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  onAction,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-secondary">
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
      <div>
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      </div>
      <Button variant="outline" size="sm" onClick={onAction}>
        <Plus />
        {actionLabel}
      </Button>
    </div>
  );
}

function UsageSparkline({
  daily,
}: {
  daily: UsageOverviewResponse['daily'];
}): JSX.Element {
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
          className="flex-1 rounded-sm bg-primary/70 transition-colors hover:bg-primary"
          style={{ height: `${Math.max(6, Math.round((point.tokens / peak) * 100))}%` }}
          title={`${point.day}: ${formatNumber(point.tokens)} tokens`}
        />
      ))}
    </div>
  );
}

// ——————————————————————————————————————————————————————————————
// Formatting

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

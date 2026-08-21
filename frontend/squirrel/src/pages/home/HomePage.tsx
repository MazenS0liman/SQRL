import { useEffect, useRef, useMemo, useState } from 'react';
import { Plus, FolderPlus, Database, Cpu, ArrowUpRight, AlertCircle } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { Notebook, ConnectorSummary } from '@/types';
import { useTheme } from '@/contexts/ThemeContext';
import { apiFetch, connectorsApi, timeAgo, getNotebookStatus, getConnectorStatus } from './shared';
import { statusMeta } from '@/pages/notebooks/shared';
import LineWaves from '@/components/background/LineWaves';
import { FolderCard } from '@/components/common/FolderCard';

// ---------- UI Constants ----------
type DisplayStatus = 'ready' | 'running' | 'draft';
type SourceStatus = 'connected' | 'syncing' | 'error';

interface QuickAction {
  label: string;
  sub: string;
  icon: LucideIcon;
  rotate: string;
  span: string;
  href?: string;
}

const STATUS_DOT: Record<DisplayStatus | SourceStatus, string> = {
  ready: 'bg-emerald-400',
  running: 'bg-amber-400 animate-pulse',
  draft: 'bg-stone-500',
  connected: 'bg-emerald-400',
  syncing: 'bg-amber-400 animate-pulse',
  error: 'bg-red-500',
};

const QUICK_ACTIONS: QuickAction[] = [
  { label: 'New notebook', sub: 'Start from a blank canvas', icon: Plus, rotate: '-rotate-1', span: 'md:col-span-2', href: '/notebooks' },
  { label: 'New workspace', sub: 'Build your model', icon: FolderPlus, rotate: 'rotate-1', span: '', href: '/workspace' },
  { label: 'Connect a source', sub: 'Connect to your data sources', icon: Database, rotate: '-rotate-2', span: '', href: '/data-connectors' },
  { label: 'Browse models', sub: 'See what\u2019s available', icon: Cpu, rotate: 'rotate-2', span: '', href: '/models' },
];

export function HomePage() {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const [now] = useState<Date>(() => new Date());
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [connectors, setConnectors] = useState<ConnectorSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isDark = theme === 'dark';

  const greeting = useMemo(() => {
    const hour = now.getHours();
    if (hour < 5) return { part: 'LATE NIGHT LOG', line: 'Still up?' };
    if (hour < 12) return { part: 'MORNING LOG', line: 'Good morning.' };
    if (hour < 18) return { part: 'AFTERNOON LOG', line: 'Good afternoon.' };
    return { part: 'EVENING LOG', line: 'Good evening.' };
  }, [now]);
  
  const today = useMemo(
    () => now.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }).toUpperCase(),
    [now]
  );

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      setError(null);
      try {
        const [notebookData, connectorData] = await Promise.all([
          apiFetch<Notebook[]>('/notebook'),
          connectorsApi.list(),
        ]);
        
        // Sort notebooks by creation date (most recent first)
        const sortedNotebooks = notebookData.sort((a, b) => 
          new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
        );
        
        setNotebooks(sortedNotebooks.slice(0, 8));
        setConnectors(connectorData);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load data';
        setError(message);
        console.error('Failed to load home page data:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  return (
    <div className="flex h-[720px] w-full rounded-xl font-sans">
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
            </h1>
            <p className="mt-2 max-w-md font-body text-sm text-muted-foreground">
              {loading ? 'Loading your notebooks...' : `${notebooks.length} notebooks ready. Everything's exactly where you left it.`}
            </p>
          </div>
        </section>

        {/* Quick action cluster */}
        <section className="relative z-20 -mt-8 grid grid-cols-2 gap-4 px-10 md:grid-cols-4">
          {QUICK_ACTIONS.map(({ label, sub, icon: Icon, rotate, span, href }) => (
            <button
              key={label}
              onClick={() => href && navigate(href)}
              className={`group ${span} ${rotate} rounded-2xl border border-border bg-card/95 p-5 text-left shadow-lg shadow-black/10 transition-transform duration-200 hover:rotate-0 hover:scale-[1.02]`}
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-orange-500/15 text-orange-400">
                <Icon className="h-4 w-4" />
              </div>
              <div className="mt-3 font-display text-base font-medium text-foreground">{label}</div>
              <div className="mt-0.5 font-body text-xs text-muted-foreground">{sub}</div>
            </button>
          ))}
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
            ) : error ? (
              <div className="flex items-center gap-2 py-8 text-red-500">
                <AlertCircle className="h-4 w-4" />
                <span className="text-sm">{error}</span>
              </div>
            ) : notebooks.length === 0 ? (
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
              notebooks.map((nb) => {
                const meta = statusMeta(nb.status);
                return (
                  <FolderCard
                    key={nb.id}
                    title={nb.name}
                    statusDotClass={meta.tabClass}
                    footerText={timeAgo(nb.created_at || new Date().toISOString())}
                    onOpen={() => navigate(`/notebooks/${nb.id}`)}
                    className="w-[220px]"
                  />
                );
              })
            )}
          </div>
        </section>

        {/* Data connectors */}
        <section className="mt-10 px-10 pb-10">
          <h2 className="font-display text-xl font-medium text-foreground">Connected sources</h2>
          <div className="mt-4 flex flex-wrap gap-3">
            {connectors.length === 0 ? (
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
              connectors.map((src) => (
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
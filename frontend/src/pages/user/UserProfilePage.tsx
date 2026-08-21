import React, { useEffect, useState, useCallback } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import {
  CalendarDays,
  Mail,
  ShieldCheck,
  LogOut,
  UserCircle2,
  Clock3,
  BadgeInfo,
  User,
  KeyRound,
  Plus,
  Trash2,
  Loader2,
} from 'lucide-react';
import DotField from '@/components/background/DotField';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { apiFetch } from '@/pages/workspace/shared';

interface TokenRead {
  id: string;
  provider: string;
  label: string | null;
  token_preview: string;
  created_at: string;
  last_used_at: string | null;
}

interface TokenCreateResponse {
  token: TokenRead;
}

interface TokenListResponse {
  tokens: TokenRead[];
}

const PROVIDERS = [
  { value: 'gemini', label: 'Gemini' },
  { value: 'groq', label: 'Groq' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'huggingface', label: 'HuggingFace' },
  { value: 'github', label: 'GitHub' },
  { value: 'custom', label: 'Custom' },
];

export default function UserProfilePage(): JSX.Element {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { theme } = useTheme();
  const isDark = theme === 'dark';

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="relative min-h-full overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <DotField
          dotRadius={1.5}
          dotSpacing={14}
          bulgeStrength={67}
          glowRadius={160}
          sparkle={false}
          waveAmplitude={0}
          cursorRadius={500}
          cursorForce={0}
          bulgeOnly={false}
          gradientFrom={isDark ? '#592603' : '#E7D8C5'}
          gradientTo={isDark ? '#613508' : '#F5EBDD'}
          glowColor={isDark ? '#120F17' : '#F7F1E8'}
        />
      </div>

      <div className="relative mx-auto min-h-full w-full max-w-5xl px-6 py-10">
        <header className="mb-8 flex items-end justify-between gap-4 border-b border-border pb-6">
          <div>
            <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
              User profile
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">Detailed information about the signed-in user.</p>
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
          <section className="rounded-3xl border border-border bg-card p-6 shadow-sm">
            <div className="flex items-start gap-4">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-primary-gradient text-lg font-semibold text-primary-foreground shadow-sm">
                {user.avatarSeed}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-2xl font-semibold tracking-tight">{user.username}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{user.role}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-3 py-1 text-xs text-muted-foreground">
                    <BadgeInfo className="h-3.5 w-3.5" />
                    Local session
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-3 py-1 text-xs text-muted-foreground">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    Active
                  </span>
                </div>
              </div>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              <InfoRow icon={User} label="Username" value={user.username} />
              <InfoRow icon={Mail} label="Email" value={user.email} />
              <InfoRow icon={CalendarDays} label="Joined" value={formatDate(user.joinedAt)} />
              <InfoRow icon={Clock3} label="Last login" value={formatDateTime(user.lastLoginAt)} />
              <InfoRow icon={UserCircle2} label="User ID" value={user.id} />
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <button onClick={() => navigate('/settings')} className="rounded-xl border border-border px-4 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary">
                Edit preferences
              </button>
              <button onClick={handleLogout} className="rounded-xl bg-destructive px-4 py-2.5 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90">
                <span className="inline-flex items-center gap-2">
                  <LogOut className="h-4 w-4" />
                  Log out
                </span>
              </button>
            </div>
          </section>

          <section className="space-y-4">
            <div className="rounded-3xl border border-border bg-card p-6 shadow-sm">
              <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">Account summary</h3>
              <div className="mt-4 space-y-3 text-sm">
                <SummaryItem label="Name" value={user.name} />
                <SummaryItem label="Email" value={user.email} />
                <SummaryItem label="Role" value={user.role} />
                <SummaryItem label="Theme" value={`${theme} mode`} />
              </div>
            </div>

            <div className="rounded-3xl border border-border bg-card p-6 shadow-sm">
              <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">What you can do here</h3>
              <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                <li>Review the session identity used across the app.</li>
                <li>Jump to settings to change the color theme.</li>
                <li>Log out to return to the local sign-in page.</li>
                <li>Save API tokens below to reuse them later.</li>
              </ul>
            </div>
          </section>
        </div>

        <TokensSection />
      </div>
    </div>
  );
}

function TokensSection(): JSX.Element {
  const [tokens, setTokens] = useState<TokenRead[]>([]);
  const [provider, setProvider] = useState(PROVIDERS[0].value);
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadTokens = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<TokenListResponse>('/token');
      setTokens(data.tokens ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load saved tokens');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTokens();
  }, [loadTokens]);

  const handleAddToken = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const data = await apiFetch<TokenCreateResponse>('/token', {
        method: 'POST',
        body: JSON.stringify({ 
          provider, 
          label: label.trim() || null, 
          token: value.trim() 
        }),
      });
      setTokens((prev) => [data.token, ...prev]);
      setValue('');
      setLabel('');
      setSuccess('Token saved successfully!');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save token');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    setError(null);
    setSuccess(null);
    try {
      await apiFetch(`/token/${id}`, { method: 'DELETE' });
      setTokens((prev) => prev.filter((t) => t.id !== id));
      setSuccess('Token deleted successfully!');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete token');
    }
  };

  return (
    <section className="mt-6 rounded-3xl border border-border bg-card p-6 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">API tokens</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Save provider API keys so you don't have to re-enter them each time. They're encrypted before storage.
          </p>
        </div>
      </div>

      <form onSubmit={handleAddToken} className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Provider</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full rounded-xl border border-border bg-secondary/50 px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Label (optional)</label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. personal key"
            className="w-full rounded-xl border border-border bg-secondary/50 px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Token</label>
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Paste your API key"
            autoComplete="off"
            className="w-full rounded-xl border border-border bg-secondary/50 px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div className="sm:col-span-2 flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5" />
            Encrypted at rest, never shown again in full
          </span>
          <button
            type="submit"
            disabled={submitting || !value.trim()}
            className="inline-flex items-center gap-2 rounded-xl bg-primary-gradient px-4 py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Save token
          </button>
        </div>
      </form>

      {error && (
        <div className="mt-3 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {success && (
        <div className="mt-3 rounded-lg bg-primary/10 p-3 text-sm text-primary">
          {success}
        </div>
      )}

      <div className="mt-6 border-t border-border pt-4">
        {loading ? (
          <div className="flex justify-center text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tokens saved yet.</p>
        ) : (
          <ul className="space-y-3">
            {tokens.map((t) => (
              <li
                key={t.id}
                className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-secondary/50 p-4"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-foreground">
                      {PROVIDERS.find((p) => p.value === t.provider)?.label ?? t.provider}
                      {t.label ? ` · ${t.label}` : ''}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{t.token_preview}</div>
                    {t.last_used_at && (
                      <div className="truncate text-[10px] text-muted-foreground/70">
                        Last used: {formatDateTime(t.last_used_at)}
                      </div>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(t.id)}
                  className="shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
                  aria-label="Delete token"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function InfoRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }): JSX.Element {
  return (
    <div className="rounded-2xl border border-border bg-secondary/50 p-4">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-2 break-all text-sm font-medium text-foreground">{value}</div>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border/60 pb-2 last:border-b-0 last:pb-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[60%] text-right font-medium text-foreground">{value}</span>
    </div>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}
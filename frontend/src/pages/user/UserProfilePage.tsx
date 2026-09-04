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
  Moon,
  Sun,
  Palette,
  Check,
} from 'lucide-react';
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
  const { theme, setTheme } = useTheme();

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="min-h-full overflow-y-auto bg-background text-foreground">
      <div className="mx-auto min-h-full w-full max-w-3xl px-6 py-8 sm:px-10">
        <div>
          <section className="border-b border-border pb-8">
            <div className="flex items-start gap-4">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg bg-primary text-lg font-semibold text-primary-foreground">
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

            <div className="mt-6 grid gap-x-8 gap-y-0 sm:grid-cols-2">
              <InfoRow icon={User} label="Username" value={user.username} />
              <InfoRow icon={Mail} label="Email" value={user.email} />
              <InfoRow icon={CalendarDays} label="Joined" value={formatDate(user.joinedAt)} />
              <InfoRow icon={Clock3} label="Last login" value={formatDateTime(user.lastLoginAt)} />
              <InfoRow icon={UserCircle2} label="User ID" value={user.id} />
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <button onClick={handleLogout} className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground transition-opacity hover:opacity-90">
                <span className="inline-flex items-center gap-2">
                  <LogOut className="h-4 w-4" />
                  Log out
                </span>
              </button>
            </div>
          </section>
        </div>

        <AppearanceSection theme={theme} setTheme={setTheme} />
        <TokensSection />
      </div>
    </div>
  );
}

function AppearanceSection({
  theme,
  setTheme,
}: {
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
}): JSX.Element {
  const themes = [
    { id: 'light' as const, label: 'Light', icon: Sun, swatch: ['#F5F1EA', '#FFFFFF', '#E8895C'] },
    { id: 'dark' as const, label: 'Dark', icon: Moon, swatch: ['#17110D', '#0B0908', '#E8895C'] },
  ];

  return (
    <section className="mt-8 border-b border-border pb-8">
      <div className="mb-1 flex items-center gap-2">
        <Palette className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">Appearance</h2>
      </div>
      <p className="mb-5 text-sm text-muted-foreground">Choose how Squirrel looks on this device.</p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {themes.map(({ id, label, icon: Icon, swatch }) => {
          const active = theme === id;
          return (
            <button
              key={id}
              onClick={() => setTheme(id)}
              aria-pressed={active}
              className={`overflow-hidden rounded-md border text-left transition-colors ${
                active ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/40'
              }`}
            >
              <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
                <Icon className="h-4 w-4 text-muted-foreground" />
                <span className="flex-1 text-sm font-medium text-foreground">{label}</span>
                {active && <Check className="h-4 w-4 text-primary" strokeWidth={3} />}
              </div>
              <div className="flex h-16 items-center gap-2 p-3" style={{ backgroundColor: swatch[0] }}>
                <span className="h-7 w-7 rounded border border-black/10" style={{ backgroundColor: swatch[1] }} />
                <span className="flex-1 space-y-1">
                  <span className="block h-1.5 w-3/4 rounded-full" style={{ backgroundColor: swatch[2] }} />
                  <span className="block h-1.5 w-1/2 rounded-full opacity-40" style={{ backgroundColor: swatch[1] }} />
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
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
    <section className="mt-8 border-b border-border pb-8">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground">API tokens</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Save provider API keys so you don't have to re-enter them each time. They're encrypted before storage.
          </p>
        </div>
      </div>

      <form onSubmit={handleAddToken} className="mt-5 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Provider</label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
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
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
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
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
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
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
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
                className="flex items-center justify-between gap-4 border-t border-border px-1 py-4"
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
    <div className="border-b border-border/70 py-3">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1 break-all text-sm text-foreground">{value}</div>
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
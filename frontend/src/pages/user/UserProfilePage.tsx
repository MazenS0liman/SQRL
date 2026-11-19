import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import {
  BadgeCheck,
  CalendarDays,
  Check,
  Clock3,
  Copy,
  Cpu,
  Eye,
  EyeOff,
  Fingerprint,
  Gauge,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  Moon,
  Palette,
  Pencil,
  Plus,
  ShieldCheck,
  Sun,
  Trash2,
  UserRound,
  Workflow,
} from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { usageApi } from '@/api/usageApi';
import { apiFetch, cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import DotField from '@/components/background/DotField';
import type { UsageOverviewResponse } from '@/types';

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
  { value: 'gemini', label: 'Google Gemini', hint: 'AI Studio API key' },
  { value: 'groq', label: 'Groq', hint: 'Groq Cloud API key' },
  { value: 'openrouter', label: 'OpenRouter', hint: 'OpenRouter API key' },
  { value: 'huggingface', label: 'Hugging Face', hint: 'Access token' },
  { value: 'github', label: 'GitHub', hint: 'Personal access token' },
  { value: 'custom', label: 'Custom', hint: 'Any provider key' },
] as const;

const PROVIDER_DOT: Record<string, string> = {
  gemini: 'bg-blue-500',
  groq: 'bg-orange-500',
  openrouter: 'bg-emerald-500',
  huggingface: 'bg-yellow-500',
  github: 'bg-zinc-500',
  custom: 'bg-purple-500',
};

function providerLabel(value: string): string {
  return PROVIDERS.find((provider) => provider.value === value)?.label ?? value;
}

/**
 * Profile Page
 * ============
 *
 * Account centre for the signed-in user. It pulls the same usage figures the
 * Usage page renders (last 30 days), lists the saved provider keys, and hosts
 * the account, security, and appearance controls. Editing a profile or
 * rotating a password goes through AuthContext so the cached user and the
 * stored bearer token stay in sync everywhere.
 */
export default function UserProfilePage(): JSX.Element {
  const { user, logout, updateProfile, changePassword } = useAuth();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();

  const [tokens, setTokens] = useState<TokenRead[]>([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokensError, setTokensError] = useState<string | null>(null);
  const [overview, setOverview] = useState<UsageOverviewResponse | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);

  const [editOpen, setEditOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [addTokenOpen, setAddTokenOpen] = useState(false);
  const [tokenToDelete, setTokenToDelete] = useState<TokenRead | null>(null);
  const [deletingToken, setDeletingToken] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const loadTokens = useCallback(async () => {
    setTokensLoading(true);
    setTokensError(null);
    try {
      const data = await apiFetch<TokenListResponse>('/token');
      setTokens(data.tokens ?? []);
    } catch (err) {
      setTokensError(err instanceof Error ? err.message : 'Failed to load saved tokens.');
    } finally {
      setTokensLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTokens();
  }, [loadTokens]);

  useEffect(() => {
    let cancelled = false;
    setUsageLoading(true);
    void usageApi
      .overview(30)
      .then((data) => {
        if (!cancelled) setOverview(data);
      })
      .catch(() => {
        // Usage is supplementary on this page — a failure should not block
        // the account controls, so the cards simply show placeholders.
        if (!cancelled) setOverview(null);
      })
      .finally(() => {
        if (!cancelled) setUsageLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const totals = overview?.totals;

  const handleDeleteToken = async () => {
    if (!tokenToDelete) return;
    setDeletingToken(true);
    try {
      await apiFetch(`/token/${tokenToDelete.id}`, { method: 'DELETE' });
      setTokens((current) => current.filter((token) => token.id !== tokenToDelete.id));
      toast.success('API token deleted');
      setTokenToDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete token.');
    } finally {
      setDeletingToken(false);
    }
  };

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      setSigningOut(false);
    }
  };

  if (!user) {
    return <Navigate to="/login" replace />;
  }

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
          <div>
            <h1 className="bg-primary-gradient bg-clip-text text-3xl font-semibold text-transparent">
              Profile
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Manage your identity, credentials, and workspace preferences.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => navigate('/usage')}>
              <Gauge />
              Usage
            </Button>
            <Button variant="outline" onClick={() => void handleSignOut()} disabled={signingOut}>
              {signingOut ? <Loader2 className="animate-spin" /> : <LogOut />}
              Sign out
            </Button>
          </div>
        </header>

        <section className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <div className="relative h-24 bg-primary-gradient">
            <div
              aria-hidden="true"
              className="absolute inset-0 opacity-20"
              style={{
                backgroundImage:
                  'radial-gradient(circle at 20% 30%, rgba(255,255,255,0.9) 0, transparent 35%), radial-gradient(circle at 80% 20%, rgba(255,255,255,0.7) 0, transparent 30%)',
              }}
            />
          </div>
          <div className="px-6 pb-6">
            <div className="relative z-10 -mt-10 flex flex-wrap items-end justify-between gap-4">
              <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-end sm:gap-4">
                {/* Avatar overlaps the banner */}
                <div className="-mt-10 flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl border-4 border-card bg-primary-gradient text-2xl font-semibold text-primary-foreground shadow-sm">
                  {user.avatarSeed}
                </div>

                {/* Name sits fully on the card surface */}
                <div className="min-w-0 sm:pb-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-2xl font-semibold tracking-tight text-foreground">
                      {user.name}
                    </h2>
                    <Badge variant="secondary" className="gap-1">
                      <BadgeCheck className="h-3 w-3" />
                      {user.role}
                    </Badge>
                  </div>
                  <p className="mt-1 truncate text-sm text-muted-foreground">
                    @{user.username} · {user.email}
                  </p>
                </div>
              </div>

              <Button className="mt-3" onClick={() => setEditOpen(true)}>
                <Pencil />
                Edit profile
              </Button>
            </div>

            <div className="mt-6 grid gap-4 border-t border-border pt-5 sm:grid-cols-3">
              <MetaItem icon={CalendarDays} label="Member since" value={formatDate(user.joinedAt)} />
              <MetaItem icon={Clock3} label="Last sign-in" value={formatDateTime(user.lastLoginAt)} />
              <MetaItem
                icon={Fingerprint}
                label="Account ID"
                value={user.id}
                mono
                action={
                  <button
                    type="button"
                    onClick={() => void copyToClipboard(user.id, 'Account ID')}
                    className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label="Copy account ID"
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </button>
                }
              />
            </div>
          </div>


        </section>

        <section className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            icon={<Gauge className="h-3.5 w-3.5" />}
            label="Tokens · 30 days"
            value={formatNumber(totals?.total_tokens)}
            loading={usageLoading}
          />
          <StatCard
            icon={<Workflow className="h-3.5 w-3.5" />}
            label="Requests · 30 days"
            value={formatNumber(totals?.requests)}
            loading={usageLoading}
          />
          <StatCard
            icon={<Cpu className="h-3.5 w-3.5" />}
            label="Estimated spend"
            value={formatCost(totals?.estimated_cost_usd)}
            hint="Last 30 days"
            loading={usageLoading}
          />
          <StatCard
            icon={<KeyRound className="h-3.5 w-3.5" />}
            label="Saved API keys"
            value={String(tokens.length)}
            hint={tokens.length === 1 ? '1 provider' : `${tokens.length} providers`}
            loading={tokensLoading}
          />
        </section>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <AccountCard
              username={user.username}
              name={user.name}
              email={user.email}
              role={user.role}
              onEdit={() => setEditOpen(true)}
            />

            <TokensCard
              tokens={tokens}
              loading={tokensLoading}
              error={tokensError}
              onRetry={() => void loadTokens()}
              onAdd={() => setAddTokenOpen(true)}
              onDelete={setTokenToDelete}
            />
          </div>

          <div className="space-y-6">
            <SecurityCard
              lastLoginAt={user.lastLoginAt}
              onChangePassword={() => setPasswordOpen(true)}
              onSignOut={() => void handleSignOut()}
              signingOut={signingOut}
            />
            <AppearanceCard theme={theme} setTheme={setTheme} />
            <ShortcutsCard />
          </div>
        </div>
      </div>

      <EditProfileDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        defaultName={user.name}
        defaultEmail={user.email}
        onSubmit={async (values) => {
          await updateProfile(values);
          toast.success('Profile updated');
        }}
      />

      <ChangePasswordDialog
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        onSubmit={async (values) => {
          await changePassword(values);
          toast.success('Password changed', {
            description: 'Every other session was signed out.',
          });
        }}
      />

      <AddTokenDialog
        open={addTokenOpen}
        onOpenChange={setAddTokenOpen}
        onSubmit={async (payload) => {
          const data = await apiFetch<TokenCreateResponse>('/token', {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          setTokens((current) => [data.token, ...current]);
          toast.success('API token saved', {
            description: `${providerLabel(data.token.provider)} is ready to use.`,
          });
        }}
      />

      <AlertDialog
        open={tokenToDelete !== null}
        onOpenChange={(open) => {
          if (!open) setTokenToDelete(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this API token?</AlertDialogTitle>
            <AlertDialogDescription>
              {tokenToDelete
                ? `${providerLabel(tokenToDelete.provider)}${
                    tokenToDelete.label ? ` · ${tokenToDelete.label}` : ''
                  } will stop working immediately. This cannot be undone.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingToken}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                void handleDeleteToken();
              }}
              disabled={deletingToken}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deletingToken ? <Loader2 className="animate-spin" /> : <Trash2 />}
              Delete token
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}

/* ————————————————————————————— Sections ————————————————————————————— */

function AccountCard({
  username,
  name,
  email,
  role,
  onEdit,
}: {
  username: string;
  name: string;
  email: string;
  role: string;
  onEdit: () => void;
}): JSX.Element {
  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <SectionHeader
        icon={<UserRound className="h-4 w-4 text-primary" />}
        title="Account details"
        description="The information shown across your SQRL workspace."
        action={
          <Button variant="outline" size="sm" onClick={onEdit}>
            <Pencil />
            Edit
          </Button>
        }
      />
      <dl className="divide-y divide-border">
        <DetailRow
          label="Username"
          value={`@${username}`}
          hint="Usernames are permanent and cannot be changed."
          trailing={
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <Lock className="h-3 w-3" />
              Locked
            </span>
          }
        />
        <DetailRow label="Display name" value={name} />
        <DetailRow label="Email" value={email} />
        <DetailRow label="Role" value={role} />
      </dl>
    </section>
  );
}

function TokensCard({
  tokens,
  loading,
  error,
  onRetry,
  onAdd,
  onDelete,
}: {
  tokens: TokenRead[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onAdd: () => void;
  onDelete: (token: TokenRead) => void;
}): JSX.Element {
  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <SectionHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title="Provider API keys"
        description="Saved keys are encrypted at rest and masked after upload."
        action={
          <Button size="sm" onClick={onAdd}>
            <Plus />
            Add key
          </Button>
        }
      />

      {error && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-destructive/5 px-5 py-3 text-sm text-destructive">
          <span>{error}</span>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}

      {loading ? (
        <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading keys…
        </div>
      ) : tokens.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-full bg-secondary">
            <KeyRound className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <p className="text-sm font-medium text-foreground">No API keys saved yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Add a provider key to reuse it without re-entering it on every request.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={onAdd}>
            <Plus />
            Add your first key
          </Button>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {tokens.map((token) => (
            <li
              key={token.id}
              className="flex flex-wrap items-center gap-3 px-5 py-3 transition-colors hover:bg-secondary/30"
            >
              <span
                aria-hidden="true"
                className={cn(
                  'h-2.5 w-2.5 shrink-0 rounded-full',
                  PROVIDER_DOT[token.provider] ?? 'bg-primary',
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {providerLabel(token.provider)}
                  </span>
                  {token.label && (
                    <Badge variant="outline" className="font-normal">
                      {token.label}
                    </Badge>
                  )}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="font-mono">{token.token_preview}</span>
                  <span>Added {formatDate(token.created_at)}</span>
                  <span>
                    {token.last_used_at
                      ? `Last used ${formatDateTime(token.last_used_at)}`
                      : 'Never used'}
                  </span>
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={() => onDelete(token)}
                aria-label={`Delete ${providerLabel(token.provider)} token`}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 border-t border-border px-5 py-3 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5" />
        Keys are encrypted with the deployment's token key and never shown in full.
      </div>
    </section>
  );
}

function SecurityCard({
  lastLoginAt,
  onChangePassword,
  onSignOut,
  signingOut,
}: {
  lastLoginAt: string;
  onChangePassword: () => void;
  onSignOut: () => void;
  signingOut: boolean;
}): JSX.Element {
  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <SectionHeader
        icon={<ShieldCheck className="h-4 w-4 text-primary" />}
        title="Security"
        description="Password and active session."
      />
      <div className="space-y-4 px-5 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-foreground">Password</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Rotating it signs out every other session.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={onChangePassword}>
            <Lock />
            Change
          </Button>
        </div>

        <div className="rounded-lg border border-border bg-secondary/40 p-3">
          <div className="flex items-center gap-2 text-xs font-medium text-foreground">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            Active session · this device
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Signed in {formatDateTime(lastLoginAt)}
          </p>
        </div>

        <Button
          variant="outline"
          className="w-full justify-center text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
          onClick={onSignOut}
          disabled={signingOut}
        >
          {signingOut ? <Loader2 className="animate-spin" /> : <LogOut />}
          Sign out of this device
        </Button>
      </div>
    </section>
  );
}

function AppearanceCard({
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
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <SectionHeader
        icon={<Palette className="h-4 w-4 text-primary" />}
        title="Appearance"
        description="Choose how SQRL looks on this device."
      />
      <div className="grid grid-cols-2 gap-3 px-5 pb-5">
        {themes.map(({ id, label, icon: Icon, swatch }) => {
          const active = theme === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => setTheme(id)}
              aria-pressed={active}
              className={cn(
                'overflow-hidden rounded-lg border text-left transition-colors',
                active ? 'border-primary ring-1 ring-primary/30' : 'border-border hover:border-primary/40',
              )}
            >
              <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="flex-1 text-xs font-medium text-foreground">{label}</span>
                {active && <Check className="h-3.5 w-3.5 text-primary" strokeWidth={3} />}
              </div>
              <div className="flex h-12 items-center gap-2 p-2.5" style={{ backgroundColor: swatch[0] }}>
                <span
                  className="h-6 w-6 rounded border border-black/10"
                  style={{ backgroundColor: swatch[1] }}
                />
                <span className="flex-1 space-y-1">
                  <span
                    className="block h-1.5 w-3/4 rounded-full"
                    style={{ backgroundColor: swatch[2] }}
                  />
                  <span
                    className="block h-1.5 w-1/2 rounded-full opacity-40"
                    style={{ backgroundColor: swatch[1] }}
                  />
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function ShortcutsCard(): JSX.Element {
  const navigate = useNavigate();
  const links = [
    { label: 'Usage and spend', description: 'Token breakdown by provider', icon: Gauge, to: '/usage' },
    { label: 'Pipelines', description: 'Scheduled ETL runs and outputs', icon: Workflow, to: '/pipelines' },
    { label: 'Models', description: 'Trained models and model cards', icon: Cpu, to: '/models' },
  ];

  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <SectionHeader
        icon={<Gauge className="h-4 w-4 text-primary" />}
        title="Shortcuts"
        description="Jump to the parts of SQRL you use most."
      />
      <div className="space-y-1 px-3 pb-3">
        {links.map(({ label, description, icon: Icon, to }) => (
          <button
            key={to}
            type="button"
            onClick={() => navigate(to)}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-secondary/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary">
              <Icon className="h-4 w-4 text-muted-foreground" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-foreground">{label}</span>
              <span className="block truncate text-xs text-muted-foreground">{description}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

/* ————————————————————————————— Dialogs ————————————————————————————— */

function EditProfileDialog({
  open,
  onOpenChange,
  defaultName,
  defaultEmail,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultName: string;
  defaultEmail: string;
  onSubmit: (values: { name: string; email: string }) => Promise<void>;
}): JSX.Element {
  const [name, setName] = useState(defaultName);
  const [email, setEmail] = useState(defaultEmail);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(defaultName);
      setEmail(defaultEmail);
      setError(null);
    }
  }, [open, defaultName, defaultEmail]);

  const dirty = name.trim() !== defaultName || email.trim().toLowerCase() !== defaultEmail.toLowerCase();
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!emailValid) {
      setError('Enter a valid email address.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSubmit({ name: name.trim(), email: email.trim() });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update profile.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit profile</DialogTitle>
          <DialogDescription>
            Update how your name and email appear across SQRL.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="profile-name">Display name</Label>
            <Input
              id="profile-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Your name"
              maxLength={120}
              autoComplete="name"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="profile-email">Email</Label>
            <Input
              id="profile-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
            />
            <p className="text-xs text-muted-foreground">
              Used for sign-in and account notifications.
            </p>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving || !dirty || !emailValid}>
              {saving ? <Loader2 className="animate-spin" /> : <Check />}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ChangePasswordDialog({
  open,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (values: { currentPassword: string; newPassword: string }) => Promise<void>;
}): JSX.Element {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPasswords, setShowPasswords] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setShowPasswords(false);
      setError(null);
    }
  }, [open]);

  const strength = passwordStrength(newPassword);
  const mismatch = confirmPassword.length > 0 && confirmPassword !== newPassword;
  const canSubmit =
    currentPassword.length >= 8 &&
    newPassword.length >= 8 &&
    confirmPassword === newPassword &&
    newPassword !== currentPassword;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setError('The new passwords do not match.');
      return;
    }
    if (newPassword === currentPassword) {
      setError('Choose a password that differs from the current one.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSubmit({ currentPassword, newPassword });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change password.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>
            Your other sessions are signed out as soon as the password changes.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <PasswordField
            id="current-password"
            label="Current password"
            value={currentPassword}
            onChange={setCurrentPassword}
            show={showPasswords}
            autoComplete="current-password"
          />
          <PasswordField
            id="new-password"
            label="New password"
            value={newPassword}
            onChange={setNewPassword}
            show={showPasswords}
            autoComplete="new-password"
          />
          {newPassword.length > 0 && (
            <div>
              <div className="flex gap-1">
                {[0, 1, 2, 3].map((index) => (
                  <span
                    key={index}
                    className={cn(
                      'h-1 flex-1 rounded-full',
                      index < strength.score
                        ? strength.score <= 2
                          ? 'bg-amber-500'
                          : 'bg-emerald-500'
                        : 'bg-border',
                    )}
                  />
                ))}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{strength.label}</p>
            </div>
          )}
          <PasswordField
            id="confirm-password"
            label="Confirm new password"
            value={confirmPassword}
            onChange={setConfirmPassword}
            show={showPasswords}
            autoComplete="new-password"
            error={mismatch ? 'Passwords do not match.' : undefined}
          />
          <button
            type="button"
            onClick={() => setShowPasswords((value) => !value)}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            {showPasswords ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            {showPasswords ? 'Hide passwords' : 'Show passwords'}
          </button>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving || !canSubmit}>
              {saving ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
              Update password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AddTokenDialog({
  open,
  onOpenChange,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: { provider: string; label: string | null; token: string }) => Promise<void>;
}): JSX.Element {
  const [provider, setProvider] = useState<string>(PROVIDERS[0].value);
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setProvider(PROVIDERS[0].value);
      setLabel('');
      setValue('');
      setShowToken(false);
      setError(null);
    }
  }, [open]);

  const selected = useMemo(
    () => PROVIDERS.find((option) => option.value === provider) ?? PROVIDERS[0],
    [provider],
  );

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (value.trim().length < 8) {
      setError('API keys must be at least 8 characters.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSubmit({
        provider,
        label: label.trim() || null,
        token: value.trim(),
      });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save token.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add provider API key</DialogTitle>
          <DialogDescription>
            Saved keys let SQRL call your provider without asking for the key each time.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="token-provider">Provider</Label>
            <Select value={provider} onValueChange={setProvider}>
              <SelectTrigger id="token-provider">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDERS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{selected.hint}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="token-label">Label</Label>
            <Input
              id="token-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="e.g. personal key"
              maxLength={64}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="token-value">API key</Label>
            <div className="relative">
              <Input
                id="token-value"
                type={showToken ? 'text' : 'password'}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder="Paste the key"
                autoComplete="off"
                className="pr-10 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowToken((current) => !current)}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
                aria-label={showToken ? 'Hide API key' : 'Show API key'}
              >
                {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary/40 p-3 text-xs text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <span>Keys are encrypted before storage and only the masked preview is returned.</span>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving || value.trim().length < 8}>
              {saving ? <Loader2 className="animate-spin" /> : <Plus />}
              Save key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ————————————————————————————— Primitives ————————————————————————————— */

function SectionHeader({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary">
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      {action}
    </div>
  );
}

function DetailRow({
  label,
  value,
  hint,
  trailing,
}: {
  label: string;
  value: string;
  hint?: string;
  trailing?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
      <div className="min-w-0">
        <dt className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          {label}
        </dt>
        <dd className="mt-0.5 truncate text-sm text-foreground">{value}</dd>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      {trailing}
    </div>
  );
}

function MetaItem({
  icon: Icon,
  label,
  value,
  mono,
  action,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  mono?: boolean;
  action?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1 flex items-center gap-1">
        <span className={cn('truncate text-sm text-foreground', mono && 'font-mono text-xs')}>
          {value}
        </span>
        {action}
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  loading?: boolean;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-secondary">{icon}</span>
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums text-foreground">
        {loading ? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /> : value}
      </div>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  show,
  autoComplete,
  error,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  autoComplete: string;
  error?: string;
}): JSX.Element {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={show ? 'text' : 'password'}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        aria-invalid={error ? true : undefined}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

/* ————————————————————————————— Helpers ————————————————————————————— */

function passwordStrength(password: string): { score: number; label: string } {
  if (!password) return { score: 0, label: 'Use at least 8 characters.' };
  let score = 0;
  if (password.length >= 8) score += 1;
  if (password.length >= 12) score += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
  if (/\d/.test(password) && /[^A-Za-z0-9]/.test(password)) score += 1;
  const labels = ['Too short', 'Weak', 'Fair', 'Good', 'Strong'];
  return { score, label: labels[score] ?? 'Strong' };
}

async function copyToClipboard(value: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copied`);
  } catch {
    toast.error('Could not copy to the clipboard.');
  }
}

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
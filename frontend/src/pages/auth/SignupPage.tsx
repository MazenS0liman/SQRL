import React, { useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Mail, Lock, Sparkles, User, UserPlus, Eye, EyeOff, Upload, Cpu, Download } from 'lucide-react';

// Contexts
import { useAuth } from '@/contexts/AuthContext';


export default function SignupPage(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, signup } = useAuth();

  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shaking, setShaking] = useState(false);

  const heroRef = useRef<HTMLDivElement>(null);

  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/';

  if (user) {
    return <Navigate to={from} replace />;
  }

  const handleHeroMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = heroRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width - 0.5;
    const py = (e.clientY - rect.top) / rect.height - 0.5;
    el.style.setProperty('--mx', px.toFixed(3));
    el.style.setProperty('--my', py.toFixed(3));
  };

  const handleHeroMouseLeave = () => {
    heroRef.current?.style.setProperty('--mx', '0');
    heroRef.current?.style.setProperty('--my', '0');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await signup({ username, password, name, email });
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create account.');
      setShaking(true);
      window.setTimeout(() => setShaking(false), 500);
    }
  };

  const chips = [
    { icon: Upload, title: 'Gather', description: 'Pull in CSVs and connectors as input sources.' },
    { icon: Cpu, title: 'Build', description: 'Clean, transform, and compare models automatically.' },
    { icon: Download, title: 'Ship', description: 'Download the winning model in one click and use it for predictions.' },
  ];

  return (
    <div className="relative h-full w-full overflow-y-auto text-foreground">
      <style>{`
        @keyframes sqrl-float {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-9px); }
        }
        @keyframes sqrl-logo-in {
          0% { opacity: 0; transform: scale(0.6) rotate(-10deg); }
          60% { opacity: 1; transform: scale(1.08) rotate(3deg); }
          100% { opacity: 1; transform: scale(1) rotate(0deg); }
        }
        @keyframes sqrl-ring-pulse {
          0%, 100% { transform: scale(1); opacity: 0.35; }
          50% { transform: scale(1.16); opacity: 0.1; }
        }
        @keyframes sqrl-fade-up {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes sqrl-shake {
          10%, 90% { transform: translateX(-1px); }
          20%, 80% { transform: translateX(2px); }
          30%, 50%, 70% { transform: translateX(-4px); }
          40%, 60% { transform: translateX(4px); }
        }
        .sqrl-anim-float { animation: sqrl-float 4.5s ease-in-out infinite; }
        .sqrl-anim-logo { animation: sqrl-logo-in 700ms cubic-bezier(.34,1.56,.64,1) both; }
        .sqrl-anim-ring { animation: sqrl-ring-pulse 3.2s ease-in-out infinite; }
        .sqrl-anim-fadeup { animation: sqrl-fade-up 600ms ease-out both; }
        .sqrl-anim-shake { animation: sqrl-shake 500ms; }
        @media (prefers-reduced-motion: reduce) {
          .sqrl-anim-float, .sqrl-anim-logo, .sqrl-anim-ring, .sqrl-anim-fadeup, .sqrl-anim-shake {
            animation: none !important;
          }
        }
      `}</style>

      <div className="relative flex min-h-full w-full items-center justify-center">
      <div className="relative w-full max-w-6xl overflow-hidden rounded-3xl border border-border bg-card shadow-2xl">
        <div className="grid lg:grid-cols-[1.1fr_0.9fr]">
          {/* Hero panel */}
          <div
            ref={heroRef}
            onMouseMove={handleHeroMouseMove}
            onMouseLeave={handleHeroMouseLeave}
            className="relative flex items-end overflow-hidden p-6 lg:p-8"
            style={{ ['--mx' as string]: 0, ['--my' as string]: 0 } as React.CSSProperties}
          >
            <div className="absolute inset-0 bg-gradient-to-br from-primary/15 via-transparent to-transparent" />

            <div className="relative max-w-xl h-[100%]">
              <div className="mb-6 flex items-center gap-4">
                <div className="relative flex h-16 w-16 shrink-0 items-center justify-center">
                  <img
                    src="/imgs/logo.png"
                    alt="Squirrel logo"
                    className="sqrl-anim-logo relative h-14 w-14 object-contain transition-transform duration-300 ease-out hover:-rotate-6 hover:scale-105"
                  />
                </div>
                <div className="inline-flex items-center gap-2 rounded-full border border-border bg-secondary px-3 py-1 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  <Sparkles className="h-3.5 w-3.5 text-primary" />
                  Create your account
                </div>
              </div>

              <h1
                className="sqrl-anim-fadeup font-serif text-3xl font-semibold tracking-tight lg:text-5xl"
                style={{ animationDelay: '80ms' }}
              >
                Sign up once, use it everywhere.
              </h1>
              <p
                className="sqrl-anim-fadeup mt-3 max-w-lg text-sm leading-6 text-muted-foreground lg:text-base"
                style={{ animationDelay: '160ms' }}
              >
                Accounts are stored locally in your browser.
              </p>

              <div className="mt-12 grid gap-3 sm:grid-cols-3">
                {chips.map(({ icon: Icon, title, description }, i) => (
                  <div
                    key={title}
                    className="sqrl-anim-fadeup rounded-2xl border border-border bg-background/70 p-4 backdrop-blur transition-transform duration-300 ease-out hover:-translate-y-1"
                    style={{ animationDelay: `${240 + i * 80}ms` }}
                  >
                    <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Icon className="h-4 w-4" />
                    </div>
                    <p className="text-sm font-semibold text-foreground">{title}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{description}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Form panel */}
          <div className="relative border-t border-border bg-background/80 p-6 backdrop-blur lg:border-l lg:border-t-0 lg:p-8">

            <div className={`mx-auto flex h-full max-w-md flex-col justify-center ${shaking ? 'sqrl-anim-shake' : ''}`}>
              <form onSubmit={handleSubmit} className="flex flex-col">
                <div className="mb-5">
                  <h2 className="text-2xl font-semibold tracking-tight">Sign up</h2>
                  <p className="mt-2 text-sm text-muted-foreground">Create a local account.</p>
                </div>

                <label htmlFor="signup-username" className="mb-1.5 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Username
                </label>
                <div className="relative mb-3">
                  <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    id="signup-username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="admin"
                    className="w-full rounded-xl border border-input bg-secondary py-3 pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                  />
                </div>

                <label htmlFor="signup-name" className="mb-1.5 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Display name
                </label>
                <div className="relative mb-3">
                  <UserPlus className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    id="signup-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Admin User"
                    className="w-full rounded-xl border border-input bg-secondary py-3 pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                  />
                </div>

                <label htmlFor="signup-email" className="mb-1.5 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Email
                </label>
                <div className="relative mb-3">
                  <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    id="signup-email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="admin@squirrel.local"
                    className="w-full rounded-xl border border-input bg-secondary py-3 pl-10 pr-4 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                  />
                </div>

                <label htmlFor="signup-password" className="mb-1.5 text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Password
                </label>
                <div className="relative mb-2">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <input
                    id="signup-password"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Create a password"
                    className="w-full rounded-xl border border-input bg-secondary py-3 pl-10 pr-10 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>

                {error && (
                  <p role="alert" className="mb-4 text-sm text-destructive">
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={!username.trim() || !name.trim() || !email.trim() || !password.trim()}
                  className="inline-flex items-center justify-center mt-4 gap-2 rounded-xl bg-primary-gradient px-4 py-3 text-sm font-medium text-primary-foreground shadow-sm transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  Create account
                </button>

                <Link
                  to="/login"
                  className="mt-4 text-center text-sm font-medium text-muted-foreground hover:text-foreground"
                >
                  Already have an account? Sign in
                </Link>
              </form>
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
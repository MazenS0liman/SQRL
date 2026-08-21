import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Moon, Sun, Palette, Check } from 'lucide-react';
import { useTheme } from '@/contexts/ThemeContext';

// Dotted background — same component used on the Notebooks / Workspace
// pages, so Settings reads as part of the same app rather than a
// separately-designed screen.
import DotField from '@/components/background/DotField';

export default function SettingsPage() {
  const navigate = useNavigate();
  const { theme, setTheme } = useTheme();

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-background text-foreground">
      <div className="relative h-full min-h-0 overflow-y-auto bg-background text-foreground">
        {/* Content column below is un-capped (min-h-full, not h-full) so this
            stretches across the full scrollable content, not just the first
            screen — same treatment as Notebooks / Workspace. */}
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
            gradientFrom="#592603"
            gradientTo="#613508"
            glowColor="#120F17"
          />
        </div>

        <div className="relative min-h-full w-full mx-auto max-w-4xl px-6 py-10">
          {/* Header */}
          <header className="relative mb-8 flex items-center gap-3 border-b border-border pb-6">
            <div>
              <h1 className="bg-primary-gradient bg-clip-text text-[28px] font-semibold leading-none tracking-tight text-transparent">
                Settings
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Manage how Squirrel looks and behaves on this device.
              </p>
            </div>
          </header>

          {/* Content */}
          <div className="relative space-y-12">
            {/* Appearance */}
            <section>
              <div className="mb-1 flex items-center gap-2">
                <Palette className="h-4 w-4 text-primary" />
                <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                  Appearance
                </h2>
              </div>
              <p className="mb-6 text-sm text-muted-foreground">Choose how Squirrel looks on this device.</p>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {(
                  [
                    { id: 'light' as const, label: 'Light', icon: Sun, swatch: ['#F5F1EA', '#FFFFFF', '#E8895C'] },
                    { id: 'dark' as const, label: 'Dark', icon: Moon, swatch: ['#17110D', '#0B0908', '#E8895C'] },
                  ]
                ).map(({ id, label, icon: Icon, swatch }) => {
                  const active = theme === id;
                  return (
                    <button
                      key={id}
                      onClick={() => setTheme(id)}
                      aria-pressed={active}
                      className={`group relative overflow-hidden rounded-2xl border text-left transition-all duration-300 ease-out hover:-translate-y-0.5 ${
                        active ? 'border-primary ring-1 ring-primary/40 shadow-sm' : 'border-border hover:border-primary/40'
                      }`}
                    >
                      {/* Title bar — matches the WorkspaceCard / BestModelCard treatment */}
                      <div className="flex items-center gap-2 border-b border-border/70 bg-secondary/40 px-4 py-2.5">
                        <span
                          className={`relative inline-flex h-2 w-2 rounded-full ${
                            active ? 'bg-primary-gradient' : 'bg-muted-foreground/40'
                          }`}
                        />
                        <span className="flex-1 truncate text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                          {label} theme
                        </span>
                        {active && <Check className="h-3.5 w-3.5 text-primary" strokeWidth={3} />}
                      </div>

                      {/* Mini live preview */}
                      <div className="flex h-24 w-full items-center gap-2 p-4" style={{ backgroundColor: swatch[0] }}>
                        <span className="h-8 w-8 rounded-md shadow-sm" style={{ backgroundColor: swatch[1] }} />
                        <span className="flex-1 space-y-1.5">
                          <span className="block h-2 w-3/4 rounded-full opacity-90" style={{ backgroundColor: swatch[2] }} />
                          <span className="block h-2 w-1/2 rounded-full opacity-40" style={{ backgroundColor: swatch[1] }} />
                        </span>
                      </div>

                      <div className="flex items-center justify-between bg-card px-4 py-3">
                        <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                          <Icon className="h-4 w-4" />
                          {label}
                        </span>
                      </div>

                      {/* Footer accent hairline — fills in when active, same as
                          the gradient hover treatment used elsewhere */}
                      <div className="h-[3px] w-full bg-border/60">
                        <div
                          className={`h-full bg-primary-gradient transition-all duration-300 ease-out ${
                            active ? 'w-full' : 'w-0 group-hover:w-full'
                          }`}
                        />
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>

            {/* Preview */}
            <section>
              <h2 className="mb-1 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Preview
              </h2>
              <p className="mb-6 text-sm text-muted-foreground">How the current theme's tokens look in practice.</p>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="overflow-hidden rounded-2xl border border-border bg-card">
                  <div className="border-b border-border/70 bg-secondary/40 px-4 py-2.5">
                    <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Colors
                    </h3>
                  </div>
                  <div className="space-y-3 p-5">
                    {[
                      { swatch: 'bg-primary', label: 'Primary' },
                      { swatch: 'bg-secondary', label: 'Secondary' },
                      { swatch: 'bg-muted', label: 'Muted' },
                    ].map(({ swatch, label }) => (
                      <div key={label} className="flex items-center gap-3">
                        <div className={`h-10 w-10 rounded-lg border border-border/40 ${swatch}`} />
                        <span className="text-sm text-foreground">{label}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="overflow-hidden rounded-2xl border border-border bg-card">
                  <div className="border-b border-border/70 bg-secondary/40 px-4 py-2.5">
                    <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                      Typography
                    </h3>
                  </div>
                  <div className="space-y-3 p-5">
                    <div>
                      <p className="mb-1 text-xs uppercase tracking-widest text-muted-foreground">Small label</p>
                      <p className="text-sm text-foreground">Regular body text</p>
                    </div>
                    <div>
                      <p className="text-lg font-semibold text-foreground">Heading large</p>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            {/* About */}
            <section className="overflow-hidden rounded-2xl border border-border bg-card">
              <div className="border-b border-border/70 bg-secondary/40 px-4 py-2.5">
                <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">About</h2>
              </div>
              <div className="space-y-1 px-4 py-4 text-sm text-muted-foreground">
                <p>
                  <span className="font-medium text-foreground">Squirrel</span> v1.0.0
                </p>
                <p>© 2026 All rights reserved.</p>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
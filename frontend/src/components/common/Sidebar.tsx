import React from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import {
  Home,
  Library,
  Cpu,
  Database,
  NotebookText,
  Files,
  CircleUserRound,
  LogOut,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';

interface SidebarProps {
  activeTab: 'home' | 'workspace' | 'notebooks' | 'files' | 'models' | 'data connectors';
  onTabChange: (tab: 'home' | 'workspace' | 'notebooks' | 'files' | 'models' | 'data connectors') => void;
}

// Nav item height (h-10) + the gap `space-y-1` inserts between siblings —
// used to compute the sliding active-indicator's offset without needing to
// measure the DOM. Keep these in sync if either class changes below.
const ITEM_HEIGHT = 40;
const ITEM_GAP = 4;

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, onTabChange }) => {
  const [isHovered, setIsHovered] = React.useState(false);
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  // Sidebar is always collapsed unless the user is actively hovering it —
  // there's no pinned/toggled state anymore.
  const expanded = isHovered;

  const navItems = [
    { id: 'home' as const, icon: Home, label: 'Home' },
    { id: 'workspace' as const, icon: Library, label: 'Workspace' },
    { id: 'notebooks' as const, icon: NotebookText, label: 'Notebooks' },
    { id: 'files' as const, icon: Files, label: 'Files' },
    { id: 'models' as const, icon: Cpu, label: 'Models' },
    { id: 'data connectors' as const, icon: Database, label: 'Data Connectors' },
  ];

  const activeIndex = navItems.findIndex((item) => item.id === activeTab);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      className={cn(
        'sticky top-0 flex h-screen flex-col bg-sidebar border-r border-sidebar-border transition-[width] duration-300 ease-out',
        expanded ? 'w-56 shadow-xl' : 'w-16'
      )}
    >
      {/* Header — logo is now purely decorative/branding, no toggle behavior */}
      <div className="flex h-[80px] items-center gap-2 border-b border-sidebar-border px-3">
        <div className="flex h-full w-full shrink-0 items-center justify-center rounded-lg">
          <img
            src="/imgs/logo.png"
            alt="Squirrel logo"
            className="h-full w-full object-contain"
          />
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2">
        <ul className="relative space-y-1">
          {/* Sliding active-indicator — one element that glides between items
              instead of each button toggling its own background, so switching
              tabs reads as a single continuous motion rather than a flip. */}
          <span
            aria-hidden="true"
            className="absolute left-0 right-0 rounded-lg bg-primary-gradient shadow-sm transition-all duration-300 ease-out"
            style={{
              top: (activeIndex >= 0 ? activeIndex : 0) * (ITEM_HEIGHT + ITEM_GAP),
              height: ITEM_HEIGHT,
              opacity: activeIndex >= 0 ? 1 : 0,
            }}
          />

          {navItems.map((item) => {
            const isActive = activeTab === item.id;
            return (
              <li key={item.id}>
                <button
                  onClick={() => onTabChange(item.id)}
                  aria-current={isActive ? 'page' : undefined}
                  title={!expanded ? item.label : undefined}
                  className={cn(
                    'group relative z-10 flex h-10 w-full items-center gap-3 rounded-lg px-3 transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    isActive
                      ? 'text-primary-foreground'
                      : 'text-sidebar-foreground hover:bg-primary/10 hover:text-sidebar-foreground'
                  )}
                >
                  <item.icon
                    className={cn(
                      'h-5 w-5 shrink-0 transition-transform duration-200',
                      !isActive && 'group-hover:translate-x-0.5'
                    )}
                  />
                  <span
                    className={cn(
                      'overflow-hidden whitespace-nowrap text-sm font-medium transition-all duration-300',
                      expanded ? 'max-w-[9rem] opacity-100' : 'max-w-0 opacity-0'
                    )}
                  >
                    {item.label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Profile / sign-out */}
      <div className="mt-auto border-t border-sidebar-border p-2">
        <button
          type="button"
          onClick={() => navigate('/profile')}
          className="group flex w-full items-center gap-3 rounded-xl border border-transparent p-1 text-left transition-colors duration-200 hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="relative shrink-0">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary-gradient text-sm font-semibold text-primary-foreground shadow-sm transition-transform duration-200 group-hover:scale-105">
              {user?.avatarSeed ?? 'SQ'}
            </div>
            {user && (
              <span
                aria-hidden="true"
                className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar bg-emerald-500"
              />
            )}
          </div>

          <div
            className={cn(
              'min-w-0 flex-1 overflow-hidden transition-all duration-300',
              expanded ? 'max-w-[9rem] opacity-100' : 'max-w-0 opacity-0'
            )}
          >
            <div className="truncate text-sm font-semibold text-sidebar-foreground">
              {user?.username ?? 'Guest user'}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {user?.name ?? 'Sign in to personalize your session'}
            </div>
          </div>

          {expanded && <CircleUserRound className="h-4 w-4 shrink-0 text-muted-foreground" />}
        </button>

        {expanded && user && (
          <button
            type="button"
            onClick={logout}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl border border-sidebar-border px-3 py-2 text-xs font-medium text-sidebar-foreground transition-colors duration-200 hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <LogOut className="h-3.5 w-3.5" />
            Log out
          </button>
        )}
      </div>
    </div>
  );
};
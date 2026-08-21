import React, { useEffect, useRef, useState } from 'react';
import { useTheme } from '@/contexts/ThemeContext';

export interface FolderCardMenuItem {
  label: string;
  onClick: () => void;
  destructive?: boolean;
}

interface FolderCardProps {
  title: string;
  /** Small colored dot in the header, e.g. status.tabClass ('bg-emerald-400') */
  statusDotClass?: string;
  /** Text shown at the bottom-left, e.g. "Last Update: Jan 3, 2025" or "No target column yet" */
  footerText?: React.ReactNode;
  onOpen: () => void;
  menuItems?: FolderCardMenuItem[];
  busy?: boolean;
  busyLabel?: string;
  className?: string;
}

/**
 * Folder-shaped card used across Notebooks, Workspace, and the Home page's
 * recent-notebooks rail. Renders a manila-folder SVG behind clickable
 * content, with an optional kebab menu in the bottom-right corner.
 */
export function FolderCard({
  title,
  statusDotClass = 'bg-stone-500',
  footerText,
  onOpen,
  menuItems,
  busy = false,
  busyLabel = 'Working…',
  className = '',
}: FolderCardProps): JSX.Element {
  const { theme } = useTheme();
  const isDark = theme === 'dark';

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  return (
    <div
      className={`group relative h-[200px] w-[270px] transform-gpu transition-all duration-300 ease-out hover:-translate-y-2 hover:scale-[1.04] ${
        busy ? 'pointer-events-none opacity-60' : ''
      } ${className}`}
      style={{
        filter: isDark
          ? 'drop-shadow(0 2px 6px rgba(0,0,0,0.18))'
          : 'drop-shadow(0 2px 6px rgba(235, 122, 10, 0.12))',
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.filter = isDark
          ? 'drop-shadow(0 2px 6px rgba(0,0,0,0.18))'
          : 'drop-shadow(0 2px 6px rgba(90,54,18,0.12))';
      }}
    >
      <FolderArt dark={isDark} />

      <button onClick={onOpen} className="absolute inset-0 flex flex-col px-6 pb-5 pt-11 text-left">
        <div className="mt-2 flex items-center gap-3">
          <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-2xl ${statusDotClass}`}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <rect x="4" y="4" width="16" height="16" rx="3" fill="white" />
            </svg>
          </div>
          <h2 className="flex-1 truncate text-sm font-medium leading-tight text-foreground">{title}</h2>
        </div>

        <div className="mt-auto mb-2 flex items-center justify-between pr-8">
          <span className="text-xs text-muted-foreground">
            {busy ? busyLabel : footerText}
          </span>
        </div>
      </button>

      {menuItems && menuItems.length > 0 && (
        <div ref={menuRef} className="absolute bottom-5 right-5 z-20">
          <button
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            className="flex h-7 w-7 items-center justify-center rounded-md text-foreground/70 hover:bg-secondary hover:text-foreground"
          >
            <svg width="18" height="18" viewBox="0 0 24 24">
              <circle cx="5" cy="12" r="1.5" fill="currentColor" />
              <circle cx="12" cy="12" r="1.5" fill="currentColor" />
              <circle cx="19" cy="12" r="1.5" fill="currentColor" />
            </svg>
          </button>

          {menuOpen && (
            <div className="absolute bottom-8 right-0 w-40 overflow-hidden rounded-xl border border-border bg-card shadow-xl">
              {menuItems.map((item) => (
                <button
                  key={item.label}
                  onClick={() => {
                    setMenuOpen(false);
                    item.onClick();
                  }}
                  className={`block w-full px-4 py-2 text-left text-sm hover:bg-secondary ${
                    item.destructive ? 'text-destructive' : 'text-foreground'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function FolderArt({ dark }: { dark: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 247 162"
      fill="none"
      aria-hidden
      className={`absolute inset-0 h-full w-full transition-all duration-300 ease-out ${
        dark ? 'text-orange-500/30' : 'text-orange-600/30'
      }`}
      style={{
        filter: dark
          ? 'drop-shadow(0 2px 4px rgba(0,0,0,0.18))'
          : 'drop-shadow(0 2px 4px rgba(74, 43, 12, 0.14))',
      }}
    >
      <path
        d="M227 23.5C231.728 23.5 235.224 23.5007 237.906 23.8613C240.574 24.22 242.362 24.9262 243.718 26.2822C245.074 27.6383 245.78 29.4261 246.139 32.0938C246.499 34.7758 246.5 38.2718 246.5 43V142C246.5 146.728 246.499 150.224 246.139 152.906C245.78 155.574 245.074 157.362 243.718 158.718C242.362 160.074 240.574 160.78 237.906 161.139C235.224 161.499 231.728 161.5 227 161.5H20C15.2718 161.5 11.7758 161.499 9.09375 161.139C6.42612 160.78 4.63829 160.074 3.28223 158.718C1.92616 157.362 1.21999 155.574 0.861328 152.906C0.500732 150.224 0.5 146.728 0.5 142V23.5H227Z"
        fill="currentColor"
      />
      <path
        d="M227 23.5C231.728 23.5 235.224 23.5007 237.906 23.8613C240.574 24.22 242.362 24.9262 243.718 26.2822C245.074 27.6383 245.78 29.4261 246.139 32.0938C246.499 34.7758 246.5 38.2718 246.5 43V142C246.5 146.728 246.499 150.224 246.139 152.906C245.78 155.574 245.074 157.362 243.718 158.718C242.362 160.074 240.574 160.78 237.906 161.139C235.224 161.499 231.728 161.5 227 161.5H20C15.2718 161.5 11.7758 161.499 9.09375 161.139C6.42612 160.78 4.63829 160.074 3.28223 158.718C1.92616 157.362 1.21999 155.574 0.861328 152.906C0.500732 150.224 0.5 146.728 0.5 142V23.5H227Z"
        stroke="currentColor"
      />
      <path
        d="M20 0.5H80.5C86.4796 0.5 89.3578 0.506311 92.0342 1.39844C94.7104 2.29057 97.0165 4.01213 101.8 7.59961L123 23.5H0.5V20C0.5 15.2718 0.500732 11.7758 0.861328 9.09375C1.21999 6.42612 1.92616 4.63829 3.28223 3.28223C4.63829 1.92616 6.42612 1.21999 9.09375 0.861328C11.7758 0.500732 15.2718 0.5 20 0.5Z"
        fill="currentColor"
        stroke="currentColor"
      />
      <path d="M1 23H121.506L122.797 24H1V23Z" fill="currentColor" />
    </svg>
  );
}
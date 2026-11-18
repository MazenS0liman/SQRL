import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell,
  BellOff,
  Boxes,
  CheckCheck,
  CheckCircle2,
  Cpu,
  Loader2,
  NotebookText,
  RefreshCw,
  Radio,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { notificationsApi } from "@/api/notificationsApi";
import type { NotificationItem } from "@/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

// ——————————————————————————————————————————————————————————————
// Helpers

interface NotificationVisual {
  Icon: LucideIcon;
  tone: string;
}

function visualFor(type: string): NotificationVisual {
  switch (type) {
    case "pipeline_run":
      return { Icon: Workflow, tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" };
    case "workspace_build":
      return { Icon: Boxes, tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" };
    case "notebook_run":
      return { Icon: NotebookText, tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" };
    case "job_succeeded":
      return { Icon: CheckCircle2, tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" };
    case "job_failed":
      return { Icon: TriangleAlert, tone: "bg-destructive/10 text-destructive" };
    case "retrain":
      return { Icon: Cpu, tone: "bg-primary/10 text-primary" };
    case "stream":
      return { Icon: Radio, tone: "bg-primary/10 text-primary" };
    default:
      return { Icon: Bell, tone: "bg-muted text-muted-foreground" };
  }
}

function relativeTime(iso?: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function isUnread(item: NotificationItem): boolean {
  return !item.read_at;
}

// ——————————————————————————————————————————————————————————————
// Component

export function NotificationPanel() {
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [markingAll, setMarkingAll] = useState(false);

  // Guards against a slow response overwriting a newer one.
  const requestId = useRef(0);

  const loadList = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const data = await notificationsApi.list({ limit: 50 });
      if (id !== requestId.current) return;
      setItems(data.items);
      setUnreadCount(data.unread_count);
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof Error ? err.message : "Could not load notifications.");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  const loadUnread = useCallback(async () => {
    try {
      const { count } = await notificationsApi.unreadCount();
      setUnreadCount(count);
    } catch {
      // Unread polling is best-effort; the panel surfaces real errors.
    }
  }, []);

  // Poll the badge count while the app is open and refresh on focus.
  useEffect(() => {
    void loadUnread();
    const interval = window.setInterval(() => void loadUnread(), 30_000);
    const onFocus = () => void loadUnread();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void loadUnread();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loadUnread]);

  // Refresh the full list whenever the panel is opened.
  useEffect(() => {
    if (open) void loadList();
  }, [open, loadList]);

  const handleItemClick = async (item: NotificationItem) => {
    if (isUnread(item)) {
      const now = new Date().toISOString();
      setItems((prev) =>
        prev.map((row) => (row.id === item.id ? { ...row, read_at: now } : row)),
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
      try {
        await notificationsApi.markRead(item.id);
      } catch {
        void loadUnread();
      }
    }

    if (item.link) {
      setOpen(false);
      navigate(item.link);
    }
  };

  const handleMarkAll = async () => {
    if (markingAll || unreadCount === 0) return;
    setMarkingAll(true);
    try {
      await notificationsApi.markAllRead();
      const now = new Date().toISOString();
      setItems((prev) =>
        prev.map((row) => (row.read_at ? row : { ...row, read_at: now })),
      );
      setUnreadCount(0);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not mark notifications read.");
      void loadUnread();
    } finally {
      setMarkingAll(false);
    }
  };

  const badgeLabel = unreadCount > 9 ? "9+" : String(unreadCount);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={() => setOpen(true)}
        className="relative h-10 w-10 rounded-lg text-foreground/60 hover:bg-foreground/5 hover:text-foreground"
        aria-label={
          unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"
        }
      >
        <Bell className="h-5 w-5" />
        {unreadCount > 0 && (
          <span className="absolute right-1.5 top-1.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold leading-none text-primary-foreground">
            {badgeLabel}
          </span>
        )}
      </Button>

      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-md"
      >
        <SheetHeader className="space-y-1 border-b border-border px-5 py-4 pr-12">
          <div className="flex items-center justify-between gap-3">
            <SheetTitle className="flex items-center gap-2 text-base">
              Notifications
              {unreadCount > 0 && (
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                  {unreadCount} new
                </span>
              )}
            </SheetTitle>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleMarkAll}
              disabled={markingAll || unreadCount === 0}
              className="h-8 gap-1.5 px-2 text-xs"
            >
              {markingAll ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <CheckCheck className="h-3.5 w-3.5" />
              )}
              Mark all read
            </Button>
          </div>
          <SheetDescription className="text-xs">
            Task updates from your workspaces, notebooks, and pipelines.
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1">
          <div className="p-2">
            {loading && items.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-sm text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
                Loading notifications…
              </div>
            ) : error && items.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
                <div className="flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10 text-destructive">
                  <TriangleAlert className="h-5 w-5" />
                </div>
                <p className="text-sm text-muted-foreground">{error}</p>
                <Button variant="outline" size="sm" onClick={() => void loadList()}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  Try again
                </Button>
              </div>
            ) : items.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
                <div className="flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <BellOff className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">You're all caught up</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Finished tasks will show up here.
                  </p>
                </div>
              </div>
            ) : (
              <ul className="space-y-1">
                {items.map((item) => {
                  const { Icon, tone } = visualFor(item.type);
                  const unread = isUnread(item);
                  const clickable = Boolean(item.link);
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => void handleItemClick(item)}
                        disabled={!clickable && !unread}
                        className={cn(
                          "flex w-full items-start gap-3 rounded-lg px-3 py-3 text-left transition-colors",
                          clickable || unread
                            ? "hover:bg-accent"
                            : "cursor-default opacity-80",
                          unread && "bg-primary/[0.04]",
                        )}
                      >
                        <span
                          className={cn(
                            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
                            tone,
                          )}
                        >
                          <Icon className="h-4 w-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-start justify-between gap-2">
                            <span
                              className={cn(
                                "text-sm leading-snug",
                                unread ? "font-semibold text-foreground" : "font-medium text-foreground/90",
                              )}
                            >
                              {item.title}
                            </span>
                            {unread && (
                              <span
                                className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary"
                                aria-label="Unread"
                              />
                            )}
                          </span>
                          {item.body && (
                            <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-muted-foreground">
                              {item.body}
                            </span>
                          )}
                          {item.created_at && (
                            <span className="mt-1 block text-[11px] text-muted-foreground/80">
                              {relativeTime(item.created_at)}
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

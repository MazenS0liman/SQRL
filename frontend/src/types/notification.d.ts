export type NotificationType =
  | "job_succeeded"
  | "job_failed"
  | "pipeline_run"
  | "workspace_build"
  | "notebook_run"
  | "retrain"
  | "stream"
  | "system"
  | string;

export interface NotificationItem {
  id: string;
  user_id: string;
  type: NotificationType;
  title: string;
  body: string;
  link?: string | null;
  read_at?: string | null;
  email_sent_at?: string | null;
  email_error?: string | null;
  created_at?: string | null;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  unread_count: number;
}

export interface UnreadCountResponse {
  count: number;
}

export interface MarkAllReadResponse {
  updated: number;
}

export interface NotificationPreferences {
  user_id: string;
  email_enabled: boolean;
  digest_mode: "immediate" | "daily" | "off" | string;
  updated_at?: string | null;
}

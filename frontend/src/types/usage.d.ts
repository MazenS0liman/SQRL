export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  requests: number;
  estimated_cost_usd: number;
}

export interface UsageProviderBreakdown extends UsageTotals {
  provider: string;
}

export interface UsageModelBreakdown extends UsageTotals {
  provider: string;
  model: string;
}

export interface UsageSourceBreakdown extends UsageTotals {
  source: string;
}

export interface UsageDailyPoint {
  day: string;
  provider: string;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface UsageEventRead {
  id: number;
  user_id?: string | null;
  provider: string;
  model: string;
  source: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  estimated_cost_usd: number;
  created_at?: string | null;
}

export interface UsageOverviewResponse {
  window_days: number;
  totals: UsageTotals;
  by_provider: UsageProviderBreakdown[];
  by_model: UsageModelBreakdown[];
  by_source: UsageSourceBreakdown[];
  daily: UsageDailyPoint[];
  recent_events: UsageEventRead[];
}

export interface UsageEventListResponse {
  items: UsageEventRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface UsagePricingEntry {
  model: string;
  input_per_million: number;
  output_per_million: number;
}

export interface UsagePricingResponse {
  providers: Record<string, string>;
  pricing: UsagePricingEntry[];
  note: string;
}

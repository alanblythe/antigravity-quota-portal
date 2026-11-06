export type UserStatus = 'ACTIVE' | 'AUTO_DISABLED' | 'MANUALLY_DISABLED';

export interface ModelTokenUsage {
  total_tokens: number;
  prompt_tokens: number;
  candidates_tokens: number;
  cached_tokens: number;
  thoughts_tokens: number;
  prompt_tokens_gt_200k?: number;
  candidates_tokens_gt_200k?: number;
  cached_tokens_gt_200k?: number;
  thoughts_tokens_gt_200k?: number;
  fallback_tokens?: number;
}

export interface SpendBreakdown {
  input_spend_usd: number;
  output_spend_usd: number;
  cached_spend_usd: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  thoughts_tokens?: number;
}

export interface CurrentWeekUsage {
  week_id: string;
  total_tokens: number;
  total_requests: number;
  gross_spend_usd: number;
  quota_credits_usd: number;
  remaining_credit_usd: number;
  net_billable_cost_usd: number;
  overage_buffer_usd: number;
  spend_breakdown: SpendBreakdown;
  tokens_by_model: Record<string, number>;
  token_details_by_model?: Record<string, ModelTokenUsage>;
  spend_by_model: Record<string, number>;
  credit_utilization_percentage: number;
  last_active?: string | null;
  wtu_net_usage?: number | null;
  wtu_limit?: number | null;
  wtu_utilization_pct?: number | null;
  wtu_window_start_at?: string | null;
  wtu_window_reset_at?: string | null;
}

export interface ProjectWTUPoolStatus {
  project_id: string;
  tier_metric: string;
  license_tier: string;
  active: boolean;
  wtu_consumed: number;
  wtu_limit: number;
  wtu_utilization_pct: number;
  cycle_start_utc?: string | null;
  cycle_reset_utc?: string | null;
  days_until_reset: number;
  hours_until_reset?: number;
  active_developers: number;
  throttled_developers: number;
  cycle_spend_usd: number;
}

export interface User {
  email: string;
  status: UserStatus;
  is_exempt: boolean;
  has_custom_quota: boolean;
  custom_quota_usd?: number | null;
  custom_overage_usd?: number | null;
  project_id?: string | null;
  license_tier?: string | null;
  current_week: CurrentWeekUsage;
  created_at: string;
  updated_at: string;
}

export interface ModelPricing {
  display_name: string;
  log_pattern: string;
  input_price_per_million: number;
  output_price_per_million: number;
  cached_price_per_million: number;
  context_tier_threshold_tokens?: number | null;
  input_price_gt_threshold_per_million?: number | null;
  output_price_gt_threshold_per_million?: number | null;
  cached_price_gt_threshold_per_million?: number | null;
  estimated_input_ratio: number;
  estimated_output_ratio: number;
  estimated_cached_ratio: number;
}

export interface AppConfig {
  doc_id: string;
  timezone: string;
  enabled_group: string;
  disabled_group: string;
  default_quota_usd: number;
  default_overage_usd: number;
  weekly_team_budget_usd?: number;
  weekly_wtu_quota?: number;
  wtu_tier_metric?: string;
  monitored_projects?: string[];
  preset_quotas: number[];
  webhook_alert_url: string;
  last_publish_timestamp?: string | null;
  models: Record<string, ModelPricing>;
}

export interface KPIStats {
  active_developers: number;
  auto_throttled_developers: number;
  manually_locked_developers: number;
  exempt_developers: number;
  total_tokens_this_week: number;
  total_gross_usage_usd: number;
  total_credits_allocated_usd: number;
  total_net_overages_usd: number;
  week_id: string;
  week_start_utc?: string | null;
  week_reset_utc?: string | null;
  days_until_reset?: number;
  hours_until_reset?: number;
  weekly_team_budget_usd?: number;
  weekly_budget_utilization_pct?: number;
  wtu_pool_net_usage?: number | null;
  wtu_pool_limit?: number | null;
  wtu_pool_utilization_pct?: number | null;
  wtu_window_start_utc?: string | null;
  wtu_window_reset_utc?: string | null;
  wtu_user_usage?: Record<string, {
    project_id?: string;
    net_usage: number;
    limit?: number | null;
    utilization_pct?: number | null;
    window_start_utc?: string | null;
    window_reset_utc?: string | null;
  }>;
  monitored_projects?: string[];
  project_pools?: ProjectWTUPoolStatus[];
}

export interface AuditEvent {
  event_id: string;
  timestamp: string;
  action: string;
  triggered_by: string;
  target_user?: string | null;
  details: Record<string, any>;
}

export interface EvaluationResult {
  evaluated_at: string;
  week_id: string;
  users_evaluated: number;
  group_swaps_count: number;
  failed_swaps_count?: number;
  changes: Array<{
    user: string;
    action: string;
    reason: string;
    status: string;
  }>;
  success: boolean;
  error?: string | null;
}

export interface UserUpdatePayload {
  is_exempt?: boolean;
  status?: UserStatus;
  has_custom_quota?: boolean;
  custom_quota_usd?: number | null;
  custom_overage_usd?: number | null;
  project_id?: string | null;
  license_tier?: string | null;
}

export interface ConfigUpdatePayload {
  default_quota_usd?: number;
  default_overage_usd?: number;
  weekly_team_budget_usd?: number;
  weekly_wtu_quota?: number;
  wtu_tier_metric?: string;
  monitored_projects?: string[];
  enabled_group?: string;
  disabled_group?: string;
  timezone?: string;
  preset_quotas?: number[];
  webhook_alert_url?: string | null;
}

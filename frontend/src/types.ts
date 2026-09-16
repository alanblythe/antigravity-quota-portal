export type UserStatus = 'ACTIVE' | 'AUTO_DISABLED' | 'MANUALLY_DISABLED';

export interface SpendBreakdown {
  input_spend_usd: number;
  output_spend_usd: number;
  cached_spend_usd: number;
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
  spend_by_model: Record<string, number>;
  credit_utilization_percentage: number;
  last_active?: string | null;
}

export interface User {
  email: string;
  status: UserStatus;
  is_exempt: boolean;
  has_custom_quota: boolean;
  custom_quota_usd?: number | null;
  custom_overage_usd?: number | null;
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
}

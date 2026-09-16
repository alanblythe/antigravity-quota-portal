"""Cost modeling, token pricing, and hinge point calculations."""

import fnmatch
import re

from app.db.models import DEFAULT_MODEL_PRICING, ModelPricing, SpendBreakdown


def match_model_pricing(model_name: str, pricing_matrix: dict[str, ModelPricing]) -> ModelPricing:
    """Match a model name from logs to the appropriate ModelPricing configuration.

    Supports SQL LIKE style wildcards (e.g., '%3%pro%') or fnmatch patterns.
    """
    model_name_lower = model_name.lower().strip()

    # 1. Direct key match
    if model_name_lower in pricing_matrix:
        return pricing_matrix[model_name_lower]

    # 2. Pattern match using log_pattern
    for key, pricing in pricing_matrix.items():
        if key == "default_fallback":
            continue

        pattern = pricing.log_pattern.lower()
        # Convert SQL LIKE '%' wildcard to regex '.*'
        regex_pattern = "^" + pattern.replace("%", ".*") + "$"
        if re.search(regex_pattern, model_name_lower):
            return pricing

        # Also try standard glob match
        glob_pattern = pattern.replace("%", "*")
        if fnmatch.fnmatch(model_name_lower, glob_pattern):
            return pricing

    # 3. Fallback
    return pricing_matrix.get("default_fallback", DEFAULT_MODEL_PRICING["default_fallback"])


def calculate_blended_rate_per_million(pricing: ModelPricing) -> float:
    """Calculate the blended rate per 1M tokens based on hinge point ratios.

    R_blended = (w_in * P_in) + (w_out * P_out) + (w_cache * P_cache)
    """
    return (
        (pricing.estimated_input_ratio * pricing.input_price_per_million)
        + (pricing.estimated_output_ratio * pricing.output_price_per_million)
        + (pricing.estimated_cached_ratio * pricing.cached_price_per_million)
    )


def calculate_model_spend(
    token_count: int,
    pricing: ModelPricing,
) -> tuple[float, SpendBreakdown]:
    """Calculate gross spend and spend breakdown for a given token count and model pricing."""
    if token_count <= 0:
        return 0.0, SpendBreakdown(input_spend_usd=0.0, output_spend_usd=0.0, cached_spend_usd=0.0)

    input_tokens = token_count * pricing.estimated_input_ratio
    output_tokens = token_count * pricing.estimated_output_ratio
    cached_tokens = token_count * pricing.estimated_cached_ratio

    input_spend = (input_tokens * pricing.input_price_per_million) / 1_000_000.0
    output_spend = (output_tokens * pricing.output_price_per_million) / 1_000_000.0
    cached_spend = (cached_tokens * pricing.cached_price_per_million) / 1_000_000.0

    total_gross = input_spend + output_spend + cached_spend
    breakdown = SpendBreakdown(
        input_spend_usd=round(input_spend, 4),
        output_spend_usd=round(output_spend, 4),
        cached_spend_usd=round(cached_spend, 4),
    )

    return round(total_gross, 4), breakdown


def calculate_total_usage_costs(
    tokens_by_model: dict[str, int],
    pricing_matrix: dict[str, ModelPricing],
    quota_credits: float,
    overage_buffer: float,
) -> tuple[float, float, float, SpendBreakdown, dict[str, float], float, bool]:
    """Calculate all financial ledger metrics for a user's weekly token usage.

    Returns:
        gross_spend_usd: Total estimated dollar value of tokens consumed
        remaining_credit_usd: max(0, quota_credits - gross_spend_usd)
        net_billable_cost_usd: max(0, gross_spend_usd - quota_credits)
        spend_breakdown: Aggregated input, output, cached dollar amounts
        spend_by_model: Per-model dollar amounts
        credit_utilization_pct: (gross_spend_usd / quota_credits) * 100
        is_throttled: True if gross_spend_usd >= (quota_credits + overage_buffer)
    """
    total_gross_spend = 0.0
    total_input_spend = 0.0
    total_output_spend = 0.0
    total_cached_spend = 0.0
    spend_by_model: dict[str, float] = {}

    for model_name, token_count in tokens_by_model.items():
        pricing = match_model_pricing(model_name, pricing_matrix)
        model_gross, breakdown = calculate_model_spend(token_count, pricing)
        spend_by_model[model_name] = round(model_gross, 4)
        total_gross_spend += model_gross
        total_input_spend += breakdown.input_spend_usd
        total_output_spend += breakdown.output_spend_usd
        total_cached_spend += breakdown.cached_spend_usd

    total_gross_spend = round(total_gross_spend, 2)
    remaining_credit = max(0.0, round(quota_credits - total_gross_spend, 2))
    net_billable_cost = max(0.0, round(total_gross_spend - quota_credits, 2))

    if quota_credits > 0:
        utilization_pct = round((total_gross_spend / quota_credits) * 100.0, 2)
    else:
        utilization_pct = 100.0 if total_gross_spend > 0 else 0.0

    hard_limit = quota_credits + overage_buffer
    is_throttled = total_gross_spend >= hard_limit

    total_breakdown = SpendBreakdown(
        input_spend_usd=round(total_input_spend, 2),
        output_spend_usd=round(total_output_spend, 2),
        cached_spend_usd=round(total_cached_spend, 2),
    )

    return (
        total_gross_spend,
        remaining_credit,
        net_billable_cost,
        total_breakdown,
        spend_by_model,
        utilization_pct,
        is_throttled,
    )

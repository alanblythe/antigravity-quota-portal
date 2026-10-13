"""Cost modeling, token pricing, and hinge point calculations."""

import fnmatch
import math
import re

from app.db.models import DEFAULT_MODEL_PRICING, ModelPricing, SpendBreakdown


def _finite_non_neg(val: float | int | None, default: float = 0.0) -> float:
    """Return a finite non-negative float, falling back to default on None/NaN/Inf."""
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return max(0.0, f)


def _safe_non_neg_int(val: int | float | None, default: int = 0) -> int:
    """Return a finite non-negative integer, falling back to default on None/NaN/Inf."""
    return max(0, int(round(_finite_non_neg(val, float(default)))))


def match_model_pricing(model_name: str, pricing_matrix: dict[str, ModelPricing]) -> ModelPricing:
    """Match a model name from logs to the appropriate ModelPricing configuration.

    Supports SQL LIKE style wildcards (e.g., '%3%pro%') or fnmatch patterns.
    """
    model_name_lower = (model_name or "").lower().strip()
    short_name = model_name_lower.rsplit("/", 1)[-1]
    lower_map = {k.lower(): v for k, v in pricing_matrix.items()}

    # 1. Direct key match (case-insensitive)
    if model_name_lower in lower_map:
        return lower_map[model_name_lower]
    if short_name in lower_map:
        return lower_map[short_name]

    # 2. Pattern match using log_pattern (sorted by pattern specificity descending).
    # Pass 1 matches against short_name first so path segments (e.g. europe-west3) never
    # false-match a numeric wildcard before the model's short name is evaluated.
    sorted_items = sorted(
        pricing_matrix.items(),
        key=lambda kv: (len(kv[1].log_pattern.replace("%", "").replace("*", "")), len(kv[0]), kv[0]),
        reverse=True,
    )
    candidates = [short_name] if short_name == model_name_lower else [short_name, model_name_lower]
    for candidate in candidates:
        for key, pricing in sorted_items:
            if key.lower() == "default_fallback":
                continue

            pattern = pricing.log_pattern.lower()
            # Escape literal regex characters (like '.') then convert SQL LIKE '%' wildcard to '.*'
            regex_pattern = "^" + ".*".join(re.escape(part) for part in pattern.split("%")) + "$"
            if re.search(regex_pattern, candidate):
                return pricing

            # Also try standard glob match
            glob_pattern = pattern.replace("%", "*")
            if fnmatch.fnmatch(candidate, glob_pattern):
                return pricing

    # 3. Fallback
    return lower_map.get("default_fallback", DEFAULT_MODEL_PRICING["default_fallback"])


def calculate_blended_rate_per_million(pricing: ModelPricing) -> float:
    """Calculate the blended rate per 1M tokens based on hinge point ratios.

    R_blended = (w_in * P_in) + (w_out * P_out) + (w_cache * P_cache)
    """
    w_in = _finite_non_neg(pricing.estimated_input_ratio)
    p_in = _finite_non_neg(pricing.input_price_per_million)
    w_out = _finite_non_neg(pricing.estimated_output_ratio)
    p_out = _finite_non_neg(pricing.output_price_per_million)
    w_cache = _finite_non_neg(pricing.estimated_cached_ratio)
    p_cache = _finite_non_neg(pricing.cached_price_per_million)
    return _finite_non_neg((w_in * p_in) + (w_out * p_out) + (w_cache * p_cache))


def calculate_model_spend(
    token_count: int,
    pricing: ModelPricing,
    prompt_tokens: int = 0,
    candidates_tokens: int = 0,
    cached_tokens: int = 0,
    thoughts_tokens: int = 0,
    prompt_tokens_gt_200k: int = 0,
    candidates_tokens_gt_200k: int = 0,
    cached_tokens_gt_200k: int = 0,
    thoughts_tokens_gt_200k: int = 0,
    fallback_tokens: int | None = None,
) -> tuple[float, SpendBreakdown]:
    """Calculate gross spend and spend breakdown for a given token count and model pricing.

    Uses actual granular token telemetry (prompt, candidates, cached, thoughts) when present:
      - Effective Input Tokens = max(0, prompt_tokens - cached_tokens)
      - Cached Tokens = cached_tokens
      - Output Tokens = candidates_tokens + thoughts_tokens
    Applies >200K context tier rates when *_gt_200k token counts and model gt_threshold rates are present.
    Any legacy/fallback tokens without granular sub-counts use estimated hinge-point ratios.
    """
    token_count = _safe_non_neg_int(token_count)
    prompt_tokens = _safe_non_neg_int(prompt_tokens)
    candidates_tokens = _safe_non_neg_int(candidates_tokens)
    cached_tokens = _safe_non_neg_int(cached_tokens)
    thoughts_tokens = _safe_non_neg_int(thoughts_tokens)
    prompt_tokens_gt_200k = _safe_non_neg_int(prompt_tokens_gt_200k)
    candidates_tokens_gt_200k = _safe_non_neg_int(candidates_tokens_gt_200k)
    cached_tokens_gt_200k = _safe_non_neg_int(cached_tokens_gt_200k)
    thoughts_tokens_gt_200k = _safe_non_neg_int(thoughts_tokens_gt_200k)

    has_granular = (prompt_tokens + candidates_tokens + cached_tokens + thoughts_tokens) > 0
    if token_count <= 0 and not has_granular:
        return 0.0, SpendBreakdown()

    if has_granular:
        gt_prompt = min(prompt_tokens, max(0, prompt_tokens_gt_200k))
        gt_cached = min(cached_tokens, gt_prompt, max(0, cached_tokens_gt_200k))
        gt_candidates = min(candidates_tokens, max(0, candidates_tokens_gt_200k))
        gt_thoughts = min(thoughts_tokens, max(0, thoughts_tokens_gt_200k))

        le_prompt = max(0, prompt_tokens - gt_prompt)
        le_cached = min(max(0, cached_tokens - gt_cached), le_prompt)
        le_candidates = max(0, candidates_tokens - gt_candidates)
        le_thoughts = max(0, thoughts_tokens - gt_thoughts)

        le_input_tokens = max(0, le_prompt - le_cached)
        le_output_tokens = le_candidates + le_thoughts
        le_cached_tokens = le_cached

        gt_input_tokens = max(0, gt_prompt - gt_cached)
        gt_output_tokens = gt_candidates + gt_thoughts
        gt_cached_tokens = gt_cached

        eff_input_tokens = le_input_tokens + gt_input_tokens
        eff_output_tokens = le_output_tokens + gt_output_tokens
        eff_cached_tokens = le_cached_tokens + gt_cached_tokens
        accounted_granular_tokens = max(prompt_tokens, cached_tokens) + candidates_tokens + thoughts_tokens
        rem_fallback = (
            _safe_non_neg_int(fallback_tokens)
            if fallback_tokens is not None
            else max(0, token_count - accounted_granular_tokens)
        )
    else:
        le_input_tokens = 0
        le_output_tokens = 0
        le_cached_tokens = 0
        gt_input_tokens = 0
        gt_output_tokens = 0
        gt_cached_tokens = 0
        eff_input_tokens = 0
        eff_output_tokens = 0
        eff_cached_tokens = 0
        rem_fallback = token_count if fallback_tokens is None else _safe_non_neg_int(fallback_tokens)

    w_in = _finite_non_neg(pricing.estimated_input_ratio)
    w_out = _finite_non_neg(pricing.estimated_output_ratio)
    w_cache = _finite_non_neg(pricing.estimated_cached_ratio)
    p_in = _finite_non_neg(pricing.input_price_per_million)
    p_out = _finite_non_neg(pricing.output_price_per_million)
    p_cache = _finite_non_neg(pricing.cached_price_per_million)

    fallback_in = _finite_non_neg(rem_fallback * w_in)
    fallback_out = _finite_non_neg(rem_fallback * w_out)
    fallback_cache = _finite_non_neg(rem_fallback * w_cache)

    total_in_tokens = _finite_non_neg(eff_input_tokens + fallback_in)
    total_out_tokens = _finite_non_neg(eff_output_tokens + fallback_out)
    total_cache_tokens = _finite_non_neg(eff_cached_tokens + fallback_cache)

    has_gt_tier = bool(pricing.context_tier_threshold_tokens and pricing.context_tier_threshold_tokens > 0)
    in_price_gt = (
        _finite_non_neg(pricing.input_price_gt_threshold_per_million, p_in)
        if (has_gt_tier and pricing.input_price_gt_threshold_per_million is not None)
        else p_in
    )
    out_price_gt = (
        _finite_non_neg(pricing.output_price_gt_threshold_per_million, p_out)
        if (has_gt_tier and pricing.output_price_gt_threshold_per_million is not None)
        else p_out
    )
    cache_price_gt = (
        _finite_non_neg(pricing.cached_price_gt_threshold_per_million, p_cache)
        if (has_gt_tier and pricing.cached_price_gt_threshold_per_million is not None)
        else p_cache
    )

    input_spend = _finite_non_neg(
        ((le_input_tokens + fallback_in) * p_in + gt_input_tokens * in_price_gt) / 1_000_000.0
    )
    output_spend = _finite_non_neg(
        ((le_output_tokens + fallback_out) * p_out + gt_output_tokens * out_price_gt) / 1_000_000.0
    )
    cached_spend = _finite_non_neg(
        ((le_cached_tokens + fallback_cache) * p_cache + gt_cached_tokens * cache_price_gt) / 1_000_000.0
    )

    total_gross = _finite_non_neg(input_spend + output_spend + cached_spend)
    breakdown = SpendBreakdown(
        input_spend_usd=round(input_spend, 4),
        output_spend_usd=round(output_spend, 4),
        cached_spend_usd=round(cached_spend, 4),
        input_tokens=int(round(total_in_tokens)),
        output_tokens=int(round(total_out_tokens)),
        cached_tokens=int(round(total_cache_tokens)),
        thoughts_tokens=thoughts_tokens,
    )

    return round(total_gross, 4), breakdown


def calculate_total_usage_costs(
    tokens_by_model: dict[str, int],
    pricing_matrix: dict[str, ModelPricing],
    quota_credits: float,
    overage_buffer: float,
    granular_by_model: dict[str, dict[str, int]] | None = None,
) -> tuple[float, float, float, SpendBreakdown, dict[str, float], float, bool]:
    """Calculate all financial ledger metrics for a user's weekly token usage.

    Returns:
        gross_spend_usd: Total estimated dollar value of tokens consumed
        remaining_credit_usd: max(0, quota_credits - gross_spend_usd)
        net_billable_cost_usd: max(0, gross_spend_usd - quota_credits)
        spend_breakdown: Aggregated input, output, cached dollar amounts and token counts
        spend_by_model: Per-model dollar amounts
        credit_utilization_pct: (gross_spend_usd / quota_credits) * 100
        is_throttled: True if gross_spend_usd >= (quota_credits + overage_buffer)
    """
    quota_credits = _finite_non_neg(quota_credits)
    overage_buffer = _finite_non_neg(overage_buffer)
    total_gross_spend = 0.0
    total_input_spend = 0.0
    total_output_spend = 0.0
    total_cached_spend = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_tokens = 0
    total_thoughts_tokens = 0
    spend_by_model: dict[str, float] = {}

    for model_name, token_count in tokens_by_model.items():
        pricing = match_model_pricing(model_name, pricing_matrix)
        g = (granular_by_model or {}).get(model_name, {})
        model_gross, breakdown = calculate_model_spend(
            token_count=token_count,
            pricing=pricing,
            prompt_tokens=g.get("prompt_tokens", 0),
            candidates_tokens=g.get("candidates_tokens", 0),
            cached_tokens=g.get("cached_tokens", 0),
            thoughts_tokens=g.get("thoughts_tokens", 0),
            prompt_tokens_gt_200k=g.get("prompt_tokens_gt_200k", 0),
            candidates_tokens_gt_200k=g.get("candidates_tokens_gt_200k", 0),
            cached_tokens_gt_200k=g.get("cached_tokens_gt_200k", 0),
            thoughts_tokens_gt_200k=g.get("thoughts_tokens_gt_200k", 0),
            fallback_tokens=g.get("fallback_tokens"),
        )
        model_gross = _finite_non_neg(model_gross)
        spend_by_model[model_name] = round(model_gross, 4)
        total_gross_spend += model_gross
        total_input_spend += _finite_non_neg(breakdown.input_spend_usd)
        total_output_spend += _finite_non_neg(breakdown.output_spend_usd)
        total_cached_spend += _finite_non_neg(breakdown.cached_spend_usd)
        total_input_tokens += breakdown.input_tokens
        total_output_tokens += breakdown.output_tokens
        total_cached_tokens += breakdown.cached_tokens
        total_thoughts_tokens += breakdown.thoughts_tokens

    total_breakdown = SpendBreakdown(
        input_spend_usd=round(_finite_non_neg(total_input_spend), 2),
        output_spend_usd=round(_finite_non_neg(total_output_spend), 2),
        cached_spend_usd=round(_finite_non_neg(total_cached_spend), 2),
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cached_tokens=total_cached_tokens,
        thoughts_tokens=total_thoughts_tokens,
    )
    total_gross_spend = round(
        _finite_non_neg(
            total_breakdown.input_spend_usd
            + total_breakdown.output_spend_usd
            + total_breakdown.cached_spend_usd
        ),
        2,
    )
    remaining_credit = max(0.0, round(_finite_non_neg(quota_credits - total_gross_spend), 2))
    net_billable_cost = max(0.0, round(_finite_non_neg(total_gross_spend - quota_credits), 2))

    if quota_credits > 0:
        utilization_pct = round(_finite_non_neg((total_gross_spend / quota_credits) * 100.0), 2)
    else:
        utilization_pct = 100.0 if total_gross_spend > 0 else 0.0

    hard_limit = round(_finite_non_neg(quota_credits + overage_buffer), 2)
    is_throttled = total_gross_spend >= hard_limit

    return (
        total_gross_spend,
        remaining_credit,
        net_billable_cost,
        total_breakdown,
        spend_by_model,
        utilization_pct,
        is_throttled,
    )

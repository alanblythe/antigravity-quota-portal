"""Unit tests for cost modeling, hinge points, and credit mechanics."""

from app.core.cost_model import (
    calculate_blended_rate_per_million,
    calculate_model_spend,
    calculate_total_usage_costs,
    match_model_pricing,
)
from app.db.models import DEFAULT_MODEL_PRICING, load_default_model_pricing


def test_load_default_model_pricing_from_external_config():
    pricing_map = load_default_model_pricing()
    assert "gemini-3-pro" in pricing_map
    assert "gemini-3.8-flash" in pricing_map
    assert "claude-opus-5.5" in pricing_map
    assert "claude-sonnet-5.5" in pricing_map
    assert pricing_map["gemini-3-pro"].input_price_per_million == 2.00
    assert pricing_map["gemini-3-pro"].input_price_gt_threshold_per_million == 4.00
    assert pricing_map["gemini-3.8-flash"].input_price_per_million == 0.75
    assert pricing_map["gemini-3.8-flash"].input_price_gt_threshold_per_million is None


def test_match_model_pricing():
    # Exact and pattern matching tests
    pricing = match_model_pricing("gemini-3-pro", DEFAULT_MODEL_PRICING)
    assert pricing.display_name == "Gemini 3.1 Pro / 3.0 Pro"

    pricing_38 = match_model_pricing("models/gemini-3.8-flash-high", DEFAULT_MODEL_PRICING)
    assert pricing_38.display_name == "Gemini 3.8 Flash"

    pricing_37 = match_model_pricing("models/gemini-3.7-flash-medium", DEFAULT_MODEL_PRICING)
    assert pricing_37.display_name == "Gemini 3.7 Flash"

    pricing_flash = match_model_pricing("models/gemini-3.6-flash-preview", DEFAULT_MODEL_PRICING)
    assert pricing_flash.display_name == "Gemini 3.6 Flash"

    pricing_opus = match_model_pricing("claude-opus-5.5-high", DEFAULT_MODEL_PRICING)
    assert pricing_opus.display_name == "Claude Opus 5.5"

    pricing_fallback = match_model_pricing("unknown-custom-model", DEFAULT_MODEL_PRICING)
    assert pricing_fallback.display_name == "Default Model Fallback"


def test_calculate_blended_rate():
    pricing = DEFAULT_MODEL_PRICING["gemini-3-pro"]
    # 0.70 * 2.00 + 0.20 * 12.00 + 0.10 * 0.20 = 1.40 + 2.40 + 0.02 = 3.82
    rate = calculate_blended_rate_per_million(pricing)
    assert round(rate, 3) == 3.82


def test_calculate_model_spend():
    pricing = DEFAULT_MODEL_PRICING["gemini-3-pro"]
    # 1,000,000 tokens of gemini-3-pro (fallback blended rate)
    gross_spend, breakdown = calculate_model_spend(1_000_000, pricing)
    assert gross_spend == 3.82
    assert breakdown.input_spend_usd == 1.40
    assert breakdown.output_spend_usd == 2.40
    assert breakdown.cached_spend_usd == 0.02


def test_calculate_total_usage_costs_under_quota():
    tokens = {
        "gemini-3-pro": 2_000_000,  # 2M * 3.82 = $7.64
    }
    quota = 10.00
    overage = 2.00
    (
        gross_spend,
        remaining_credit,
        net_billable,
        breakdown,
        spend_by_model,
        utilization_pct,
        is_throttled,
    ) = calculate_total_usage_costs(tokens, DEFAULT_MODEL_PRICING, quota, overage)

    assert gross_spend == 7.64
    assert remaining_credit == 2.36
    assert net_billable == 0.00
    assert utilization_pct == 76.4
    assert is_throttled is False


def test_calculate_total_usage_costs_in_overage_buffer():
    tokens = {
        "gemini-3-pro": 3_000_000,  # 3M * 3.82 = $11.46
    }
    quota = 10.00
    overage = 2.00  # Hard limit is $12.00
    (
        gross_spend,
        remaining_credit,
        net_billable,
        breakdown,
        spend_by_model,
        utilization_pct,
        is_throttled,
    ) = calculate_total_usage_costs(tokens, DEFAULT_MODEL_PRICING, quota, overage)

    assert gross_spend == 11.46
    assert remaining_credit == 0.00
    assert net_billable == 1.46
    assert utilization_pct == 114.6
    assert is_throttled is False  # $11.46 < $12.00


def test_calculate_total_usage_costs_hard_breach():
    tokens = {
        "gemini-3-pro": 4_000_000,  # 4M * 3.82 = $15.28
    }
    quota = 10.00
    overage = 2.00  # Hard limit is $12.00
    (
        gross_spend,
        remaining_credit,
        net_billable,
        breakdown,
        spend_by_model,
        utilization_pct,
        is_throttled,
    ) = calculate_total_usage_costs(tokens, DEFAULT_MODEL_PRICING, quota, overage)

    assert gross_spend == 15.28
    assert remaining_credit == 0.00
    assert net_billable == 5.28
    assert is_throttled is True  # $15.28 >= $12.00


def test_calculate_model_spend_with_granular_tokens():
    pricing = DEFAULT_MODEL_PRICING["gemini-3-pro"]
    # promptTokenCount=800_000 (includes 100_000 cached), candidatesTokenCount=150_000, thoughtsTokenCount=50_000
    # Effective Input = 800k - 100k = 700k * $2.00 / 1M = $1.40
    # Output + Thoughts = 150k + 50k = 200k * $12.00 / 1M = $2.40
    # Cached = 100k * $0.20 / 1M = $0.02
    # Total = $3.82
    gross_spend, breakdown = calculate_model_spend(
        token_count=1_000_000,
        pricing=pricing,
        prompt_tokens=800_000,
        candidates_tokens=150_000,
        cached_tokens=100_000,
        thoughts_tokens=50_000,
        fallback_tokens=0,
    )
    assert gross_spend == 3.82
    assert breakdown.input_spend_usd == 1.40
    assert breakdown.output_spend_usd == 2.40
    assert breakdown.cached_spend_usd == 0.02
    assert breakdown.input_tokens == 700_000
    assert breakdown.output_tokens == 200_000
    assert breakdown.cached_tokens == 100_000
    assert breakdown.thoughts_tokens == 50_000


def test_calculate_model_spend_with_gt_200k_context_tier():
    pricing = DEFAULT_MODEL_PRICING["gemini-3-pro"]
    # Suppose 1,000,000 total tokens split evenly between <=200k calls and >200k calls:
    # - <=200k: 350k uncached in ($2/1M = $0.70), 50k cached ($0.20/1M = $0.01), 100k out ($12/1M = $1.20) -> $1.91
    # - >200k:  350k uncached in ($4/1M = $1.40), 50k cached ($0.40/1M = $0.02), 100k out ($18/1M = $1.80) -> $3.22
    # Total expected = $5.13
    gross_spend, breakdown = calculate_model_spend(
        token_count=1_000_000,
        pricing=pricing,
        prompt_tokens=800_000,
        candidates_tokens=150_000,
        cached_tokens=100_000,
        thoughts_tokens=50_000,
        prompt_tokens_gt_200k=400_000,
        candidates_tokens_gt_200k=75_000,
        cached_tokens_gt_200k=50_000,
        thoughts_tokens_gt_200k=25_000,
        fallback_tokens=0,
    )
    assert gross_spend == 5.13
    assert breakdown.input_spend_usd == 2.10
    assert breakdown.output_spend_usd == 3.00
    assert breakdown.cached_spend_usd == 0.03



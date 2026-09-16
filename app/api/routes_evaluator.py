"""Evaluation and publish trigger API endpoints."""

from fastapi import APIRouter, Depends

from app.api.deps import get_db, get_evaluator
from app.core.timezone_engine import get_current_week_window
from app.db.models import EvaluationResult, KPIStats, UserStatus

router = APIRouter(prefix="/api/evaluator", tags=["Evaluator"])


@router.post("/publish", response_model=EvaluationResult)
def publish_and_sync(evaluator=Depends(get_evaluator)):
    """Execute immediate full evaluation & publish sync."""
    result = evaluator.run_evaluation(triggered_by="admin_publish_button", is_publish=True)
    return result


@router.post("/run", response_model=EvaluationResult)
def run_evaluation_manual(evaluator=Depends(get_evaluator)):
    """Run an ad-hoc evaluation without updating the published timestamp."""
    result = evaluator.run_evaluation(triggered_by="admin_manual_run", is_publish=False)
    return result


@router.get("/kpis", response_model=KPIStats)
def get_kpi_stats(db=Depends(get_db)):
    users = db.list_users()
    config = db.get_config()
    week_id, _, _, _ = get_current_week_window(config.timezone)

    active_count = sum(1 for u in users if u.status == UserStatus.ACTIVE and not u.is_exempt)
    throttled_count = sum(1 for u in users if u.status == UserStatus.AUTO_DISABLED)
    locked_count = sum(1 for u in users if u.status == UserStatus.MANUALLY_DISABLED)
    exempt_count = sum(1 for u in users if u.is_exempt)

    total_tokens = sum(u.current_week.total_tokens for u in users)
    total_gross = sum(u.current_week.gross_spend_usd for u in users)
    total_credits = sum(u.current_week.quota_credits_usd for u in users)
    total_net = sum(u.current_week.net_billable_cost_usd for u in users)

    return KPIStats(
        active_developers=active_count,
        auto_throttled_developers=throttled_count,
        manually_locked_developers=locked_count,
        exempt_developers=exempt_count,
        total_tokens_this_week=total_tokens,
        total_gross_usage_usd=round(total_gross, 2),
        total_credits_allocated_usd=round(total_credits, 2),
        total_net_overages_usd=round(total_net, 2),
        week_id=week_id,
    )

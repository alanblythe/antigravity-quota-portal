"""FastAPI Application entry point for Antigravity Quota Portal."""

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.deps import get_db, get_evaluator, get_wtu_client
from app.api.routes_audit import router as audit_router
from app.api.routes_config import router as config_router
from app.api.routes_evaluator import router as evaluator_router
from app.api.routes_users import router as users_router
from app.config import settings
from app.core.timezone_engine import get_current_week_window

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("antigravity-quota-portal")

scheduler = BackgroundScheduler()
_schedule_lock = threading.RLock()


def schedule_project_wtu_reset_triggers():
    """Schedule exact one-shot APScheduler triggers aligned to each monitored project's WTU 7-day reset."""
    try:
        db = get_db()
        wtu_client = get_wtu_client()
        if not hasattr(wtu_client, "fetch_multi_project_wtu_status"):
            return
        config = db.get_config()
        base_projects = config.monitored_projects or settings.monitored_project_ids
        all_users = db.list_users()
        user_projects = [u.project_id for u in all_users if u.project_id]
        monitored_projects = list(dict.fromkeys([*base_projects, *user_projects]))
        primary_proj = monitored_projects[0] if monitored_projects else settings.PROJECT_ID
        wtu_map = wtu_client.fetch_multi_project_wtu_status(
            project_ids=monitored_projects,
            preferred_tier=config.wtu_tier_metric,
            configured_wtu_limit=int(config.weekly_wtu_quota or 0),
        )

        with _schedule_lock:
            now_utc = datetime.now(UTC)
            active_job_ids = set()
            for pid in monitored_projects:
                status = wtu_map.get(pid)
                reset_utc = getattr(status, "active_cycle_reset_utc", None) if status else None
                if reset_utc is not None:
                    reset_utc = (
                        reset_utc.replace(tzinfo=UTC) if reset_utc.tzinfo is None else reset_utc.astimezone(UTC)
                    )
                if not (status and getattr(status, "active", False) and reset_utc):
                    persisted_resets = [
                        (
                            u.current_week.wtu_window_reset_at.replace(tzinfo=UTC)
                            if u.current_week.wtu_window_reset_at.tzinfo is None
                            else u.current_week.wtu_window_reset_at.astimezone(UTC)
                        )
                        for u in all_users
                        if (u.project_id or primary_proj) == pid
                        and u.current_week.wtu_window_reset_at is not None
                    ]
                    if persisted_resets:
                        reset_utc = max(persisted_resets)
                    else:
                        _, _, reset_utc, _ = get_current_week_window(config.timezone, now_utc)
                while reset_utc <= now_utc:
                    reset_utc = reset_utc + timedelta(days=7)
                run_date = reset_utc + timedelta(seconds=5)
                job_id = f"wtu_reset_{pid}"
                active_job_ids.add(job_id)
                if scheduler.running:
                    scheduler.add_job(
                        scheduled_evaluation_job,
                        "date",
                        run_date=run_date,
                        kwargs={"triggered_by": f"WTU_RESET_TIMER:{pid}"},
                        id=job_id,
                        replace_existing=True,
                    )
                    logger.info(
                        f"Scheduled exact Weekly Reset trigger for project '{pid}' at {run_date.isoformat()}"
                    )

            # Prune stale WTU reset jobs for removed projects
            if scheduler.running:
                for job in list(scheduler.get_jobs()):
                    if job.id.startswith("wtu_reset_") and job.id not in active_job_ids:
                        try:
                            scheduler.remove_job(job.id)
                        except Exception as rm_err:
                            logger.warning(f"Failed to remove stale WTU reset job '{job.id}': {rm_err}")
    except Exception as e:
        logger.warning(f"Could not schedule dynamic WTU reset triggers: {e}")


def scheduled_evaluation_job(triggered_by: str = "HOURLY_APS_CRON"):
    logger.info(f"Executing scheduled background quota evaluation (triggered_by={triggered_by})...")
    try:
        evaluator = get_evaluator()
        evaluator.run_evaluation(triggered_by=triggered_by, is_publish=False)
        schedule_project_wtu_reset_triggers()
    except Exception as e:
        logger.error(f"Scheduled quota evaluation failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    # Startup: Start APScheduler
    logger.info("Starting up Antigravity Quota Portal...")
    with _schedule_lock:
        if getattr(scheduler, "_was_shutdown", False):
            scheduler = BackgroundScheduler()
        scheduler.add_job(
            scheduled_evaluation_job,
            "interval",
            hours=settings.SCHEDULER_INTERVAL_HOURS,
            id="hourly_quota_evaluator",
            replace_existing=True,
        )
        if not scheduler.running:
            scheduler.start()
    try:
        await asyncio.to_thread(schedule_project_wtu_reset_triggers)
    except Exception as e:
        logger.warning(f"Initial WTU reset trigger scheduling failed: {e}")
    logger.info(f"Background APScheduler started (interval={settings.SCHEDULER_INTERVAL_HOURS}h)")

    yield

    # Shutdown: Stop scheduler
    logger.info("Shutting down background APScheduler...")
    with _schedule_lock:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            scheduler._was_shutdown = True


app = FastAPI(
    title="Antigravity Quota & Usage Management Portal",
    version=__version__,
    description=(
        "Fine-grained quota visibility, credit tracking, cost estimation, "
        "and automated access governance for Google Antigravity."
    ),
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(users_router)
app.include_router(config_router)
app.include_router(evaluator_router)
app.include_router(audit_router)


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "antigravity-quota-portal",
        "mock_mode": settings.USE_MOCK_SERVICES,
        "timezone": settings.APP_TIMEZONE,
    }


# Static Files & SPA Routing
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # Don't intercept API routes
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        real_static = os.path.realpath(STATIC_DIR)
        file_path = os.path.realpath(os.path.join(STATIC_DIR, full_path))
        if file_path.startswith(real_static + os.sep) and os.path.isfile(file_path):
            return FileResponse(file_path)
        index_file = os.path.join(real_static, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Frontend not compiled"})

"""FastAPI Application entry point for Antigravity Quota Portal."""

import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import get_evaluator
from app.api.routes_audit import router as audit_router
from app.api.routes_config import router as config_router
from app.api.routes_evaluator import router as evaluator_router
from app.api.routes_users import router as users_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("antigravity-quota-portal")

scheduler = AsyncIOScheduler()


def scheduled_evaluation_job():
    logger.info("Executing scheduled hourly background quota evaluation...")
    try:
        evaluator = get_evaluator()
        evaluator.run_evaluation(triggered_by="HOURLY_APS_CRON", is_publish=False)
    except Exception as e:
        logger.error(f"Scheduled quota evaluation failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start APScheduler
    logger.info("Starting up Antigravity Quota Portal...")
    scheduler.add_job(
        scheduled_evaluation_job,
        "interval",
        hours=settings.SCHEDULER_INTERVAL_HOURS,
        id="hourly_quota_evaluator",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Background APScheduler started (interval={settings.SCHEDULER_INTERVAL_HOURS}h)")

    yield

    # Shutdown: Stop scheduler
    logger.info("Shutting down background APScheduler...")
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Antigravity Quota & Usage Management Portal",
    version="1.0.0",
    description=(
        "Fine-grained quota visibility, credit tracking, cost estimation, "
        "and automated access governance for Google Antigravity."
    ),
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
        file_path = os.path.join(STATIC_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        index_file = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Frontend not compiled"})

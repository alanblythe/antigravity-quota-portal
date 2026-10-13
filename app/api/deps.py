"""Dependency injection providers for database, BigQuery, and identity clients."""

import logging
import threading

from fastapi import Request

from app.audit import BaseAuditEmitter, CloudLoggingAuditEmitter, MockAuditEmitter
from app.bq.mock_bq import MockBigQueryUsageClient
from app.config import settings
from app.core.evaluator import QuotaEvaluator
from app.db.mock_db import MockDatabase
from app.identity.mock_identity import MockCloudIdentityGroupManager

logger = logging.getLogger(__name__)

# Singletons for runtime
_deps_lock = threading.RLock()
eval_lock = threading.RLock()
_db_instance = None
_bq_instance = None
_identity_instance = None
_evaluator_instance = None
_audit_emitter_instance = None
_wtu_instance = None


def get_wtu_client():
    global _wtu_instance
    with _deps_lock:
        if _wtu_instance is None:
            wtu_project = (
                settings.monitored_project_ids[0]
                if settings.monitored_project_ids
                else settings.PROJECT_ID
            )
            if settings.USE_MOCK_SERVICES:
                from app.bq.wtu_client import MockWTUClient

                logger.info(f"Initializing MockWTUClient sandbox (project={wtu_project})")
                _wtu_instance = MockWTUClient(project_id=wtu_project)
            else:
                from app.bq.wtu_client import CloudMonitoringWTUClient

                logger.info(f"Initializing CloudMonitoringWTUClient (project={wtu_project})")
                _wtu_instance = CloudMonitoringWTUClient(
                    project_id=wtu_project,
                    quota_project_id=settings.PROJECT_ID,
                )
        return _wtu_instance


def get_audit_emitter() -> BaseAuditEmitter:
    global _audit_emitter_instance
    with _deps_lock:
        if _audit_emitter_instance is None:
            if settings.USE_MOCK_SERVICES or not settings.ENABLE_GCP_AUDIT_LOGGING:
                logger.info("Initializing in-memory MockAuditEmitter")
                _audit_emitter_instance = MockAuditEmitter()
            else:
                logger.info(
                    "Initializing CloudLoggingAuditEmitter "
                    f"(project={settings.PROJECT_ID}, log_name={settings.GCP_AUDIT_LOG_NAME})"
                )
                _audit_emitter_instance = CloudLoggingAuditEmitter(
                    project_id=settings.PROJECT_ID,
                    log_name=settings.GCP_AUDIT_LOG_NAME,
                )
        return _audit_emitter_instance


def get_db():
    global _db_instance
    with _deps_lock:
        if _db_instance is None:
            emitter = get_audit_emitter()
            if settings.USE_MOCK_SERVICES:
                logger.info("Initializing in-memory MockDatabase sandbox")
                _db_instance = MockDatabase(audit_emitter=emitter)
            else:
                from app.db.firestore_client import FirestoreDatabase

                logger.info(f"Initializing FirestoreDatabase (project={settings.PROJECT_ID})")
                _db_instance = FirestoreDatabase(project_id=settings.PROJECT_ID, audit_emitter=emitter)
        return _db_instance


def get_bq():
    global _bq_instance
    with _deps_lock:
        if _bq_instance is None:
            if settings.USE_MOCK_SERVICES:
                logger.info("Initializing MockBigQueryUsageClient sandbox")
                _bq_instance = MockBigQueryUsageClient()
            else:
                from app.bq.bq_client import BigQueryUsageClient

                logger.info(
                    "Initializing BigQueryUsageClient "
                    f"(project={settings.PROJECT_ID}, dataset={settings.BIGQUERY_DATASET})"
                )
                _bq_instance = BigQueryUsageClient(
                    project_id=settings.PROJECT_ID,
                    dataset_name=settings.BIGQUERY_DATASET,
                    default_workload_project_id=(
                        settings.monitored_project_ids[0]
                        if settings.monitored_project_ids
                        else settings.PROJECT_ID
                    ),
                )
        return _bq_instance


def get_identity():
    global _identity_instance
    with _deps_lock:
        if _identity_instance is None:
            if settings.USE_MOCK_SERVICES:
                logger.info("Initializing MockCloudIdentityGroupManager sandbox")
                _identity_instance = MockCloudIdentityGroupManager(
                    enabled_group_email=settings.ENABLED_GROUP_EMAIL,
                    disabled_group_email=settings.DISABLED_GROUP_EMAIL,
                )
            else:
                from app.identity.cloud_identity import CloudIdentityGroupManager

                logger.info("Initializing CloudIdentityGroupManager")
                _identity_instance = CloudIdentityGroupManager(
                    enabled_group_email=settings.ENABLED_GROUP_EMAIL,
                    disabled_group_email=settings.DISABLED_GROUP_EMAIL,
                    quota_project_id=settings.PROJECT_ID,
                )
        return _identity_instance


def get_evaluator() -> QuotaEvaluator:
    global _evaluator_instance
    with _deps_lock:
        if _evaluator_instance is None:
            _evaluator_instance = QuotaEvaluator(
                db_client=get_db(),
                bq_client=get_bq(),
                identity_client=get_identity(),
                wtu_client=get_wtu_client(),
            )
            _evaluator_instance._eval_lock = eval_lock
        return _evaluator_instance


def get_actor(request: Request = None, default: str = "admin_ui") -> str:
    """Extract authenticated IAP user email from request headers, falling back to default."""
    if request is not None and hasattr(request, "headers"):
        iap_email = request.headers.get("X-Goog-Authenticated-User-Email")
        if iap_email:
            email_part = iap_email.split(":", 1)[-1].strip().lower()
            if email_part and "@" in email_part:
                return email_part
    if not settings.USE_MOCK_SERVICES:
        logger.warning(
            "Request missing X-Goog-Authenticated-User-Email header "
            "while USE_MOCK_SERVICES=False; using fallback actor '%s'",
            default,
        )
    return default

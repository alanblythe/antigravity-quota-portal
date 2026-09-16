"""Pytest test fixtures."""

import pytest
from starlette.testclient import TestClient

from app.api.deps import get_audit_emitter, get_bq, get_db, get_evaluator, get_identity
from app.audit import MockAuditEmitter
from app.bq.mock_bq import MockBigQueryUsageClient
from app.core.evaluator import QuotaEvaluator
from app.db.mock_db import MockDatabase
from app.identity.mock_identity import MockCloudIdentityGroupManager
from app.main import app


@pytest.fixture
def mock_audit_emitter():
    return MockAuditEmitter()


@pytest.fixture
def mock_db(mock_audit_emitter):
    return MockDatabase(audit_emitter=mock_audit_emitter)


@pytest.fixture
def mock_bq():
    return MockBigQueryUsageClient()


@pytest.fixture
def mock_identity():
    return MockCloudIdentityGroupManager()


@pytest.fixture
def evaluator(mock_db, mock_bq, mock_identity):
    return QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
    )


@pytest.fixture
def client(mock_db, mock_bq, mock_identity, evaluator, mock_audit_emitter):
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_bq] = lambda: mock_bq
    app.dependency_overrides[get_identity] = lambda: mock_identity
    app.dependency_overrides[get_evaluator] = lambda: evaluator
    app.dependency_overrides[get_audit_emitter] = lambda: mock_audit_emitter
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

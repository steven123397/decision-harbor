from datetime import datetime, timezone
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

import decisionharbor.api as api_module
from decisionharbor.api import create_app
from decisionharbor.domain import QueryRun
from decisionharbor.service import ServiceFailure


def terminal_run(status: str = "queued", code: str | None = None) -> QueryRun:
    now = datetime.now(timezone.utc)
    return QueryRun(
        id="75e24c21-416c-4bd8-a37d-68667f4ec753",
        raw_sql="SELECT 1",
        status=status,
        policy_decision="allowed" if status != "rejected" else "rejected",
        policy_version="policy-v1",
        referenced_objects=(),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=1 if status == "succeeded" else None,
        result_truncated=False if status == "succeeded" else None,
        error_code=code,
        error_summary="Safe error summary." if code else None,
        created_at=now,
        started_at=None if status in {"received", "rejected", "queued"} else now,
        finished_at=now if status not in {"received", "queued", "running", "cancelling"} else None,
        duration_ms=None if status in {"received", "queued", "running", "cancelling"} else 4,
    )


class FakeService:
    def __init__(self, failure: ServiceFailure | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, str | None]] = []

    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun:
        self.calls.append((raw_sql, idempotency_key))
        if self.failure:
            raise self.failure
        return terminal_run()


class FakeRepository:
    def __init__(self, *, fail_get: bool = False) -> None:
        self.fail_get = fail_get

    def get(self, run_id: str) -> QueryRun | None:
        if self.fail_get:
            raise RuntimeError("database DSN must not escape")
        return terminal_run() if run_id == "75e24c21-416c-4bd8-a37d-68667f4ec753" else None


def client(
    service: FakeService | None = None,
    ready: bool = True,
    repository: FakeRepository | None = None,
) -> TestClient:
    app = create_app(
        service=service or FakeService(),
        repository=repository or FakeRepository(),
        readiness_check=lambda: ready,
        recover_on_startup=False,
    )
    return TestClient(app)


def test_health_and_ready_have_unified_envelopes() -> None:
    with client() as test_client:
        assert test_client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
        response = test_client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"data": {"status": "ready"}, "error": None}


def test_ready_failure_is_safe_and_does_not_change_health() -> None:
    with client(ready=False) as test_client:
        response = test_client.get("/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_not_ready"
        assert test_client.get("/health").status_code == 200


def test_ready_returns_promptly_when_a_dependency_probe_hangs(
    monkeypatch,
) -> None:
    started = Event()
    release = Event()

    def blocked_readiness_check() -> bool:
        started.set()
        release.wait(timeout=1)
        return False

    monkeypatch.setattr(api_module, "READINESS_TIMEOUT_SECONDS", 0.01, raising=False)
    app = create_app(
        service=FakeService(),
        repository=FakeRepository(),
        readiness_check=blocked_readiness_check,
        recover_on_startup=False,
    )
    try:
        with TestClient(app) as test_client:
            started_at = monotonic()
            response = test_client.get("/ready")
            elapsed = monotonic() - started_at
    finally:
        release.set()

    assert started.is_set()
    assert elapsed < 0.1
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"


def test_post_accepts_the_run_and_returns_no_result() -> None:
    with client() as test_client:
        response = test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})

    assert response.status_code == 202
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["query_run"]["status"] == "queued"
    assert set(payload["data"]) == {"query_run"}


def test_submit_forwards_a_valid_idempotency_key_to_the_service() -> None:
    service = FakeService()
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT 1"},
            headers={"Idempotency-Key": "submit-1"},
        )

    assert response.status_code == 202
    assert service.calls == [("SELECT 1", "submit-1")]


def test_submit_without_a_key_asks_the_service_for_a_new_run_each_time() -> None:
    service = FakeService()
    with client(service) as test_client:
        test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})
        test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})

    assert service.calls == [("SELECT 1", None), ("SELECT 1", None)]


@pytest.mark.parametrize("key", ["", "k" * 129, "key with spaces", "key\x7f"])
def test_invalid_idempotency_keys_are_mapped_to_a_safe_envelope(key: str) -> None:
    failure = ServiceFailure(
        "invalid_idempotency_key",
        "The Idempotency-Key header is invalid.",
        None,
    )
    with client(FakeService(failure)) as test_client:
        response = test_client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT 1"},
            headers={"Idempotency-Key": key},
        )

    assert response.status_code == 422
    assert response.json() == {
        "data": None,
        "error": {
            "code": "invalid_idempotency_key",
            "message": "The Idempotency-Key header is invalid.",
        },
    }


def test_idempotency_conflict_returns_a_stable_409_envelope() -> None:
    failure = ServiceFailure(
        "idempotency_conflict",
        "The idempotency key was already used with a different request.",
        None,
    )
    with client(FakeService(failure)) as test_client:
        response = test_client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT 2"},
            headers={"Idempotency-Key": "submit-1"},
        )

    assert response.status_code == 409
    assert response.json() == {
        "data": None,
        "error": {
            "code": "idempotency_conflict",
            "message": "The idempotency key was already used with a different request.",
        },
    }


def test_policy_rejection_uses_stable_http_mapping_and_run_id() -> None:
    run = terminal_run("rejected", "sql_object_not_allowed")
    failure = ServiceFailure("sql_object_not_allowed", "Object is not allowed.", run)
    with client(FakeService(failure)) as test_client:
        response = test_client.post("/api/v1/query-runs", json={"sql": "SELECT * FROM secrets"})

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "sql_object_not_allowed",
        "message": "Object is not allowed.",
        "query_run_id": run.id,
    }
    assert response.json()["data"]["query_run"]["status"] == "rejected"


def test_invalid_request_and_oversized_body_do_not_create_a_run() -> None:
    with client() as test_client:
        missing = test_client.post("/api/v1/query-runs", json={})
        oversized = test_client.post(
            "/api/v1/query-runs",
            content=b'{"sql":"' + (b"x" * (128 * 1024)) + b'"}',
            headers={"content-type": "application/json"},
        )

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_request"
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "invalid_request"
    assert "query_run_id" not in oversized.json()["error"]


def test_get_returns_audit_without_result_cells() -> None:
    with client() as test_client:
        found = test_client.get("/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753")
        missing = test_client.get("/api/v1/query-runs/11111111-1111-4111-8111-111111111111")

    assert found.status_code == 200
    assert set(found.json()["data"]) == {"query_run"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "query_run_not_found"


def test_get_maps_audit_store_failure_to_safe_envelope() -> None:
    with client(repository=FakeRepository(fail_get=True)) as test_client:
        response = test_client.get("/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753")

    assert response.status_code == 503
    assert response.json() == {
        "data": None,
        "error": {
            "code": "audit_unavailable",
            "message": "The audit store is unavailable.",
        },
    }
    assert "DSN" not in response.text

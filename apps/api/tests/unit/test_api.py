from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

import decisionharbor.api as api_module
from decisionharbor.api import create_app
from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.service import ServiceFailure


def terminal_run(status: str = "succeeded", code: str | None = None) -> QueryRun:
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
        started_at=now if status != "rejected" else None,
        finished_at=now,
        duration_ms=4,
    )


class FakeService:
    def __init__(
        self,
        failure: ServiceFailure | None = None,
        repository=None,
        cancel_outcome: str = "cancelled",
        cancel_failure: ServiceFailure | None = None,
    ) -> None:
        self.failure = failure
        self.repository = repository
        self.cancel_outcome = cancel_outcome
        self.cancel_failure = cancel_failure
        self.submissions: list[tuple[str, str | None]] = []
        self.cancellations: list[str] = []

    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun:
        self.submissions.append((raw_sql, idempotency_key))
        if self.failure:
            raise self.failure
        return replace(
            terminal_run(),
            status="queued",
            started_at=None,
            finished_at=None,
            returned_row_count=None,
            result_truncated=None,
            duration_ms=None,
        )

    def read_result(self, run_id: str) -> QueryResult:
        if self.failure:
            raise self.failure
        if self.repository is None:
            return QueryResult(
                columns=(QueryColumn(name="answer", type="integer"),),
                rows=((1,),),
                truncated=False,
            )
        run = self.repository.get(run_id)
        if run is None:
            raise ServiceFailure("query_run_not_found", "Query run was not found.", None)
        result = self.repository.get_result(run_id)
        if result is not None:
            return result
        if run.status in {"received", "queued", "running", "cancelling"}:
            raise ServiceFailure("result_not_ready", "The query result is not ready yet.", run)
        raise ServiceFailure("result_unavailable", "This query run has no readable result.", run)

    def cancel(self, run_id: str) -> tuple[str, QueryRun]:
        self.cancellations.append(run_id)
        if self.cancel_failure:
            raise self.cancel_failure
        status = {
            "cancelled": "cancelled",
            "cancelling": "cancelling",
            "terminal": "succeeded",
        }[self.cancel_outcome]
        return self.cancel_outcome, terminal_run(status)


class ResultService(FakeService):
    def __init__(self, result: QueryResult | None = None, failure: ServiceFailure | None = None) -> None:
        super().__init__(failure)
        self.result = result or QueryResult(
            columns=(QueryColumn(name="answer", type="integer"),),
            rows=((1,),),
            truncated=False,
        )

    def read_result(self, run_id: str) -> QueryResult:
        if self.failure:
            raise self.failure
        return self.result


class FakeRepository:
    def __init__(self, *, fail_get: bool = False, status: str = "succeeded") -> None:
        self.fail_get = fail_get
        self.status = status

    def get(self, run_id: str) -> QueryRun | None:
        if self.fail_get:
            raise RuntimeError("database DSN must not escape")
        return terminal_run(self.status) if run_id == "75e24c21-416c-4bd8-a37d-68667f4ec753" else None

    def get_result(self, run_id: str) -> QueryResult | None:
        if self.fail_get:
            raise RuntimeError("database DSN must not escape")
        if run_id != "75e24c21-416c-4bd8-a37d-68667f4ec753" or self.status != "succeeded":
            return None
        return QueryResult(
            columns=(QueryColumn(name="answer", type="integer"),),
            rows=((1,),),
            truncated=False,
        )


def client(
    service: FakeService | None = None,
    ready: bool = True,
    repository: FakeRepository | None = None,
) -> TestClient:
    selected_repository = repository or FakeRepository()
    app = create_app(
        service=service or FakeService(repository=selected_repository),
        repository=selected_repository,
        readiness_check=lambda: ready,
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


def test_concurrent_ready_requests_share_the_in_flight_probe() -> None:
    started = Event()
    release = Event()

    def readiness_check() -> bool:
        started.set()
        release.wait(timeout=1)
        return True

    app = create_app(
        service=FakeService(),
        repository=FakeRepository(),
        readiness_check=readiness_check,
    )
    with TestClient(app) as test_client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(test_client.get, "/ready")
        assert started.wait(timeout=1)
        second = executor.submit(test_client.get, "/ready")
        sleep(0.05)
        assert second.done() is False
        release.set()

        assert first.result().status_code == 200
        assert second.result().status_code == 200


def test_post_accepts_a_queued_run_without_an_immediate_result() -> None:
    with client() as test_client:
        response = test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})

    assert response.status_code == 202
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["query_run"]["status"] == "queued"
    assert set(payload["data"]) == {"query_run"}


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
    assert "raw_sql" not in response.json()["data"]["query_run"]


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


@pytest.mark.parametrize("idempotency_key", ["", "contains space", "x" * 129])
def test_invalid_idempotency_key_is_rejected_before_submission(idempotency_key: str) -> None:
    service = FakeService()
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT 1"},
            headers={"Idempotency-Key": idempotency_key},
        )

    assert response.status_code == 422
    assert response.json() == {
        "data": None,
        "error": {
            "code": "invalid_idempotency_key",
            "message": "Idempotency-Key must contain 1 to 128 visible ASCII characters.",
        },
    }
    assert service.submissions == []


@pytest.mark.parametrize("idempotency_key", ["!", "~", "x" * 128])
def test_valid_idempotency_key_is_forwarded_to_submission(idempotency_key: str) -> None:
    service = FakeService()
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT 1"},
            headers={"Idempotency-Key": idempotency_key},
        )

    assert response.status_code == 202
    assert service.submissions == [("SELECT 1", idempotency_key)]


def test_get_returns_audit_without_result_cells() -> None:
    with client() as test_client:
        found = test_client.get("/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753")
        missing = test_client.get("/api/v1/query-runs/11111111-1111-4111-8111-111111111111")

    assert found.status_code == 200
    assert set(found.json()["data"]) == {"query_run"}
    assert "raw_sql" not in found.json()["data"]["query_run"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "query_run_not_found"


def test_get_result_returns_the_persisted_snapshot() -> None:
    with client() as test_client:
        response = test_client.get(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/result"
        )

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "result": {
                "columns": [{"name": "answer", "type": "integer"}],
                "rows": [[1]],
                "truncated": False,
            }
        },
        "error": None,
    }


def test_get_result_reports_that_a_queued_run_is_not_ready() -> None:
    with client(repository=FakeRepository(status="queued")) as test_client:
        response = test_client.get(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/result"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_not_ready"


def test_get_result_reports_that_a_failed_run_has_no_snapshot() -> None:
    with client(repository=FakeRepository(status="failed")) as test_client:
        response = test_client.get(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/result"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_unavailable"


def test_service_result_read_maps_expiry_to_http_410_and_preserves_run_facts() -> None:
    run = terminal_run("succeeded")
    failure = ServiceFailure("result_expired", "The query result has expired.", run)
    with client(service=ResultService(failure=failure)) as test_client:
        response = test_client.get(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/result"
        )

    assert response.status_code == 410
    assert response.json()["error"] == {
        "code": "result_expired",
        "message": "The query result has expired.",
        "query_run_id": run.id,
    }
    assert response.json()["data"]["query_run"]["status"] == "succeeded"


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


@pytest.mark.parametrize(
    ("outcome", "status_code", "run_status"),
    [("cancelled", 200, "cancelled"), ("cancelling", 202, "cancelling"), ("terminal", 200, "succeeded")],
)
def test_cancel_maps_service_outcomes_to_the_http_contract(
    outcome: str,
    status_code: int,
    run_status: str,
) -> None:
    service = FakeService(cancel_outcome=outcome)
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/cancel"
        )

    assert response.status_code == status_code
    assert response.json()["error"] is None
    assert response.json()["data"]["query_run"]["status"] == run_status
    assert service.cancellations == ["75e24c21-416c-4bd8-a37d-68667f4ec753"]


def test_cancel_of_a_not_cancellable_run_maps_to_409() -> None:
    run = terminal_run("failed", "query_semantic_error")
    service = FakeService(
        cancel_failure=ServiceFailure(
            "query_run_not_cancellable", "The query run cannot be cancelled.", run
        )
    )
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs/75e24c21-416c-4bd8-a37d-68667f4ec753/cancel"
        )

    assert response.status_code == 409
    assert response.json()["data"] is None
    assert response.json()["error"] == {
        "code": "query_run_not_cancellable",
        "message": "The query run cannot be cancelled.",
        "query_run_id": run.id,
    }


def test_cancel_of_an_unknown_run_is_not_found() -> None:
    service = FakeService(
        cancel_failure=ServiceFailure("query_run_not_found", "Query run was not found.", None)
    )
    with client(service) as test_client:
        response = test_client.post(
            "/api/v1/query-runs/11111111-1111-4111-8111-111111111111/cancel"
        )

    assert response.status_code == 404
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "query_run_not_found"

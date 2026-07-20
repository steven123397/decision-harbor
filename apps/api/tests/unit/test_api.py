from datetime import datetime, timezone

from fastapi.testclient import TestClient

from decisionharbor.api import create_app
from decisionharbor.domain import QueryColumn, QueryResult, QueryRun
from decisionharbor.service import QueryOutcome, ServiceFailure


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
    def __init__(self, failure: ServiceFailure | None = None) -> None:
        self.failure = failure

    def run(self, raw_sql: str) -> QueryOutcome:
        if self.failure:
            raise self.failure
        return QueryOutcome(
            query_run=terminal_run(),
            result=QueryResult(
                columns=(QueryColumn("answer", "integer"),),
                rows=((1,),),
                truncated=False,
            ),
        )


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


def test_post_returns_terminal_audit_and_immediate_result() -> None:
    with client() as test_client:
        response = test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["query_run"]["status"] == "succeeded"
    assert payload["data"]["result"]["rows"] == [[1]]


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

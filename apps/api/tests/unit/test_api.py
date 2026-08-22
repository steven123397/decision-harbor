from datetime import datetime, timezone
from threading import Event
from time import monotonic

from fastapi.testclient import TestClient

import decisionharbor.api as api_module
from decisionharbor.api import create_app
from decisionharbor.domain import QueryRun, ResultSnapshot
from decisionharbor.service import ServiceFailure


RUN_ID = "75e24c21-416c-4bd8-a37d-68667f4ec753"

SNAPSHOT_PAYLOAD = '{"columns":[{"name":"answer","type":"integer"}],"rows":[[1]]}'


def make_run(status: str = "succeeded", code: str | None = None) -> QueryRun:
    now = datetime.now(timezone.utc)
    terminal = status in ("succeeded", "rejected", "failed", "cancelled")
    allowed = status != "rejected"
    return QueryRun(
        id=RUN_ID,
        raw_sql="SELECT 1",
        status=status,
        policy_decision="rejected" if status == "rejected" else ("not_evaluated" if status == "received" else "allowed"),
        policy_version="policy-v1",
        referenced_objects=() if status == "received" else ("analytics.customers",),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=1 if status == "succeeded" else None,
        result_truncated=False if status == "succeeded" else None,
        error_code=code,
        error_summary="Safe error summary." if code else None,
        created_at=now,
        started_at=now if status not in ("received", "rejected", "queued") else None,
        finished_at=now if terminal else None,
        duration_ms=4 if terminal else None,
    )


def make_snapshot() -> ResultSnapshot:
    return ResultSnapshot(
        run_id=RUN_ID,
        payload=SNAPSHOT_PAYLOAD,
        truncated=False,
        row_count=1,
        byte_size=len(SNAPSHOT_PAYLOAD.encode()),
        created_at=datetime.now(timezone.utc),
    )


class FakeService:
    def __init__(self, failure: ServiceFailure | None = None, status: str = "queued") -> None:
        self.failure = failure
        self.status = status

    def submit(self, raw_sql: str) -> QueryRun:
        if self.failure:
            raise self.failure
        return make_run(self.status)


class FakeRepository:
    def __init__(
        self,
        *,
        fail_get: bool = False,
        run: QueryRun | None = None,
        snapshot: ResultSnapshot | None = None,
    ) -> None:
        self.fail_get = fail_get
        self.run = run if run is not None else make_run()
        self.snapshot = snapshot

    def get(self, run_id: str) -> QueryRun | None:
        if self.fail_get:
            raise RuntimeError("database DSN must not escape")
        return self.run if run_id == RUN_ID else None

    def get_result_snapshot(self, run_id: str):
        if self.fail_get:
            raise RuntimeError("database DSN must not escape")
        return self.snapshot if run_id == RUN_ID else None


def client(
    service: FakeService | None = None,
    ready: bool = True,
    repository: FakeRepository | None = None,
) -> TestClient:
    app = create_app(
        service=service or FakeService(),
        repository=repository or FakeRepository(),
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


def test_post_returns_202_and_the_queued_run() -> None:
    with client() as test_client:
        response = test_client.post("/api/v1/query-runs", json={"sql": "SELECT 1"})

    assert response.status_code == 202
    payload = response.json()
    assert payload["error"] is None
    assert payload["data"]["query_run"]["status"] == "queued"
    assert set(payload["data"]) == {"query_run"}


def test_policy_rejection_uses_stable_http_mapping_and_run_id() -> None:
    run = make_run("rejected", "sql_object_not_allowed")
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
        found = test_client.get(f"/api/v1/query-runs/{RUN_ID}")
        missing = test_client.get("/api/v1/query-runs/11111111-1111-4111-8111-111111111111")

    assert found.status_code == 200
    assert set(found.json()["data"]) == {"query_run"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "query_run_not_found"


def test_get_maps_audit_store_failure_to_safe_envelope() -> None:
    with client(repository=FakeRepository(fail_get=True)) as test_client:
        response = test_client.get(f"/api/v1/query-runs/{RUN_ID}")

    assert response.status_code == 503
    assert response.json() == {
        "data": None,
        "error": {
            "code": "audit_unavailable",
            "message": "The audit store is unavailable.",
        },
    }
    assert "DSN" not in response.text


def test_get_result_returns_the_snapshot_when_readable() -> None:
    repository = FakeRepository(run=make_run("succeeded"), snapshot=make_snapshot())
    with client(repository=repository) as test_client:
        response = test_client.get(f"/api/v1/query-runs/{RUN_ID}/result")

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


def test_get_result_is_not_ready_before_the_run_reaches_a_terminal_state() -> None:
    for status in ("received", "queued", "running", "cancelling"):
        repository = FakeRepository(run=make_run(status))
        with client(repository=repository) as test_client:
            response = test_client.get(f"/api/v1/query-runs/{RUN_ID}/result")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "result_not_ready"
        assert response.json()["error"]["query_run_id"] == RUN_ID


def test_get_result_is_unavailable_for_non_success_terminals_and_missing_snapshots() -> None:
    for repository in (
        FakeRepository(run=make_run("failed", "query_semantic_error")),
        FakeRepository(run=make_run("rejected", "sql_object_not_allowed")),
        FakeRepository(run=make_run("succeeded"), snapshot=None),
    ):
        with client(repository=repository) as test_client:
            response = test_client.get(f"/api/v1/query-runs/{RUN_ID}/result")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "result_unavailable"


def test_get_result_for_unknown_run_is_not_found() -> None:
    with client() as test_client:
        response = test_client.get("/api/v1/query-runs/11111111-1111-4111-8111-111111111111/result")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "query_run_not_found"


def test_get_result_maps_audit_store_failure_to_safe_envelope() -> None:
    with client(repository=FakeRepository(fail_get=True)) as test_client:
        response = test_client.get(f"/api/v1/query-runs/{RUN_ID}/result")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "audit_unavailable"
    assert "DSN" not in response.text

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from decisionharbor_api.api import create_app
from decisionharbor_api.domain import QueryResult, QueryRunResponse, RunState


class FakeService:
    def __init__(self, response: QueryRunResponse):
        self.response = response
        self.submitted: list[str] = []

    def submit(self, sql: str) -> QueryRunResponse:
        self.submitted.append(sql)
        return self.response

    def get(self, run_id: str) -> QueryRunResponse | None:
        return self.response if run_id == self.response.run_id else None


class FakeReadiness:
    def __init__(self, ready: bool = True):
        self.ready = ready

    def check(self) -> bool:
        return self.ready


def _response(state: RunState = RunState.SUCCEEDED) -> QueryRunResponse:
    result = QueryResult(
        columns=({"name": "region", "type": "text"},),
        rows=(("North",),),
        row_count=1,
        duration_ms=2,
    ) if state is RunState.SUCCEEDED else None
    return QueryRunResponse(
        run_id="00000000-0000-0000-0000-000000000001",
        raw_sql="SELECT region FROM analytics.customers",
        state=state,
        outcome=state,
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        policy_decision="allowed" if state is not RunState.REJECTED else "rejected",
        policy_code="object_not_allowed" if state is RunState.REJECTED else None,
        row_count=result.row_count if result else None,
        duration_ms=2,
        result=result,
        error_code="object_not_allowed" if state is RunState.REJECTED else None,
        error_message="Query references an unauthorized object" if state is RunState.REJECTED else None,
    )


def test_health_is_live_without_database_readiness() -> None:
    service = FakeService(_response())
    client = TestClient(create_app(service=service, readiness=FakeReadiness(False)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_dependency_failure_with_stable_error() -> None:
    client = TestClient(create_app(service=FakeService(_response()), readiness=FakeReadiness(False)))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "service_not_ready", "message": "Database migrations are not ready"}}


def test_query_routes_return_success_and_rejection_as_recorded_results() -> None:
    success_service = FakeService(_response())
    success_client = TestClient(create_app(service=success_service, readiness=FakeReadiness()))
    success = success_client.post("/api/v1/query-runs", json={"sql": "SELECT region FROM analytics.customers"})

    assert success.status_code == 200
    assert success.json()["state"] == "succeeded"
    assert success.json()["result"]["rows"] == [["North"]]
    assert success_service.submitted == ["SELECT region FROM analytics.customers"]

    rejected = TestClient(create_app(service=FakeService(_response(RunState.REJECTED)), readiness=FakeReadiness())).post(
        "/api/v1/query-runs", json={"sql": "SELECT * FROM platform.query_runs"}
    )

    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["error"]["code"] == "object_not_allowed"
    assert rejected.json()["result"] is None


def test_invalid_payload_and_unknown_run_have_stable_http_errors() -> None:
    client = TestClient(create_app(service=FakeService(_response()), readiness=FakeReadiness()))

    invalid = client.post("/api/v1/query-runs", json={"sql": 7})
    missing = client.get("/api/v1/query-runs/does-not-exist")

    assert invalid.status_code == 400
    assert invalid.json() == {"error": {"code": "invalid_request", "message": "Request body is invalid"}}
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "query_run_not_found", "message": "Query run was not found"}}

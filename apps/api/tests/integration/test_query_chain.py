import json
import os
import socket
from time import monotonic, sleep
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.config import Settings
from decisionharbor.domain import RESULT_MAX_BYTES
from decisionharbor.repository import QueryRunRepository


pytestmark = pytest.mark.integration


ASYNC_LIFECYCLE = {"queued", "running", "succeeded"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}

# Analytics accepts at most 1664 target list entries, and the sort key of the
# `ORDER BY` below counts as one of them. Row width, not row count, is what
# brings a policy-allowed query up against the snapshot byte budget.
WIDE_TARGET_LIST = ", ".join(f"o.order_no AS c{index}" for index in range(1_663))


def snapshot_bytes(columns: object, rows: object) -> bytes:
    """The snapshot as the external contract measures it: compact UTF-8 JSON."""
    return json.dumps(
        {"columns": columns, "rows": rows},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def platform_admin_url() -> str:
    return os.environ["TEST_ADMIN_DATABASE_URL"].rsplit("/", 1)[0] + "/platform"


def submit_and_abandon_connection(raw_sql: str) -> str:
    """Post a query run and leave before the response can be delivered.

    The client never learns the run identity from the response, so the only way
    to read the run back is by the identifier the platform recorded.
    """
    base = urlsplit(os.environ["API_BASE_URL"])
    body = json.dumps({"sql": raw_sql}).encode()
    request = (
        "POST /api/v1/query-runs HTTP/1.1\r\n"
        f"Host: {base.netloc}\r\n"
        "content-type: application/json\r\n"
        f"content-length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body
    with socket.create_connection((base.hostname or "api", base.port or 80), timeout=10) as connection:
        connection.sendall(request)
    return wait_for_run_id(raw_sql)


def wait_for_run_id(raw_sql: str) -> str:
    deadline = monotonic() + 30
    while monotonic() < deadline:
        with psycopg.connect(platform_admin_url()) as connection:
            row = connection.execute(
                "SELECT id FROM query_runs WHERE raw_sql = %s", (raw_sql,)
            ).fetchone()
        if row is not None:
            return str(row[0])
        sleep(0.1)
    raise AssertionError(f"no query run was recorded for {raw_sql}")


def wait_for_terminal(run_id: str) -> dict:
    deadline = monotonic() + 30
    while monotonic() < deadline:
        with urlopen(f"{os.environ['API_BASE_URL']}/api/v1/query-runs/{run_id}", timeout=5) as response:
            run = json.loads(response.read())["data"]["query_run"]
        if run["status"] in TERMINAL_STATUSES:
            return run
        sleep(0.1)
    raise AssertionError(f"query run {run_id} never reached a terminal state")


def read_snapshot(run_id: str) -> tuple[object, object, bool] | None:
    with psycopg.connect(platform_admin_url()) as connection:
        return connection.execute(
            """
            SELECT result_columns, result_rows, truncated
            FROM query_run_results WHERE query_run_id = %s
            """,
            (run_id,),
        ).fetchone()


def test_api_is_ready_without_analytics_execution_credentials() -> None:
    assert "ANALYTICS_DATABASE_URL" not in os.environ
    app = create_runtime_app()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}
        started_at = monotonic()
        ready = client.get("/ready")

    assert monotonic() - started_at < 1.0
    assert ready.status_code == 200
    assert ready.json() == {"data": {"status": "ready"}, "error": None}


def test_allowed_query_is_queued_without_returning_a_result() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        submitted = client.post(
            "/api/v1/query-runs",
            json={"sql": "SELECT count(*) AS customer_count FROM customers"},
        )

        assert submitted.status_code == 202
        payload = submitted.json()
        assert payload["error"] is None
        run = payload["data"]["query_run"]
        assert run["status"] == "queued"
        assert run["referenced_objects"] == ["analytics.customers"]
        assert run["execution_attempt_count"] == 0
        assert set(payload["data"]) == {"query_run"}

        audit = client.get(f"/api/v1/query-runs/{run['id']}")

    assert audit.status_code == 200
    audit_run = audit.json()["data"]["query_run"]
    assert audit_run["status"] in ASYNC_LIFECYCLE
    assert audit_run["policy_decision"] == "allowed"
    assert audit_run["referenced_objects"] == ["analytics.customers"]


def test_submitted_run_reaches_success_after_the_submit_connection_is_abandoned() -> None:
    alias = f"abandoned_{uuid4().hex}"
    raw_sql = f"SELECT count(*) AS {alias} FROM customers"

    run_id = submit_and_abandon_connection(raw_sql)

    facts = wait_for_terminal(run_id)

    assert facts["raw_sql"] == raw_sql
    assert facts["status"] == "succeeded"
    assert facts["returned_row_count"] == 1
    assert facts["result_truncated"] is False
    assert facts["started_at"] is not None
    assert facts["finished_at"] is not None
    assert facts["duration_ms"] is not None
    assert facts["attempt_worker_id"]
    assert facts["execution_attempt_count"] == 1
    assert facts["error_code"] is None

    snapshot = read_snapshot(run_id)
    assert snapshot is not None
    assert snapshot[0] == [{"name": alias, "type": "bigint"}]
    assert snapshot[1] == [["100"]]
    assert snapshot[2] is False


def test_a_wide_result_is_truncated_by_the_byte_budget_before_the_row_budget() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        submitted = client.post(
            "/api/v1/query-runs",
            json={"sql": f"SELECT {WIDE_TARGET_LIST} FROM orders o ORDER BY o.id"},
        )

        assert submitted.status_code == 202
        run_id = submitted.json()["data"]["query_run"]["id"]

    facts = wait_for_terminal(run_id)
    columns, rows, truncated = read_snapshot(run_id)

    assert facts["status"] == "succeeded"
    assert facts["result_truncated"] is True
    assert 0 < facts["returned_row_count"] < 500
    assert truncated is True
    assert len(rows) == facts["returned_row_count"]
    assert len(snapshot_bytes(columns, rows)) <= RESULT_MAX_BYTES
    assert len(snapshot_bytes(columns, rows + [rows[0]])) > RESULT_MAX_BYTES


def test_unknown_query_run_identifier_is_not_found() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/query-runs/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {
        "data": None,
        "error": {"code": "query_run_not_found", "message": "Query run was not found."},
    }


@pytest.mark.parametrize(
    "raw_sql",
    [
        "SELECT 'maintenance.dataset_seeds'::regclass::text",
        "SELECT 'harbor_admin'::regrole::text",
        "SELECT 'count'::regproc::text",
        "SELECT 'count(integer)'::regprocedure::text",
        "SELECT '='::regoper::text",
        "SELECT '=(integer,integer)'::regoperator::text",
        "SELECT 'pg_catalog'::regnamespace::text",
        "SELECT 'pg_catalog.int4'::regtype::text",
    ],
)
def test_real_api_rejects_catalog_resolving_casts_before_execution(raw_sql: str) -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/query-runs", json={"sql": raw_sql})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sql_object_not_allowed"
    assert response.json()["data"]["query_run"]["status"] == "rejected"
    assert response.json()["data"]["query_run"]["referenced_objects"] == []


def test_startup_recovery_closes_interrupted_audit_records() -> None:
    settings = Settings.from_env()
    repository = QueryRunRepository(settings.platform_database_url)
    received = repository.reserve("SELECT 1", "policy-v1", 5_000, 500).run

    assert repository.recover_interrupted() >= 1

    recovered = repository.get(received.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_code == "execution_interrupted"

from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import rows
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.domain import IDEMPOTENCY_KEY_MAX_LENGTH, submit_request_fingerprint


pytestmark = pytest.mark.integration


ALLOWED_SQL = "SELECT count(*) AS customer_count FROM customers"
OTHER_ALLOWED_SQL = "SELECT region FROM customers"
REJECTED_SQL = "SELECT * FROM secrets"


def platform_admin_url() -> str:
    return os.environ["TEST_ADMIN_DATABASE_URL"].rsplit("/", 1)[0] + "/platform"


def count_rows(statement: str) -> int:
    with psycopg.connect(platform_admin_url()) as connection:
        return connection.execute(statement).fetchone()[0]


def query_run_count() -> int:
    return count_rows("SELECT count(*) FROM query_runs")


def idempotency_record_count() -> int:
    return count_rows("SELECT count(*) FROM query_run_idempotency")


def submit_records(key: str) -> list[dict[str, object]]:
    with psycopg.connect(platform_admin_url(), row_factory=rows.dict_row) as connection:
        return connection.execute(
            """
            SELECT scope, idempotency_key, request_fingerprint, query_run_id
            FROM query_run_idempotency
            WHERE idempotency_key = %s
            """,
            (key,),
        ).fetchall()


def test_identical_replay_returns_the_same_queued_run() -> None:
    key = f"submit-{uuid4()}"
    app = create_runtime_app()
    with TestClient(app) as client:
        before = query_run_count()
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": ALLOWED_SQL},
            headers={"Idempotency-Key": key},
        )
        second = client.post(
            "/api/v1/query-runs",
            json={"sql": ALLOWED_SQL},
            headers={"Idempotency-Key": key},
        )

    run_id = first.json()["data"]["query_run"]["id"]
    assert first.status_code == 202
    assert first.json()["data"]["query_run"]["status"] == "queued"
    assert set(first.json()["data"]) == {"query_run"}
    assert second.status_code == 202
    assert second.json()["error"] is None
    assert set(second.json()["data"]) == {"query_run"}
    assert second.json()["data"]["query_run"]["id"] == run_id
    assert second.json()["data"]["query_run"]["status"] == "queued"
    assert query_run_count() == before + 1

    records = submit_records(key)
    assert len(records) == 1
    assert records[0]["scope"] == "submit"
    assert records[0]["request_fingerprint"] == submit_request_fingerprint(ALLOWED_SQL)
    assert str(records[0]["query_run_id"]) == run_id


def test_same_key_with_different_sql_conflicts_without_creating_a_run() -> None:
    key = f"submit-{uuid4()}"
    app = create_runtime_app()
    with TestClient(app) as client:
        before = query_run_count()
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": ALLOWED_SQL},
            headers={"Idempotency-Key": key},
        )
        conflict = client.post(
            "/api/v1/query-runs",
            json={"sql": OTHER_ALLOWED_SQL},
            headers={"Idempotency-Key": key},
        )
        accepted = client.get(f"/api/v1/query-runs/{first.json()['data']['query_run']['id']}")

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {
        "data": None,
        "error": {
            "code": "idempotency_conflict",
            "message": "The idempotency key was already used with a different request.",
        },
    }
    assert query_run_count() == before + 1
    assert accepted.json()["data"]["query_run"]["raw_sql"] == ALLOWED_SQL
    assert len(submit_records(key)) == 1


def test_rejected_submit_replays_the_same_rejected_run() -> None:
    key = f"submit-{uuid4()}"
    app = create_runtime_app()
    with TestClient(app) as client:
        before = query_run_count()
        first = client.post(
            "/api/v1/query-runs",
            json={"sql": REJECTED_SQL},
            headers={"Idempotency-Key": key},
        )
        second = client.post(
            "/api/v1/query-runs",
            json={"sql": REJECTED_SQL},
            headers={"Idempotency-Key": key},
        )

    run_id = first.json()["data"]["query_run"]["id"]
    assert first.status_code == 422
    assert first.json()["error"]["code"] == "sql_object_not_allowed"
    assert first.json()["error"]["query_run_id"] == run_id
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "sql_object_not_allowed"
    assert second.json()["error"]["query_run_id"] == run_id
    assert second.json()["data"]["query_run"]["status"] == "rejected"
    assert query_run_count() == before + 1
    assert len(submit_records(key)) == 1


def test_concurrent_replays_of_one_key_create_a_single_run() -> None:
    key = f"submit-{uuid4()}"
    app = create_runtime_app()

    def submit() -> object:
        return client.post(
            "/api/v1/query-runs",
            json={"sql": ALLOWED_SQL},
            headers={"Idempotency-Key": key},
        )

    with TestClient(app) as client:
        before = query_run_count()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: submit(), range(2)))

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["query_run"]["id"] == second.json()["data"]["query_run"]["id"]
    assert query_run_count() == before + 1
    assert len(submit_records(key)) == 1


def test_submits_without_a_key_create_a_run_per_request() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        before_runs = query_run_count()
        before_keys = idempotency_record_count()
        first = client.post("/api/v1/query-runs", json={"sql": ALLOWED_SQL})
        second = client.post("/api/v1/query-runs", json={"sql": ALLOWED_SQL})

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["query_run"]["id"] != second.json()["data"]["query_run"]["id"]
    assert query_run_count() == before_runs + 2
    assert idempotency_record_count() == before_keys


@pytest.mark.parametrize("key", ["", "k" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1), "key with spaces"])
def test_invalid_idempotency_key_is_rejected_without_creating_a_run(key: str) -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        before_runs = query_run_count()
        before_keys = idempotency_record_count()
        response = client.post(
            "/api/v1/query-runs",
            json={"sql": ALLOWED_SQL},
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
    assert query_run_count() == before_runs
    assert idempotency_record_count() == before_keys

"""结果快照大小预算矩阵的真实数据库集成证据。"""

import json
import os

from fastapi.testclient import TestClient
import pytest

from decisionharbor.api import create_runtime_app
from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor
from decisionharbor.repository import QueryRunRepository
from decisionharbor.snapshots import SNAPSHOT_MAX_BYTES, SNAPSHOT_MAX_ROWS
from decisionharbor.worker import QueryWorker

from conftest import worker_settings


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]


def repository() -> QueryRunRepository:
    return QueryRunRepository(os.environ["PLATFORM_DATABASE_URL"])


def executor() -> PostgresQueryExecutor:
    return PostgresQueryExecutor(os.environ["ANALYTICS_DATABASE_URL"], 1)


def enqueue(raw_sql: str) -> str:
    run = repository().create(raw_sql, "policy-v1", 30_000, SNAPSHOT_MAX_ROWS)
    queued = repository().transition(run.id, "received", status="queued", policy_decision="allowed")
    return queued.id


def run_to_completion(worker: QueryWorker, raw_sql: str):
    run_id = enqueue(raw_sql)
    assert worker.run_once() is True
    return repository().get(run_id)


# 快照 payload 的固定前缀/后缀与单行 JSON 开销：{"columns":[...],"rows":[ 与 ]} 与 [""]
PREFIX_BYTES = len('{"columns":[{"name":"value","type":"text"}],"rows":[')
SUFFIX_BYTES = len("]}")
ROW_OVERHEAD = len('[""]')


def expected_row_bytes(content: str) -> int:
    return ROW_OVERHEAD + len(content.encode("utf-8"))


def test_executor_exactly_one_mib_succeeds_and_round_trips() -> None:
    # 两行使快照恰好 1 MiB：前缀 + 行1 + 逗号 + 行2 + 后缀 = SNAPSHOT_MAX_BYTES。
    first_len = 100
    second_len = (
        SNAPSHOT_MAX_BYTES
        - PREFIX_BYTES
        - SUFFIX_BYTES
        - expected_row_bytes("a" * first_len)
        - 1
        - expected_row_bytes("")
    )
    sql = (
        "SELECT 'a'::text || repeat('a', {first}) AS value UNION ALL "
        "SELECT 'b'::text || repeat('b', {second}) ORDER BY value"
    ).format(first=first_len - 1, second=second_len - 1)
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert snapshot.byte_size == SNAPSHOT_MAX_BYTES
    assert snapshot.row_count == 2
    assert snapshot.truncated is False
    payload = json.loads(snapshot.payload)
    assert payload["rows"][0][0] == "a" * first_len
    assert payload["rows"][1][0] == "b" * second_len


def test_executor_one_byte_over_one_mib_keeps_the_stable_prefix() -> None:
    first_len = 100
    second_len = (
        SNAPSHOT_MAX_BYTES
        - PREFIX_BYTES
        - SUFFIX_BYTES
        - expected_row_bytes("a" * first_len)
        - 1
        - expected_row_bytes("")
        + 1
    )
    sql = (
        "SELECT 'a'::text || repeat('a', {first}) AS value UNION ALL "
        "SELECT 'b'::text || repeat('b', {second}) ORDER BY value"
    ).format(first=first_len - 1, second=second_len - 1)
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert snapshot.row_count == 1
    assert snapshot.truncated is True
    assert snapshot.byte_size == PREFIX_BYTES + expected_row_bytes("a" * first_len) + SUFFIX_BYTES
    payload = json.loads(snapshot.payload)
    assert payload["rows"] == [["a" * first_len]]


def test_executor_exactly_500_rows_succeeds() -> None:
    sql = "SELECT id::text AS value FROM order_items ORDER BY id LIMIT 500"
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert snapshot.row_count == 500
    assert snapshot.truncated is False
    rows = json.loads(snapshot.payload)["rows"]
    assert rows[0] == ["1"]
    assert rows[-1] == ["500"]


def test_executor_501_rows_truncates_to_the_first_500_in_order() -> None:
    sql = "SELECT id::text AS value FROM order_items ORDER BY id LIMIT 501"
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert snapshot.row_count == 500
    assert snapshot.truncated is True
    rows = json.loads(snapshot.payload)["rows"]
    assert rows[0] == ["1"]
    assert rows[-1] == ["500"]


def test_first_row_pushing_past_one_mib_fails_with_result_too_large() -> None:
    # 行本身未超过 1 MiB，但加入首行后快照整体超过 1 MiB。
    first_len = SNAPSHOT_MAX_BYTES - PREFIX_BYTES - SUFFIX_BYTES - ROW_OVERHEAD + 1
    sql = "SELECT 'a'::text || repeat('a', {length}) AS value".format(length=first_len - 1)

    with pytest.raises(ExecutionFailure) as caught:
        executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert caught.value.code == "result_too_large"


def test_single_row_over_one_mib_fails_with_result_too_large() -> None:
    sql = "SELECT repeat('a', {length}) AS value".format(length=SNAPSHOT_MAX_BYTES + 10)

    with pytest.raises(ExecutionFailure) as caught:
        executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert caught.value.code == "result_too_large"


def test_multibyte_content_counts_bytes_and_round_trips_losslessly() -> None:
    # 每行 60,000 个三字节字符 = 180,000 字节；第 6 行使快照超过 1 MiB。
    sql = "SELECT repeat('析', 60000) AS value FROM generate_series(1, 10)"
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    assert snapshot.truncated is True
    payload = json.loads(snapshot.payload)
    assert snapshot.byte_size == len(snapshot.payload.encode("utf-8"))
    assert len(payload["rows"]) == 5
    for row in payload["rows"]:
        assert row[0] == "析" * 60_000


def test_snapshot_values_follow_adr_0007_type_rules() -> None:
    import re

    sql = (
        "SELECT p.active AS flag, 7 AS amount, p.id::bigint AS big, p.list_price AS price, "
        "p.name AS label, o.ordered_at AS moment, o.ordered_at::date AS day, "
        "o.order_no AS reference, NULL AS missing "
        "FROM products p, orders o ORDER BY p.id, o.id LIMIT 1"
    )
    snapshot = executor().execute(sql, 30_000, SNAPSHOT_MAX_ROWS)

    payload = json.loads(snapshot.payload)
    assert [column["type"] for column in payload["columns"]] == [
        "boolean",
        "integer",
        "bigint",
        "numeric",
        "character varying",
        "timestamp with time zone",
        "date",
        "character varying",
        "text",
    ]
    row = payload["rows"][0]
    assert isinstance(row[0], bool)
    assert row[1] == 7
    assert isinstance(row[2], str) and row[2] == "1"
    assert isinstance(row[3], str)
    assert isinstance(row[4], str)
    # 时间戳与日期是 ISO 8601 字符串，且日期取自同一时间戳的年月日。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", row[5])
    assert row[6] == row[5][:10]
    assert isinstance(row[7], str)
    assert row[8] is None


def test_worker_publishes_succeeded_and_result_atomically() -> None:
    worker = QueryWorker(worker_settings())
    run = run_to_completion(worker, "SELECT id::text AS value FROM order_items ORDER BY id LIMIT 3")

    assert run is not None and run.status == "succeeded"
    assert run.returned_row_count == 3
    assert run.result_truncated is False
    snapshot = repository().get_result_snapshot(run.id)
    assert snapshot is not None
    # 终态可见即可读：两行事实来自同一事务。
    assert json.loads(snapshot.payload)["rows"] == [["1"], ["2"], ["3"]]


def test_result_too_large_run_leaves_no_partial_snapshot() -> None:
    worker = QueryWorker(worker_settings())
    sql = "SELECT repeat('a', {length}) AS value FROM generate_series(1, 3)".format(
        length=SNAPSHOT_MAX_BYTES + 10
    )
    run = run_to_completion(worker, sql)

    assert run is not None and run.status == "failed"
    assert run.error_code == "result_too_large"
    assert repository().get_result_snapshot(run.id) is None
    assert run.returned_row_count is None


def test_api_serves_the_truncated_prefix_result() -> None:
    # 100 × 1000 行 60 KiB 文本使字节预算先于行数上限耗尽；SQL 需通过 AST 策略。
    worker = QueryWorker(worker_settings())
    sql = "SELECT '{literal}' AS value FROM customers, orders".format(literal="a" * 60_000)
    app = create_runtime_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/query-runs", json={"sql": sql})
        assert response.status_code == 202
        run_id = response.json()["data"]["query_run"]["id"]
        assert worker.run_once() is True

        facts = client.get(f"/api/v1/query-runs/{run_id}").json()["data"]["query_run"]
        result = client.get(f"/api/v1/query-runs/{run_id}/result")

    assert facts["status"] == "succeeded"
    assert facts["result_truncated"] is True
    assert result.status_code == 200
    body = result.json()["data"]["result"]
    assert body["truncated"] is True
    assert len(body["rows"]) == 17
    assert body["rows"][0][0] == "a" * 60_000

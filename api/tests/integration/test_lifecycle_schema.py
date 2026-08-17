"""v0.2.0 生命周期数据模型（expand 阶段）集成测试。

只测外部可观察事实：迁移后的 platform 库结构（列、约束、索引、权限）
与既有 v0.1.0 行为正交——行为不变由其余集成/浏览器测试继续证明。
"""

from __future__ import annotations

import os

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.runs.models import (
    STATE_CANCELLED,
    STATE_CANCELLING,
    STATE_FAILED,
    STATE_QUEUED,
    STATE_RECEIVED,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
)

PLATFORM_APP_URL = os.environ["PLATFORM_APP_URL"]

ASYNC_STATES = [
    STATE_RECEIVED,
    STATE_QUEUED,
    STATE_RUNNING,
    STATE_CANCELLING,
    STATE_CANCELLED,
    STATE_SUCCEEDED,
    STATE_FAILED,
    STATE_REJECTED,
]

# psycopg 原生 DSN：违规断言与目录查询直接走 psycopg，不经 SQLAlchemy 包装。
APP_DSN = PLATFORM_APP_URL.replace("+psycopg", "")


@pytest.fixture()
def engine():
    eng = create_engine(PLATFORM_APP_URL)
    yield eng
    eng.dispose()


def _columns(table):
    # 走原生 psycopg（以 platform_app 身份）查目录，测试约束对低权限身份生效。
    with psycopg.connect(APP_DSN) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = %s",
            (table,),
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def test_lifecycle_columns_present():
    cols = _columns("query_runs")
    for name in ("idempotency_key", "retry_of", "queued_at", "started_at", "worker_id", "lease_expires_at"):
        assert name in cols, name
        assert cols[name][1] == "YES", name
    for name in ("attempt", "generation"):
        assert name in cols, name
        assert cols[name][1] == "NO", name
        assert cols[name][0] == "integer", name
    for name in ("queued_at", "started_at", "lease_expires_at"):
        assert cols[name][0] == "timestamp with time zone", name


def test_defaults_apply_without_explicit_values(engine):
    # 省略 attempt/generation 的插入由 server_default 兜底（存量行回填
    # 走同一路径）；单事务回滚，不留审计噪音。
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text("INSERT INTO query_runs (state, sql) VALUES ('succeeded', 'SELECT 1')")
        )
        row = conn.execute(
            text(
                "SELECT attempt, generation FROM query_runs "
                "WHERE sql = 'SELECT 1' AND state = 'succeeded' ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
        trans.rollback()
    assert row is not None
    assert row[0] == 1
    assert row[1] == 0


def test_state_check_accepts_all_async_states():
    # 八种状态都能落库（单事务插入后整体回滚，不留审计噪音）。
    with psycopg.connect(APP_DSN) as conn:
        for state in ASYNC_STATES:
            conn.execute(
                "INSERT INTO query_runs (state, sql) VALUES (%s, 'SELECT 1')",
                (state,),
            )
        conn.rollback()


def test_state_check_rejects_unknown_state():
    with psycopg.connect(APP_DSN) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO query_runs (state, sql) VALUES ('paused', 'SELECT 1')"
            )
        conn.rollback()


def test_idempotency_key_unique():
    with psycopg.connect(APP_DSN) as conn:
        conn.execute(
            "INSERT INTO query_runs (state, sql, idempotency_key) "
            "VALUES ('received', 'SELECT 1', 'schema-test-key')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO query_runs (state, sql, idempotency_key) "
                "VALUES ('received', 'SELECT 2', 'schema-test-key')"
            )
        conn.rollback()


def test_snapshot_table_shape_and_fk():
    cols = _columns("query_run_snapshots")
    for name in (
        "run_id",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "size_bytes",
        "created_at",
        "expires_at",
    ):
        assert name in cols, name
    assert cols["run_id"][0] == "bigint"
    assert cols["expires_at"][0] == "timestamp with time zone"
    assert cols["row_count"][1] == "NO"
    assert cols["expires_at"][1] == "NO"

    with psycopg.connect(APP_DSN) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO query_run_snapshots "
                "(run_id, columns, rows, row_count, truncated, size_bytes, expires_at) "
                "VALUES (999999999, '[]', '[]', 0, false, 0, now())"
            )
        conn.rollback()


def test_platform_app_full_dml_on_snapshots():
    with psycopg.connect(APP_DSN) as conn:
        run_id = conn.execute(
            "INSERT INTO query_runs (state, sql) VALUES ('succeeded', 'SELECT 1') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO query_run_snapshots "
            "(run_id, columns, rows, row_count, truncated, size_bytes, expires_at) "
            "VALUES (%s, %s, %s, 1, false, 10, now() + interval '24 hours')",
            (run_id, '[{"name":"region","type":"text"}]', '[["north"]]'),
        )
        got = conn.execute(
            "SELECT rows FROM query_run_snapshots WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        assert got == [["north"]]
        conn.execute("DELETE FROM query_run_snapshots WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM query_runs WHERE id = %s", (run_id,))
        conn.commit()


def test_retry_of_fk_to_query_runs():
    with psycopg.connect(APP_DSN) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO query_runs (state, sql, attempt, retry_of) "
                "VALUES ('received', 'SELECT 1', 1, 999999999)"
            )
        conn.rollback()

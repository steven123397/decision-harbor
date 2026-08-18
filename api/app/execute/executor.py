"""只读执行器：psycopg 直连 analytics，服务端游标流式取数、超时与行数上限。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg

QY_TIMEOUT = "QY_TIMEOUT"
QY_ANALYTICS_UNAVAILABLE = "QY_ANALYTICS_UNAVAILABLE"
QY_EXECUTION_ERROR = "QY_EXECUTION_ERROR"
QY_ROW_TOO_LARGE = "QY_ROW_TOO_LARGE"

# 快照统一字节上限：1 MiB（ADR-0011 扩展，不开放上调配置）。
SNAPSHOT_MAX_BYTES = 1_048_576

# 快照 JSON 结构（包裹键名、行间分隔符）的最坏固定开销：500 行 × 逗号
# 1 字节 + 键与括号，按 2 KiB 预留。取数预算 = SNAPSHOT_MAX_BYTES − 它，
# 保证「取数判不超限」蕴含「落库序列化不超限」。
SNAPSHOT_STRUCT_OVERHEAD_BYTES = 2_048

# 服务端游标按块取数的批大小：小块使字节上限尽早生效，也压低
# 单批物化内存。
FETCH_BATCH_ROWS = 256


class ExecutionFailure(Exception):
    """执行失败，携带稳定错误码与摘要。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[ColumnDef]
    rows: list[list]
    row_count: int
    truncated: bool
    duration_ms: int


def execute_readonly(
    query: str,
    *,
    conn: psycopg.Connection,
    max_rows: int,
    max_bytes: int = SNAPSHOT_MAX_BYTES,
) -> ExecutionResult:
    """在池中已获取的只读连接上执行一条已通过策略检查的 SQL。

    500 行与 max_bytes 并行约束取数（ADR-0011 扩展）：任一达限即停止
    取数并标记 truncated；单行自身序列化超过 max_bytes 是确定性失败
    （QY_ROW_TOO_LARGE，不自动重跑）。错误信息在此映射为稳定摘要，
    不透传数据库原始文本。
    """
    started = time.perf_counter()
    try:
        # 服务端（命名）游标：结果集留在数据库侧按块传输，
        # execute() 不会先在 API 进程内存里物化完整结果。
        with conn.cursor(name=f"query_{uuid4().hex}") as cur:
            cur.itersize = FETCH_BATCH_ROWS
            cur.execute(query)
            columns = [
                ColumnDef(name=col.name, type=_type_name(conn, col.type_code))
                for col in (cur.description or [])
            ]
            # 字节口径与 build_snapshot 同源（snapshot_json）：取数预算
            # 预留快照结构开销（见 SNAPSHOT_STRUCT_OVERHEAD_BYTES），
            # 保证「取数判不超限」蕴含「落库 size_bytes 不超限」。
            col_defs = column_dicts(columns)
            head_bytes = len(snapshot_json(col_defs).encode("utf-8"))
            budget = max_bytes - SNAPSHOT_STRUCT_OVERHEAD_BYTES
            rows: list[list] = []
            total_bytes = head_bytes
            truncated = False
            exhausted = False
            while not exhausted and len(rows) < max_rows:
                batch = cur.fetchmany(min(cur.itersize, max_rows - len(rows)))
                if not batch:
                    break
                if len(batch) < min(cur.itersize, max_rows - len(rows)):
                    # fetchmany 返回不足请求数：结果集已取尽
                    exhausted = True
                for raw in batch:
                    row = _convert_row(raw)
                    row_bytes = len(snapshot_json(row).encode("utf-8"))
                    if row_bytes > max_bytes:
                        raise ExecutionFailure(
                            QY_ROW_TOO_LARGE,
                            "单行结果超过大小上限，请缩小查询结果后重试",
                        )
                    if total_bytes + row_bytes > budget:
                        truncated = True
                        break
                    rows.append(row)
                    total_bytes += row_bytes
                if truncated:
                    break
            if not truncated and len(rows) >= max_rows:
                # 行数达限后再探一行：有剩余即截断
                more = cur.fetchmany(1)
                truncated = bool(more)
        conn.rollback()
    except psycopg.errors.QueryCanceled as exc:
        raise ExecutionFailure(
            QY_TIMEOUT, "查询执行超时，已被语句超时限制中止"
        ) from exc
    except psycopg.OperationalError as exc:
        # 连接类失败（断连、不可达）是基础设施错误，自动重试的资格
        # 由 queue.REQUEUE_ELIGIBLE_ERROR_CODES 决定；QueryCanceled 是其
        # 子类，必须先匹配。确定性数据库错误落到下面的 QY_EXECUTION_ERROR。
        raise ExecutionFailure(
            QY_ANALYTICS_UNAVAILABLE, "analytics 数据库暂不可达，请稍后重试"
        ) from exc
    except psycopg.Error as exc:
        raise ExecutionFailure(
            QY_EXECUTION_ERROR, "查询执行失败，请检查列名与表达式"
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=duration_ms,
    )


def execute_on_connection(query: str, *, conn, max_rows: int) -> ExecutionResult:
    return execute_readonly(query, conn=conn, max_rows=max_rows)


def snapshot_json(value) -> str:
    """快照字节口径的唯一实现：build_snapshot 与取数累积共用。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def column_dicts(columns: list[ColumnDef]) -> list[dict]:
    """列定义的快照序列化形状（{"name", "type"}），取数与落库共用。"""
    return [{"name": c.name, "type": c.type} for c in columns]


def _type_name(conn: psycopg.Connection, oid: int) -> str:
    try:
        return conn.adapters.types.get(oid).name or "unknown"
    except Exception:
        return "unknown"


def _convert_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _convert_row(row: tuple) -> list:
    return [_convert_value(v) for v in row]

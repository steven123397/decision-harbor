"""运行队列网关：入队、认领、终态发布与运维排水开关。

query_runs 表即队列（ADR-0015）。所有状态转移都是带条件的原子 UPDATE
（CAS）：认领用 FOR UPDATE SKIP LOCKED 避免多执行者争抢同一行，终态
发布与快照写入同事务、携带 generation 做 fencing——失去所有权的旧
执行者因代过期无法发布结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

WORKER_PAUSED_FLAG = "worker.paused"


def build_snapshot(result) -> dict:
    """执行结果 → 快照字段（列定义、行、字节数）；worker 与测试共用，
    序列化口径只有这一处。"""
    columns = [{"name": c.name, "type": c.type} for c in result.columns]
    size_bytes = len(
        json.dumps({"columns": columns, "rows": result.rows}, ensure_ascii=False).encode("utf-8")
    )
    return {"columns": columns, "rows": result.rows, "size_bytes": size_bytes}


@dataclass(frozen=True)
class Claim:
    run_id: int
    sql: str
    attempt: int
    generation: int


def mark_queued(session: Session, run_id: int) -> bool:
    """received → queued（加入调用方事务，不自行提交）。

    返回 False 表示运行已不在 received（被并发转移，如取消）。
    """
    return (
        session.execute(
            text(
                "UPDATE query_runs SET state = 'queued', queued_at = now() "
                "WHERE id = :id AND state = 'received'"
            ),
            {"id": run_id},
        ).rowcount
        == 1
    )


def finalize_rejected(session: Session, run_id: int, *, code: str, message: str) -> bool:
    """received → rejected（终态）：策略拒绝同步落定，不入队。加入调用方事务。"""
    return (
        session.execute(
            text(
                "UPDATE query_runs SET state = 'rejected', rejection_code = :code, "
                "rejection_message = :message, finished_at = now() "
                "WHERE id = :id AND state = 'received'"
            ),
            {"id": run_id, "code": code, "message": message},
        ).rowcount
        == 1
    )


def claim_next(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    lease_seconds: int,
    run_id: int | None = None,
) -> Claim | None:
    """认领最早的可执行运行：写租约、递增 generation、转入 running。

    可执行 = queued，或 running 且租约已过期（接管：原执行者失去
    所有权，接管者递增 attempt 重新执行）。FOR UPDATE SKIP LOCKED 使
    多个执行者并发认领时不争抢同一行；run_id 过滤仅供测试定位特定
    运行。未认领到则返回 None。
    """
    with session_factory() as session:
        # run_id 过滤在客户端拼接：参数化 NULL 判断会触发
        # AmbiguousParameter（PostgreSQL 无法推断 :rid 类型）。
        filter_clause = "AND id = :rid" if run_id is not None else ""
        row = session.execute(
            text(
                "SELECT id, sql, attempt, generation, state FROM query_runs "
                "WHERE (state = 'queued' OR (state = 'running' "
                f"AND lease_expires_at <= now())) {filter_clause} "
                "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"rid": run_id} if run_id is not None else {},
        ).fetchone()
        if row is None:
            return None
        # 接管（原 state=running）按 CONTEXT.md「尝试」语义递增 attempt；
        # 首次认领保持 attempt 不变。行已被锁定，state 不会并发漂移。
        takeover_bump = 1 if row.state == "running" else 0
        session.execute(
            text(
                "UPDATE query_runs SET state = 'running', worker_id = :worker, "
                "lease_expires_at = now() + make_interval(secs => :lease), "
                "generation = generation + 1, attempt = attempt + :bump, "
                "started_at = now() WHERE id = :id"
            ),
            {
                "worker": worker_id,
                "lease": lease_seconds,
                "bump": takeover_bump,
                "id": row.id,
            },
        )
        session.commit()
        return Claim(
            run_id=row.id,
            sql=row.sql,
            attempt=row.attempt + takeover_bump,
            generation=row.generation + 1,
        )


def renew_lease(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    """租约心跳续期：仍持有所有权（worker + generation + running）才生效。

    返回 False 表示执行者已失去所有权（租约过期被接管），调用方应
    停止续期；其后的终态发布同样会被 generation fencing 拒绝。
    """
    with session_factory() as session:
        updated = session.execute(
            text(
                "UPDATE query_runs SET "
                "lease_expires_at = now() + make_interval(secs => :lease) "
                "WHERE id = :id AND state = 'running' AND worker_id = :worker "
                "AND generation = :gen"
            ),
            {
                "id": claim.run_id,
                "gen": claim.generation,
                "worker": worker_id,
                "lease": lease_seconds,
            },
        ).rowcount
        session.commit()
        return updated == 1


def publish_success(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    columns: list[dict],
    rows: list[list],
    row_count: int,
    truncated: bool,
    duration_ms: int,
    size_bytes: int,
    retention_hours: int,
) -> bool:
    """running → succeeded + 快照，一个事务内的不可分割发布。

    generation 不匹配（执行者已失去所有权）时整体不生效并返回 False。
    """
    with session_factory() as session:
        try:
            updated = session.execute(
                text(
                    "UPDATE query_runs SET state = 'succeeded', row_count = :rc, "
                    "truncated = :tr, duration_ms = :dur, finished_at = now() "
                    "WHERE id = :id AND generation = :gen AND state = 'running'"
                ),
                {
                    "rc": row_count,
                    "tr": truncated,
                    "dur": duration_ms,
                    "id": claim.run_id,
                    "gen": claim.generation,
                },
            ).rowcount
            if updated != 1:
                session.rollback()
                return False
            session.execute(
                text(
                    "INSERT INTO query_run_snapshots "
                    "(run_id, columns, rows, row_count, truncated, size_bytes, expires_at) "
                    "VALUES (:rid, :cols, :rows, :rc, :tr, :size, "
                    "now() + make_interval(hours => :retention))"
                ),
                {
                    "rid": claim.run_id,
                    "cols": _json(columns),
                    "rows": _json(rows),
                    "rc": row_count,
                    "tr": truncated,
                    "size": size_bytes,
                    "retention": retention_hours,
                },
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise


def publish_failure(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    error_code: str,
    error_message: str,
) -> bool:
    """running → failed（终态），同样受 generation fencing 保护。"""
    with session_factory() as session:
        updated = session.execute(
            text(
                "UPDATE query_runs SET state = 'failed', error_code = :code, "
                "error_message = :message, finished_at = now() "
                "WHERE id = :id AND generation = :gen AND state = 'running'"
            ),
            {
                "id": claim.run_id,
                "gen": claim.generation,
                "code": error_code,
                "message": error_message,
            },
        ).rowcount
        session.commit()
        return updated == 1


def set_worker_paused(session_factory: sessionmaker[Session], *, paused: bool) -> None:
    """运维排水开关：暂停时执行者不再认领新运行（已在执行的跑完为止）。

    迁移或测试前用来安全排空队列；标记只是认领闸门，不影响已入队运行。
    """
    with session_factory() as session:
        if paused:
            session.execute(
                text(
                    "INSERT INTO system_flags (flag) VALUES (:flag) "
                    "ON CONFLICT (flag) DO NOTHING"
                ),
                {"flag": WORKER_PAUSED_FLAG},
            )
        else:
            session.execute(
                text("DELETE FROM system_flags WHERE flag = :flag"),
                {"flag": WORKER_PAUSED_FLAG},
            )
        session.commit()


def worker_paused(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        row = session.execute(
            text("SELECT 1 FROM system_flags WHERE flag = :flag LIMIT 1"),
            {"flag": WORKER_PAUSED_FLAG},
        ).fetchone()
        return row is not None


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

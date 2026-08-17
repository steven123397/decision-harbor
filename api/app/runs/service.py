"""查询运行服务：受理提交（同步治理）与读取运行。执行在 worker（ADR-0016）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.policy import PolicyLimits, evaluate
from app.runs import queue
from app.runs.models import STATE_REJECTED, QueryRun, run_to_dict


class QueryRunService:
    """POST 只做三件事：落 received 行、策略判定、入队或拒绝。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        allowed_tables: frozenset[str],
        sql_max_length: int,
    ):
        self._session_factory = session_factory
        self._limits = PolicyLimits(sql_max_length=sql_max_length)
        self._allowed_tables = allowed_tables

    def submit(self, sql: str) -> dict:
        """受理提交，返回 accepted（已入队）或 rejected（同步拒绝）。

        受理与入队/拒绝在同一事务内落定：进程在受理中途崩溃不会留下
        永远停在 received 的孤儿行。
        """
        decision = self._evaluate(sql)
        with self._session_factory() as session:
            run = QueryRun(state="received", sql=sql)
            session.add(run)
            session.flush()  # 取得 id，转移语句与插入同事务
            if decision.allowed:
                queue.mark_queued(session, run.id)
                outcome = "accepted"
            else:
                queue.finalize_rejected(
                    session, run.id, code=decision.code, message=decision.message
                )
                outcome = STATE_REJECTED
            session.commit()
            session.refresh(run)
        return {"outcome": outcome, "run": run_to_dict(run)}

    def get(self, run_id: int) -> dict | None:
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            return run_to_dict(run)

    def snapshot(self, run_id: int) -> dict | None:
        """读取运行与快照。None = 运行不存在；其余由路由层区分 409/200。"""
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            snap = session.execute(
                text(
                    "SELECT columns, rows, row_count, truncated, expires_at "
                    "FROM query_run_snapshots WHERE run_id = :rid"
                ),
                {"rid": run_id},
            ).fetchone()
            return {"run": run_to_dict(run), "snapshot": snap}

    def _evaluate(self, sql: str):
        return evaluate(sql, limits=self._limits, allowed_tables=self._allowed_tables)

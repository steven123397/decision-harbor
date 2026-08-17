"""查询运行服务：受理提交（同步治理）、幂等重放与历史分页。执行在 worker（ADR-0016）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.policy import PolicyLimits, evaluate
from app.runs import queue
from app.runs.models import STATE_REJECTED, QueryRun, run_to_dict


class QueryRunService:
    """POST 只做四件事：查键重放、落 received 行、策略判定、入队或拒绝。"""

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

    def submit(self, sql: str, idempotency_key: str | None = None) -> dict:
        """受理提交，outcome ∈ accepted / rejected / replayed / conflict。

        键的判定先于策略判定（ADR-0017）：唯一索引保证一个键终身只绑定
        一条运行记录，重放与冲突是「键已绑定什么」的事实问题——被拒绝
        的原运行同样按重放返回，不存在「同键再建一条」的路径。
        """
        if idempotency_key is not None:
            replayed = self._replay_by_key(idempotency_key, sql)
            if replayed is not None:
                return replayed
        try:
            return self._create(sql, idempotency_key)
        except IntegrityError:
            # 并发同键提交：唯一索引裁决胜负，后到者重取原运行按重放/冲突处理。
            if idempotency_key is None:
                raise
            replayed = self._replay_by_key(idempotency_key, sql)
            if replayed is None:
                raise
            return replayed

    def get(self, run_id: int) -> dict | None:
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            return run_to_dict(run)

    def list_runs(
        self, *, limit: int, cursor: tuple[datetime, int] | None
    ) -> dict:
        """历史分页：(created_at, id) 双键降序的 keyset 分页。

        双键比较使 created_at 并列时仍有稳定全序；多取一行判断是否还有
        下一页。游标编解码是协议层的事，落在路由。
        """
        with self._session_factory() as session:
            stmt = (
                select(QueryRun)
                .order_by(desc(QueryRun.created_at), desc(QueryRun.id))
                .limit(limit + 1)
            )
            if cursor is not None:
                stmt = stmt.where(tuple_(QueryRun.created_at, QueryRun.id) < tuple_(*cursor))
            runs = list(session.scalars(stmt))
        page = runs[:limit]
        next_cursor = (
            (page[-1].created_at, page[-1].id) if len(runs) > limit and page else None
        )
        return {"runs": [run_to_dict(r) for r in page], "next_cursor": next_cursor}

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

    def _create(self, sql: str, idempotency_key: str | None) -> dict:
        """创建新运行。受理与入队/拒绝在同一事务内落定：进程在受理中途
        崩溃不会留下永远停在 received 的孤儿行。"""
        decision = self._evaluate(sql)
        with self._session_factory() as session:
            run = QueryRun(state="received", sql=sql, idempotency_key=idempotency_key)
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

    def _by_key(self, idempotency_key: str) -> QueryRun | None:
        with self._session_factory() as session:
            return session.scalars(
                select(QueryRun).where(QueryRun.idempotency_key == idempotency_key)
            ).first()

    def _replay_by_key(self, idempotency_key: str, sql: str) -> dict | None:
        """按键重取原运行并裁决重放/冲突。None = 键尚未绑定任何运行。"""
        existing = self._by_key(idempotency_key)
        if existing is None:
            return None
        if existing.sql == sql:
            return {"outcome": "replayed", "run": run_to_dict(existing)}
        return {"outcome": "conflict", "run": run_to_dict(existing)}

    def _evaluate(self, sql: str):
        return evaluate(sql, limits=self._limits, allowed_tables=self._allowed_tables)

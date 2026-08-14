"""查询运行服务：编排审计记录、策略判定与只读执行。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from app.execute.executor import ExecutionFailure, execute_readonly
from app.policy import PolicyLimits, evaluate
from app.runs.models import (
    STATE_FAILED,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    QueryRun,
    run_to_dict,
)


class QueryRunService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        readonly_dsn: str,
        statement_timeout_ms: int,
        max_rows: int,
        sql_max_length: int,
        allowed_tables: frozenset[str],
    ):
        self._session_factory = session_factory
        self._readonly_dsn = readonly_dsn
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows
        self._limits = PolicyLimits(sql_max_length=sql_max_length)
        self._allowed_tables = allowed_tables

    def submit(self, sql: str) -> dict:
        """同步执行完整链路，返回统一 envelope（见 docs/design/api.md）。"""
        with self._session_factory() as session:
            run = QueryRun(state=STATE_RUNNING, sql=sql)
            session.add(run)
            session.commit()
            run_id = run.id

        decision = evaluate(
            sql, limits=self._limits, allowed_tables=self._allowed_tables
        )
        if not decision.allowed:
            run = self._finalize(
                run_id,
                state=STATE_REJECTED,
                rejection_code=decision.code,
                rejection_message=decision.message,
            )
            return {"outcome": STATE_REJECTED, "run": run_to_dict(run)}

        try:
            result = execute_readonly(
                sql,
                readonly_dsn=self._readonly_dsn,
                statement_timeout_ms=self._statement_timeout_ms,
                max_rows=self._max_rows,
            )
        except ExecutionFailure as exc:
            run = self._finalize(
                run_id,
                state=STATE_FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            return {"outcome": STATE_FAILED, "run": run_to_dict(run)}

        run = self._finalize(
            run_id,
            state=STATE_SUCCEEDED,
            row_count=result.row_count,
            truncated=result.truncated,
            duration_ms=result.duration_ms,
        )
        return {
            "outcome": STATE_SUCCEEDED,
            "run": run_to_dict(run),
            "result": {
                "columns": [
                    {"name": c.name, "type": c.type} for c in result.columns
                ],
                "rows": result.rows,
                "row_count": result.row_count,
                "truncated": result.truncated,
            },
        }

    def get(self, run_id: int) -> dict | None:
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            if run is None:
                return None
            return run_to_dict(run)

    def _finalize(self, run_id: int, **fields) -> QueryRun:
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            for key, value in fields.items():
                setattr(run, key, value)
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(run)
            return run

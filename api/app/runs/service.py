"""查询运行服务：编排审计记录、策略判定与只读执行。"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import BoundedSemaphore

from sqlalchemy.orm import Session, sessionmaker

from app.execute.executor import QY_EXECUTION_ERROR, ExecutionFailure
from app.execute.pool import ReadOnlyPool
from app.policy import PolicyLimits, evaluate
from app.runs.models import (
    STATE_FAILED,
    STATE_REJECTED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    QueryRun,
    run_to_dict,
)

QY_CAPACITY_EXCEEDED = "QY_CAPACITY_EXCEEDED"
QY_ANALYTICS_UNAVAILABLE = "QY_ANALYTICS_UNAVAILABLE"


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
        max_concurrency: int = 5,
        capacity_wait_seconds: float = 0.25,
    ):
        self._session_factory = session_factory
        self._readonly_dsn = readonly_dsn
        self._statement_timeout_ms = statement_timeout_ms
        # 池在构造时创建：池对象本身不建立连接（连接在首次 acquire 时
        # 才惰性建立，单元测试不触库），却消除多线程首请求的竞态——
        # 惰性赋值会让并发请求各自建池、acquire/release 落到不同实例。
        self._pool = ReadOnlyPool(
            readonly_dsn,
            max_size=max_concurrency,
            statement_timeout_ms=statement_timeout_ms,
        )
        self._max_rows = max_rows
        self._limits = PolicyLimits(sql_max_length=sql_max_length)
        self._allowed_tables = allowed_tables
        self._capacity = BoundedSemaphore(max_concurrency)
        self._capacity_wait_seconds = capacity_wait_seconds
        self.max_concurrency = max_concurrency

    # 供测试与 main 关闭时调用
    def close(self) -> None:
        self._pool.close()

    def submit(self, sql: str) -> dict:
        """同步执行完整链路，返回统一 envelope（见 docs/design/api.md）。"""
        run_id = self._create_run(sql)
        decision = self._evaluate(sql)

        if not decision.allowed:
            run = self._finalize(
                run_id,
                state=STATE_REJECTED,
                rejection_code=decision.code,
                rejection_message=decision.message,
            )
            return {"outcome": STATE_REJECTED, "run": run_to_dict(run)}

        if not self._capacity.acquire(timeout=self._capacity_wait_seconds):
            # 容量耗尽是执行资源问题，不是策略拒绝：按 failed 落审计，
            # 客户端才不会把资源繁忙误读成 SQL 违规。
            run = self._finalize(
                run_id,
                state=STATE_FAILED,
                error_code=QY_CAPACITY_EXCEEDED,
                error_message="查询服务当前繁忙，请稍后重试",
            )
            return {"outcome": STATE_FAILED, "run": run_to_dict(run)}

        try:
            pool = self._pool  # 固定实例：acquire 与 release 必须落在同一个池上
            try:
                conn = pool.acquire()
            except Exception:
                run = self._finalize(
                    run_id,
                    state=STATE_FAILED,
                    error_code=QY_ANALYTICS_UNAVAILABLE,
                    error_message="查询数据库暂不可用，请稍后重试",
                )
                return {"outcome": STATE_FAILED, "run": run_to_dict(run)}
            try:
                result = self._execute(sql, conn=conn)
            except ExecutionFailure as exc:
                run = self._finalize(
                    run_id,
                    state=STATE_FAILED,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                return {"outcome": STATE_FAILED, "run": run_to_dict(run)}
            except Exception:
                # 未映射异常同样收敛为稳定失败，不留 running 审计记录、不抛 500。
                run = self._finalize(
                    run_id,
                    state=STATE_FAILED,
                    error_code=QY_EXECUTION_ERROR,
                    error_message="查询执行失败，请稍后重试",
                )
                return {"outcome": STATE_FAILED, "run": run_to_dict(run)}
            finally:
                pool.release(conn)
        finally:
            self._capacity.release()

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
                "columns": [{"name": c.name, "type": c.type} for c in result.columns],
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

    # ---- 可替换接缝（测试桩在此打桩） ----

    def _evaluate(self, sql: str):
        return evaluate(sql, limits=self._limits, allowed_tables=self._allowed_tables)

    def _create_run(self, sql: str) -> int:
        with self._session_factory() as session:
            run = QueryRun(state=STATE_RUNNING, sql=sql)
            session.add(run)
            session.commit()
            return run.id

    def _execute(self, sql: str, *, conn):
        from app.execute.executor import execute_on_connection

        return execute_on_connection(
            sql, conn=conn, max_rows=self._max_rows
        )

    def _finalize(self, run_id: int, **fields) -> QueryRun:
        with self._session_factory() as session:
            run = session.get(QueryRun, run_id)
            for key, value in fields.items():
                setattr(run, key, value)
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(run)
            return run

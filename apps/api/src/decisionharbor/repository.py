import hashlib
import json

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row
from sqlalchemy.exc import IntegrityError

from decisionharbor.domain import IdempotencyRecord, QueryRun, ResultSnapshot, finished_fields


class StateConflict(RuntimeError):
    pass


class IdempotencyKeyTaken(RuntimeError):
    """并发提交同一幂等键：唯一约束已由先提交的事务占用。"""


# 提交幂等的实例级作用域；当前版本没有用户或租户。
IDEMPOTENCY_SCOPE_SUBMIT = "submit"

# 取消请求的结果口径：取消意图与终态发布的竞态由条件更新竞争后落定的稳定结论。
CANCEL_OUTCOME_CANCELLED = "cancelled"  # queued 直接取消为 cancelled 终态
CANCEL_OUTCOME_CANCELLING = "cancelling"  # running 进入 cancelling 或重复取消幂等命中
CANCEL_OUTCOME_TERMINAL = "terminal"  # cancelled/succeeded：返回既有终态事实
CANCEL_OUTCOME_NOT_CANCELLABLE = "not_cancellable"  # rejected/failed/received 不可取消
CANCEL_OUTCOME_NOT_FOUND = "not_found"

# 条件更新竞争的重读上限：运行状态只前进，数据库行锁分出先后后必然稳定。
CANCEL_RETRY_STEPS = 8


def request_fingerprint(raw_sql: str) -> str:
    """幂等重放的输入指纹：相同 SQL 才视为完全相同输入。"""
    return hashlib.sha256(raw_sql.encode("utf-8")).hexdigest()


TRANSITION_COLUMNS = frozenset(
    {
        "status",
        "policy_decision",
        "referenced_objects",
        "returned_row_count",
        "result_truncated",
        "error_code",
        "error_summary",
        "started_at",
        "finished_at",
        "duration_ms",
    }
)


class QueryRunRepository:
    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, pool_size=5, max_overflow=0, pool_pre_ping=True)

    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        idempotency_key: str | None = None,
    ) -> QueryRun:
        run = QueryRun.received(raw_sql, policy_version, statement_timeout_ms, max_rows)
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        """
                        INSERT INTO query_runs (
                            id, raw_sql, status, policy_decision, policy_version,
                            referenced_objects, statement_timeout_ms, max_rows, created_at
                        ) VALUES (
                            CAST(:id AS uuid), :raw_sql, :status, :policy_decision, :policy_version,
                            CAST(:referenced_objects AS jsonb), :statement_timeout_ms, :max_rows, :created_at
                        )
                        RETURNING *
                        """
                    ),
                    {
                        **run.__dict__,
                        "referenced_objects": json.dumps(run.referenced_objects),
                    },
                ).one()
                if idempotency_key is not None:
                    try:
                        connection.execute(
                            text(
                                """
                                INSERT INTO idempotency_keys (scope, key, request_fingerprint, run_id)
                                VALUES (:scope, :key, :request_fingerprint, CAST(:run_id AS uuid))
                                """
                            ),
                            {
                                "scope": IDEMPOTENCY_SCOPE_SUBMIT,
                                "key": idempotency_key,
                                "request_fingerprint": request_fingerprint(raw_sql),
                                "run_id": run.id,
                            },
                        )
                    except IntegrityError as exc:
                        # 只有键占用的唯一冲突才映射为幂等竞态；其他完整性
                        # 失败继续向上抛出，由服务按审计存储不可用收敛。
                        if exc.orig is not None and getattr(exc.orig, "sqlstate", None) == "23505":
                            raise IdempotencyKeyTaken() from exc
                        raise
        except IdempotencyKeyTaken:
            raise
        return _row_to_query_run(row)

    def find_idempotency(self, scope: str, key: str) -> IdempotencyRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT scope, key, request_fingerprint, run_id FROM idempotency_keys "
                    "WHERE scope = :scope AND key = :key"
                ),
                {"scope": scope, "key": key},
            ).one_or_none()
        if row is None:
            return None
        values = row._mapping
        return IdempotencyRecord(
            scope=values["scope"],
            key=values["key"],
            request_fingerprint=values["request_fingerprint"],
            run_id=str(values["run_id"]),
        )

    def transition(self, run_id: str, expected_status: str, **changes: object) -> QueryRun:
        unknown = set(changes) - TRANSITION_COLUMNS
        if unknown or "status" not in changes:
            raise ValueError(f"unsupported transition fields: {sorted(unknown)}")
        assignments: list[str] = []
        parameters = {"id": run_id, "expected_status": expected_status}
        for name, value in changes.items():
            if name == "referenced_objects":
                assignments.append(f"{name} = CAST(:{name} AS jsonb)")
                parameters[name] = json.dumps(value)
            else:
                assignments.append(f"{name} = :{name}")
                parameters[name] = value
        statement = text(
            f"""
            UPDATE query_runs
            SET {', '.join(assignments)}
            WHERE id = CAST(:id AS uuid) AND status = :expected_status
            RETURNING *
            """
        )
        with self._engine.begin() as connection:
            row = connection.execute(statement, parameters).one_or_none()
        if row is None:
            raise StateConflict("query run transition did not match expected state")
        return _row_to_query_run(row)

    def cancel(self, run_id: str) -> tuple[str, QueryRun | None]:
        """按条件更新竞争记录取消意图：queued 直接终态化，running 先进入 cancelling。

        与领取、终态发布的竞态由数据库行锁仲裁：条件更新失败即重读后按新状态处理，
        因此取消意图先落则取消获胜，成功终态先提交则迟到取消返回既有事实。
        """
        for _ in range(CANCEL_RETRY_STEPS):
            run = self.get(run_id)
            if run is None:
                return (CANCEL_OUTCOME_NOT_FOUND, None)
            if run.status == "queued":
                try:
                    cancelled = self.transition(
                        run_id, "queued", status="cancelled", **finished_fields(run)
                    )
                except StateConflict:
                    continue  # 与领取竞争：重读后按 running 记录取消意图
                return (CANCEL_OUTCOME_CANCELLED, cancelled)
            if run.status == "running":
                try:
                    cancelling = self.transition(run_id, "running", status="cancelling")
                except StateConflict:
                    continue  # 与终态发布竞争：重读后返回既有事实
                return (CANCEL_OUTCOME_CANCELLING, cancelling)
            if run.status == "cancelling":
                return (CANCEL_OUTCOME_CANCELLING, run)
            if run.status in ("cancelled", "succeeded"):
                return (CANCEL_OUTCOME_TERMINAL, run)
            return (CANCEL_OUTCOME_NOT_CANCELLABLE, run)
        raise StateConflict("cancellation could not be settled against concurrent transitions")

    def get(self, run_id: str) -> QueryRun | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM query_runs WHERE id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).one_or_none()
        return _row_to_query_run(row) if row else None

    def get_result_snapshot(self, run_id: str) -> ResultSnapshot | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM result_snapshots WHERE run_id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).one_or_none()
        return _row_to_result_snapshot(row) if row else None


def _row_to_result_snapshot(row: Row) -> ResultSnapshot:
    values = row._mapping
    return ResultSnapshot(
        run_id=str(values["run_id"]),
        payload=values["payload"],
        truncated=values["truncated"],
        row_count=values["row_count"],
        byte_size=values["byte_size"],
        created_at=values["created_at"],
    )


def _row_to_query_run(row: Row) -> QueryRun:
    values = row._mapping
    return QueryRun(
        id=str(values["id"]),
        raw_sql=values["raw_sql"],
        status=values["status"],
        policy_decision=values["policy_decision"],
        policy_version=values["policy_version"],
        referenced_objects=tuple(values["referenced_objects"]),
        statement_timeout_ms=values["statement_timeout_ms"],
        max_rows=values["max_rows"],
        returned_row_count=values["returned_row_count"],
        result_truncated=values["result_truncated"],
        error_code=values["error_code"],
        error_summary=values["error_summary"],
        created_at=values["created_at"],
        started_at=values["started_at"],
        finished_at=values["finished_at"],
        duration_ms=values["duration_ms"],
    )

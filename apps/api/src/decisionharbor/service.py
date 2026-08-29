from typing import Protocol

from decisionharbor.domain import IdempotencyRecord, QueryRun, finished_fields
from decisionharbor.policy import PolicyDecision
from decisionharbor.repository import (
    CANCEL_OUTCOME_NOT_CANCELLABLE,
    CANCEL_OUTCOME_NOT_FOUND,
    IDEMPOTENCY_SCOPE_SUBMIT,
    IdempotencyKeyTaken,
    request_fingerprint,
    retry_idempotency_scope,
)


class Policy(Protocol):
    def evaluate(self, raw_sql: str) -> PolicyDecision: ...


class Repository(Protocol):
    def create(
        self,
        raw_sql: str,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
        idempotency_key: str | None = None,
        idempotency_scope: str = IDEMPOTENCY_SCOPE_SUBMIT,
        retry_of: str | None = None,
    ) -> QueryRun: ...

    def transition(
        self,
        run_id: str,
        expected_status: str,
        **changes: object,
    ) -> QueryRun: ...

    def cancel(self, run_id: str) -> tuple[str, QueryRun | None]: ...

    def get(self, run_id: str) -> QueryRun | None: ...

    def find_idempotency(self, scope: str, key: str) -> IdempotencyRecord | None: ...


class ServiceFailure(Exception):
    def __init__(self, code: str, message: str, query_run: QueryRun | None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.query_run = query_run


def _conflict() -> ServiceFailure:
    return ServiceFailure(
        "idempotency_conflict",
        "The idempotency key was already used with different input.",
        None,
    )


def _audit_unavailable(run: QueryRun | None = None) -> ServiceFailure:
    return ServiceFailure("audit_unavailable", "The audit store is unavailable.", run)


def _retryable_failure(source: QueryRun) -> ServiceFailure:
    return ServiceFailure("query_run_not_retryable", "The query run cannot be retried.", source)


class QueryRunService:
    """提交路径：同步完成请求校验与策略判定，允许入队、拒绝终态化。"""

    def __init__(
        self,
        repository: Repository,
        policy: Policy,
        policy_version: str,
        statement_timeout_ms: int,
        max_rows: int,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._policy_version = policy_version
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows

    def submit(self, raw_sql: str, idempotency_key: str | None = None) -> QueryRun:
        if idempotency_key is None:
            return self._submit_new(raw_sql)
        return self._create_with_idempotency(
            raw_sql, IDEMPOTENCY_SCOPE_SUBMIT, idempotency_key
        )

    def retry(self, run_id: str, idempotency_key: str | None = None) -> QueryRun:
        """重试运行：为 failed/cancelled 运行创建记录 retry_of 的新查询运行。

        新运行拥有独立生命周期与审计事实，并重走完整治理链路（策略判定、
        执行、取消均适用）；rejected 必须修改 SQL 后重新提交，其余非允许
        状态返回 409。来源是终态时不可逆，不存在校验后的状态竞态。
        """
        try:
            source = self._repository.get(run_id)
        except ServiceFailure:
            raise
        except Exception as exc:
            raise _audit_unavailable() from exc
        if source is None:
            raise ServiceFailure("query_run_not_found", "Query run was not found.", None)
        if source.status not in ("failed", "cancelled"):
            raise _retryable_failure(source)

        # 重试幂等键按来源运行 ID 隔离；无键时每次重试创建新运行。
        if idempotency_key is None:
            return self._submit_new(source.raw_sql, retry_of=run_id)
        return self._create_with_idempotency(
            source.raw_sql,
            retry_idempotency_scope(run_id),
            idempotency_key,
            retry_of=run_id,
        )

    def _create_with_idempotency(
        self,
        raw_sql: str,
        scope: str,
        idempotency_key: str,
        **create_kwargs: object,
    ) -> QueryRun:
        """幂等创建：预检重放 → 创建（与键占用同事务）→ 竞态败者按重放返回。"""
        replay = self._replay(scope, idempotency_key, raw_sql)
        if replay is not None:
            return replay
        try:
            # 创建与键占用在同一事务提交：并发同键请求由唯一约束分出先后。
            return self._submit_new(
                raw_sql,
                idempotency_key=idempotency_key,
                idempotency_scope=scope,
                **create_kwargs,
            )
        except IdempotencyKeyTaken:
            # 竞态兜底：另一请求先提交了同一键，重读后按重放语义返回先到的运行。
            replay = self._replay(scope, idempotency_key, raw_sql)
            if replay is not None:
                return replay
            raise _conflict() from None

    def _replay(self, scope: str, idempotency_key: str, raw_sql: str) -> QueryRun | None:
        """命中已有键占用时按重放语义返回原运行；不同输入返回冲突。"""
        record = self._repository.find_idempotency(scope, idempotency_key)
        if record is None:
            return None
        if record.request_fingerprint != request_fingerprint(raw_sql):
            raise _conflict()
        try:
            run = self._repository.get(record.run_id)
        except Exception as exc:
            raise _audit_unavailable() from exc
        if run is None:
            raise _audit_unavailable()
        # 网络重试可能落在原运行生命周期的任何阶段：在途（received/queued/
        # running/cancelling）与成功/失败/取消终态都返回原运行，不创建新工作；
        # rejected 属于策略终态，以原拒绝语义重放（422 与原运行）。
        if run.status == "rejected":
            raise ServiceFailure(run.error_code or "unsupported_sql", run.error_summary or "SQL is not allowed.", run)
        return run

    def _submit_new(
        self,
        raw_sql: str,
        *,
        idempotency_key: str | None = None,
        idempotency_scope: str = IDEMPOTENCY_SCOPE_SUBMIT,
        retry_of: str | None = None,
    ) -> QueryRun:
        try:
            run = self._repository.create(
                raw_sql,
                self._policy_version,
                self._statement_timeout_ms,
                self._max_rows,
                idempotency_key=idempotency_key,
                idempotency_scope=idempotency_scope,
                retry_of=retry_of,
            )
        except IdempotencyKeyTaken:
            raise
        except Exception as exc:
            raise _audit_unavailable() from exc

        try:
            decision = self._policy.evaluate(raw_sql)
        except Exception as exc:
            failed = self._transition(
                run,
                "received",
                status="failed",
                error_code="policy_internal_error",
                error_summary="The SQL policy could not be evaluated.",
                **finished_fields(run),
            )
            raise ServiceFailure(
                "policy_internal_error",
                "The SQL policy could not be evaluated.",
                failed,
            ) from exc

        if not decision.allowed:
            rejected = self._transition(
                run,
                "received",
                status="rejected",
                policy_decision="rejected",
                referenced_objects=decision.referenced_objects,
                error_code=decision.code,
                error_summary=decision.summary,
                **finished_fields(run),
            )
            raise ServiceFailure(decision.code or "unsupported_sql", decision.summary or "SQL is not allowed.", rejected)

        return self._transition(
            run,
            "received",
            status="queued",
            policy_decision="allowed",
            referenced_objects=decision.referenced_objects,
        )

    def cancel(self, run_id: str) -> tuple[str, QueryRun]:
        """取消请求：持久化取消意图或返回既有终态事实；不可取消状态返回稳定失败。"""
        try:
            outcome, run = self._repository.cancel(run_id)
        except ServiceFailure:
            raise
        except Exception as exc:
            raise _audit_unavailable() from exc
        if outcome == CANCEL_OUTCOME_NOT_FOUND:
            raise ServiceFailure("query_run_not_found", "Query run was not found.", None)
        if outcome == CANCEL_OUTCOME_NOT_CANCELLABLE:
            raise ServiceFailure("query_run_not_cancellable", "The query run cannot be cancelled.", run)
        assert run is not None  # 可取消结果的运行事实总是存在。
        return outcome, run

    def _transition(
        self,
        run: QueryRun,
        expected_status: str,
        **changes: object,
    ) -> QueryRun:
        try:
            return self._repository.transition(run.id, expected_status, **changes)
        except ServiceFailure:
            raise
        except Exception as exc:
            raise _audit_unavailable(run) from exc

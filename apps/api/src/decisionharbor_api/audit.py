from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .domain import AuditPersistenceError, QueryResult, QueryRunResponse, RunState
from .policy import PolicyDecision


class SqlAuditStore:
    def __init__(self, database_url: str) -> None:
        self.engine: Engine = create_engine(database_url, pool_pre_ping=True)

    def dispose(self) -> None:
        self.engine.dispose()

    def create(self, run_id: str, raw_sql: str, created_at: datetime) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO platform.query_runs
                            (id, raw_sql, state, outcome, policy_decision, created_at)
                        VALUES (:id, :raw_sql, 'received', NULL, 'not_evaluated', :created_at)
                        """
                    ),
                    {"id": run_id, "raw_sql": raw_sql, "created_at": created_at},
                )
                self._insert_event(connection, run_id, "received", "received", None, created_at)
        except SQLAlchemyError as error:
            raise AuditPersistenceError("Unable to create query audit") from error

    def reject(self, run_id: str, decision: PolicyDecision, finished_at: datetime) -> None:
        self._finish(
            run_id,
            RunState.REJECTED,
            finished_at,
            policy_decision="rejected",
            policy_code=decision.code,
            error_code=decision.code,
            error_summary=decision.message,
        )

    def start(self, run_id: str, started_at: datetime) -> None:
        try:
            with self.engine.begin() as connection:
                self._transition(
                    connection,
                    run_id,
                    RunState.RECEIVED,
                    RunState.EXECUTING,
                    {"started_at": started_at, "policy_decision": "allowed"},
                    started_at,
                )
        except SQLAlchemyError as error:
            raise AuditPersistenceError("Unable to start query audit") from error

    def succeed(self, run_id: str, result: QueryResult, finished_at: datetime) -> None:
        self._finish(
            run_id,
            RunState.SUCCEEDED,
            finished_at,
            row_count=result.row_count,
            execution_duration_ms=result.duration_ms,
            error_code=None,
            error_summary=None,
        )

    def fail(self, run_id: str, code: str, message: str, finished_at: datetime) -> None:
        self._finish(run_id, RunState.FAILED, finished_at, error_code=code, error_summary=message)

    def get(self, run_id: str) -> QueryRunResponse | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT id, raw_sql, state, outcome, policy_decision, policy_code,
                               created_at, row_count, duration_ms, error_code, error_summary
                        FROM platform.query_runs
                        WHERE id = :id
                        """
                    ),
                    {"id": run_id},
                ).mappings().first()
        except SQLAlchemyError as error:
            raise AuditPersistenceError("Unable to read query audit") from error
        if row is None:
            return None
        state = RunState(row["state"])
        outcome = RunState(row["outcome"]) if row["outcome"] else None
        return QueryRunResponse(
            run_id=str(row["id"]),
            raw_sql=row["raw_sql"],
            state=state,
            outcome=outcome,
            created_at=row["created_at"],
            policy_decision=row["policy_decision"],
            policy_code=row["policy_code"],
            row_count=row["row_count"],
            duration_ms=row["duration_ms"],
            error_code=row["error_code"],
            error_message=row["error_summary"],
        )

    def _finish(self, run_id: str, state: RunState, finished_at: datetime, **values: Any) -> None:
        try:
            with self.engine.begin() as connection:
                created_at = self._transition(
                    connection,
                    run_id,
                    RunState.EXECUTING if state is not RunState.REJECTED else RunState.RECEIVED,
                    state,
                    {"finished_at": finished_at, **values},
                    finished_at,
                )
                duration_ms = max(0, int((finished_at - created_at).total_seconds() * 1000))
                connection.execute(
                    text("UPDATE platform.query_runs SET duration_ms = :duration_ms WHERE id = :id"),
                    {"id": run_id, "duration_ms": duration_ms},
                )
        except SQLAlchemyError as error:
            raise AuditPersistenceError("Unable to persist query audit") from error

    def _transition(
        self,
        connection,
        run_id: str,
        expected: RunState,
        new_state: RunState,
        values: dict[str, Any],
        event_at: datetime,
    ) -> datetime:
        row = connection.execute(
            text("SELECT state, created_at FROM platform.query_runs WHERE id = :id FOR UPDATE"),
            {"id": run_id},
        ).mappings().one_or_none()
        if row is None:
            raise AuditPersistenceError("Query audit record does not exist")
        current = RunState(row["state"])
        if current is new_state:
            return row["created_at"]
        if current is not expected:
            raise AuditPersistenceError(f"Invalid query state transition: {current} -> {new_state}")

        update_values: dict[str, Any] = {
            "id": run_id,
            "state": new_state.value,
            "outcome": new_state.value if new_state in {RunState.SUCCEEDED, RunState.REJECTED, RunState.FAILED} else None,
        }
        assignments = ["state = :state", "outcome = :outcome"]
        allowed_fields = {
            "started_at",
            "finished_at",
            "policy_decision",
            "policy_code",
            "row_count",
            "execution_duration_ms",
            "error_code",
            "error_summary",
        }
        for key, value in values.items():
            if key not in allowed_fields:
                raise AuditPersistenceError(f"Unsupported audit field: {key}")
            assignments.append(f"{key} = :{key}")
            update_values[key] = value
        connection.execute(
            text(f"UPDATE platform.query_runs SET {', '.join(assignments)} WHERE id = :id"),
            update_values,
        )
        self._insert_event(
            connection,
            run_id,
            new_state.value,
            new_state.value,
            values.get("error_code") or values.get("policy_code"),
            event_at,
        )
        return row["created_at"]

    @staticmethod
    def _insert_event(connection, run_id: str, state: str, event_type: str, code: str | None, occurred_at: datetime) -> None:
        sequence = connection.execute(
            text("SELECT COALESCE(MAX(sequence), 0) + 1 FROM platform.query_run_events WHERE run_id = :id"),
            {"id": run_id},
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO platform.query_run_events
                    (run_id, sequence, state, event_type, code, occurred_at)
                VALUES (:run_id, :sequence, :state, :event_type, :code, :occurred_at)
                """
            ),
            {
                "run_id": run_id,
                "sequence": sequence,
                "state": state,
                "event_type": event_type,
                "code": code,
                "occurred_at": occurred_at,
            },
        )

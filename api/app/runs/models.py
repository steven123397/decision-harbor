"""查询运行记录：模型与序列化。表结构以 Alembic 迁移为准。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATE_RECEIVED = "received"
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_CANCELLING = "cancelling"
STATE_CANCELLED = "cancelled"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_REJECTED = "rejected"

# 终态不可逆；一旦落入即不可再转移（cancelled 与 rejected/failed 并列）。
TERMINAL_STATES = frozenset(
    {STATE_SUCCEEDED, STATE_FAILED, STATE_REJECTED, STATE_CANCELLED}
)


class Base(DeclarativeBase):
    pass


class QueryRun(Base):
    __tablename__ = "query_runs"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    rejection_message: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column()
    # ---- v0.2.0 异步生命周期（expand：结构就位，同步链路暂不写入） ----
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    retry_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("query_runs.id")
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 代随认领/接管递增，终态发布与取消生效携带它做 fencing。
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QueryRunSnapshot(Base):
    __tablename__ = "query_run_snapshots"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("query_runs.id", ondelete="CASCADE"), primary_key=True
    )
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    rows: Mapped[list] = mapped_column(JSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def run_to_dict(run: QueryRun) -> dict:
    return {
        "id": run.id,
        "state": run.state,
        "sql": run.sql,
        "attempt": run.attempt,
        "idempotency_key": run.idempotency_key,
        "rejection_code": run.rejection_code,
        "rejection_message": run.rejection_message,
        "row_count": run.row_count,
        "truncated": run.truncated,
        "duration_ms": run.duration_ms,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }

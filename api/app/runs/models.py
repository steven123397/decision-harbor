"""查询运行记录：模型与序列化。表结构以 Alembic 迁移为准。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Identity, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_REJECTED = "rejected"
STATE_FAILED = "failed"


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


def run_to_dict(run: QueryRun) -> dict:
    return {
        "id": run.id,
        "state": run.state,
        "sql": run.sql,
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

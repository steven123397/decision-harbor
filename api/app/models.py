"""platform 库的审计模型。结构对应 docs/design/query-governance.md。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_REJECTED, STATUS_FAILED)


class Base(DeclarativeBase):
    pass


class QueryRun(Base):
    __tablename__ = "query_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{STATUS_RUNNING}', '{STATUS_SUCCEEDED}', "
            f"'{STATUS_REJECTED}', '{STATUS_FAILED}')",
            name="ck_query_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_RUNNING)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

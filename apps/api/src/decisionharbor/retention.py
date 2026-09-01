from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.domain import RESULT_RETENTION


RETENTION_PREDICATE_SQL = (
    "run.finished_at + CAST(:retention_seconds AS double precision) "
    "* INTERVAL '1 second' <= now()"
)


DELETE_EXPIRED_RESULTS = text(
    f"""
    DELETE FROM query_results AS result
    USING query_runs AS run
    WHERE result.query_run_id = run.id
      AND {RETENTION_PREDICATE_SQL}
    """
)


class ResultRetention:
    """Delete only expired snapshots while preserving query audit facts."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
        )

    def delete_expired(self) -> int:
        """Delete all snapshots past the database-defined retention boundary."""

        with self._engine.begin() as connection:
            result = connection.execute(
                DELETE_EXPIRED_RESULTS,
                {"retention_seconds": RESULT_RETENTION.total_seconds()},
            )
            return result.rowcount

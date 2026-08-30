from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from decisionharbor.domain import RESULT_RETENTION


# Deleting only the snapshot leaves every audit fact of the run behind, so a
# client can still see that the run succeeded, when it finished and how many
# rows it returned after its result is gone. The statement is also its own
# no-op: it selects by retention, never by what a previous run of the cleaner
# left behind, so overlapping schedulers and restarts all converge on the same
# stored state. It does not filter by status because only a succeeded run ever
# has a snapshot row: `publish_success` writes the two together and the
# `query_runs_require_snapshot` trigger keeps it that way.
DELETE_EXPIRED_RESULTS = text(
    """
    DELETE FROM query_run_results AS result
    USING query_runs AS run
    WHERE result.query_run_id = run.id
      AND run.finished_at + CAST(:retention_seconds AS double precision) * INTERVAL '1 second' <= now()
    """
)


class ResultRetention:
    """Removes result snapshots that outlived the retention window."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(
            database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
        )

    def delete_expired(self) -> int:
        """Delete every retained result that aged out, reporting how many went."""
        with self._engine.begin() as connection:
            return connection.execute(
                DELETE_EXPIRED_RESULTS,
                {"retention_seconds": RESULT_RETENTION.total_seconds()},
            ).rowcount

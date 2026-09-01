from sqlalchemy import create_engine, text


def backdate_finished_at(database_url: str, run_id: str, hours: float) -> None:
    """Move a run's database finish time to exercise retention boundaries."""

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE query_runs "
                    "SET finished_at = now() - :hours * INTERVAL '1 hour' "
                    "WHERE id = CAST(:run_id AS uuid)"
                ),
                {"hours": hours, "run_id": run_id},
            )
    finally:
        engine.dispose()

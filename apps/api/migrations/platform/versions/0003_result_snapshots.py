"""Store bounded result snapshots for succeeded query runs."""

from alembic import op


revision = "platform_0003"
down_revision = "platform_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE query_run_results (
            query_run_id uuid PRIMARY KEY REFERENCES query_runs (id),
            result_columns jsonb NOT NULL CHECK (jsonb_typeof(result_columns) = 'array'),
            result_rows jsonb NOT NULL CHECK (jsonb_typeof(result_rows) = 'array'),
            truncated boolean NOT NULL,
            created_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION query_runs_require_result_snapshot() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'succeeded' AND NOT EXISTS (
                SELECT 1 FROM query_run_results WHERE query_run_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'query run % cannot succeed without a result snapshot',
                    NEW.id USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER query_runs_require_snapshot
        BEFORE UPDATE ON query_runs
        FOR EACH ROW EXECUTE FUNCTION query_runs_require_result_snapshot()
        """
    )
    op.execute("GRANT SELECT, INSERT ON TABLE query_run_results TO platform_worker")
    op.execute("GRANT SELECT ON TABLE query_run_results TO platform_app")


def downgrade() -> None:
    op.execute("DROP TABLE query_run_results")
    op.execute("DROP TRIGGER query_runs_require_snapshot ON query_runs")
    op.execute("DROP FUNCTION query_runs_require_result_snapshot()")

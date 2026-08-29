import os

import pytest

from decisionharbor.executor import ExecutionFailure, PostgresQueryExecutor


pytestmark = pytest.mark.worker


@pytest.fixture
def executor() -> PostgresQueryExecutor:
    return PostgresQueryExecutor(os.environ["ANALYTICS_DATABASE_URL"], 1)


def test_allowed_query_returns_bounded_rows_with_explicit_types(
    executor: PostgresQueryExecutor,
) -> None:
    result = executor.execute(
        "SELECT customer_code::varchar(20), id::bigint, created_at::date "
        "FROM customers ORDER BY id LIMIT 1",
        5_000,
        500,
    )

    assert [column.type for column in result.columns] == [
        "character varying",
        "bigint",
        "date",
    ]
    assert len(result.rows) == 1
    assert result.truncated is False


def test_execution_stops_at_the_configured_row_limit(
    executor: PostgresQueryExecutor,
) -> None:
    result = executor.execute("SELECT id FROM orders ORDER BY id", 5_000, 2)

    assert result.rows == (("1",), ("2",))
    assert result.truncated is True


def test_statement_timeout_maps_to_a_stable_failure(
    executor: PostgresQueryExecutor,
) -> None:
    with pytest.raises(ExecutionFailure) as caught:
        executor.execute(
            "SELECT count(*) FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c",
            1,
            500,
        )

    assert caught.value.code == "query_timeout"


def test_semantic_error_maps_without_leaking_database_detail(
    executor: PostgresQueryExecutor,
) -> None:
    with pytest.raises(ExecutionFailure) as caught:
        executor.execute("SELECT missing_column FROM customers", 5_000, 500)

    assert caught.value.code == "query_semantic_error"
    assert "missing_column" not in caught.value.message.lower()


def test_unsupported_result_type_is_rejected_instead_of_stringified(
    executor: PostgresQueryExecutor,
) -> None:
    with pytest.raises(ExecutionFailure) as caught:
        executor.execute("SELECT ARRAY[1, 2] AS items", 5_000, 500)

    assert caught.value.code == "unsupported_result_type"

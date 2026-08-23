import pytest

from decisionharbor.config import ApiSettings, WorkerSettings
from decisionharbor.domain import QueryColumn, QueryResult
from decisionharbor.repository import QueryRunRepository


pytestmark = pytest.mark.integration


def test_repository_claims_queued_work_and_atomically_publishes_its_result() -> None:
    api_repository = QueryRunRepository(ApiSettings.from_env().platform_database_url)
    worker_repository = QueryRunRepository(WorkerSettings.from_env().platform_database_url)
    rejected = api_repository.create("DELETE FROM customers", "policy-v1", 5_000, 500)
    api_repository.transition(
        rejected.id,
        "received",
        status="rejected",
        policy_decision="rejected",
        error_code="sql_statement_not_allowed",
        error_summary="This SQL statement is not allowed.",
        finished_at=rejected.created_at,
        duration_ms=0,
    )
    queued = api_repository.create("SELECT count(*) FROM customers", "policy-v1", 5_000, 500)
    api_repository.transition(
        queued.id,
        "received",
        status="queued",
        policy_decision="allowed",
        referenced_objects=("analytics.customers",),
    )

    claimed = worker_repository.claim_next()

    assert claimed is not None
    assert claimed.id == queued.id
    assert claimed.status == "running"
    result = QueryResult(
        columns=(QueryColumn(name="count", type="bigint"),),
        rows=(("100",),),
        truncated=False,
    )
    published = worker_repository.publish_success(claimed.id, result)
    assert published.status == "succeeded"
    assert published.returned_row_count == 1
    assert api_repository.get_result(claimed.id) == result

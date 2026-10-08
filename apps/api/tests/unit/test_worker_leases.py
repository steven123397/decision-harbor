from datetime import datetime, timezone
from time import monotonic, sleep

from decisionharbor.domain import QueryRun
from decisionharbor.worker.leases import LeaseHeartbeat


NOW = datetime.now(timezone.utc)


class FakeQueue:
    def __init__(self, *, renewed: bool = True, fail: bool = False) -> None:
        self.renewed = renewed
        self.fail = fail
        self.renewals: list[str] = []

    def renew_lease(self, run: QueryRun) -> bool:
        if self.fail:
            raise RuntimeError("the platform database refused the renewal")
        self.renewals.append(run.id)
        return self.renewed


def owned_run(identifier: str, generation: int = 1) -> QueryRun:
    return QueryRun(
        id=identifier,
        raw_sql="SELECT id FROM customers ORDER BY id",
        status="running",
        policy_decision="allowed",
        policy_version="policy-v1",
        referenced_objects=("analytics.customers",),
        statement_timeout_ms=5_000,
        max_rows=500,
        returned_row_count=None,
        result_truncated=None,
        error_code=None,
        error_summary=None,
        created_at=NOW,
        started_at=NOW,
        finished_at=None,
        duration_ms=None,
        execution_attempt_count=generation,
        attempt_number=generation,
        attempt_worker_id="worker-test",
        attempt_generation=generation,
        lease_expires_at=NOW,
        heartbeat_at=NOW,
    )


def test_every_tracked_execution_is_renewed() -> None:
    queue = FakeQueue()
    leases = LeaseHeartbeat(queue, 1_000)
    leases.track(owned_run("run-1"))
    leases.track(owned_run("run-2"))

    leases.renew_once()

    assert queue.renewals == ["run-1", "run-2"]
    assert leases.owned_count == 2


def test_an_ownership_the_database_refuses_to_renew_is_dropped() -> None:
    # A refused renewal means another replica took the run over, so this worker
    # stops renewing what it no longer owns instead of stealing it back.
    queue = FakeQueue(renewed=False)
    leases = LeaseHeartbeat(queue, 1_000)
    leases.track(owned_run("run-1"))

    leases.renew_once()

    assert leases.owned_count == 0
    assert queue.renewals == ["run-1"]


def test_a_renewal_that_cannot_be_issued_keeps_the_ownership() -> None:
    # A transport failure is not a lost ownership: the next beat may still be
    # in time, and only the database decides when the lease is gone.
    queue = FakeQueue(fail=True)
    leases = LeaseHeartbeat(queue, 1_000)
    leases.track(owned_run("run-1"))

    leases.renew_once()

    assert leases.owned_count == 1


def test_a_released_execution_is_no_longer_renewed() -> None:
    queue = FakeQueue()
    leases = LeaseHeartbeat(queue, 1_000)
    owned = owned_run("run-1")
    leases.track(owned)
    leases.release(owned)

    leases.renew_once()

    assert queue.renewals == []
    assert leases.owned_count == 0


def test_the_heartbeat_renews_on_its_own_until_it_is_stopped() -> None:
    queue = FakeQueue()
    leases = LeaseHeartbeat(queue, 10)
    leases.track(owned_run("run-1"))
    leases.start()
    try:
        deadline = monotonic() + 5
        while len(queue.renewals) < 2 and monotonic() < deadline:
            sleep(0.01)
    finally:
        leases.stop()

    assert len(queue.renewals) >= 2

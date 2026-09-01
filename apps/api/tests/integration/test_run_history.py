"""运行历史分页的真实数据库集成证据。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
import pytest

from conftest import platform_engine

from decisionharbor.api import create_runtime_app
from decisionharbor.pagination import decode_cursor, encode_cursor


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("close_leftover_runs")]

# 历史断言只针对本用例创建的运行：SQL 标记隔离，避免数据库中
# 跨测试运行累积的其他运行干扰计数。
RUN_TOKEN = uuid4().hex[:8]
_SQL_COUNTER = [0]


def tagged(sql: str) -> str:
    _SQL_COUNTER[0] += 1
    return f"{sql} /* {RUN_TOKEN}-{_SQL_COUNTER[0]} */"


def set_created_at(engine: Engine, run_id: str, created_at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE query_runs SET created_at = :created_at WHERE id = CAST(:id AS uuid)"),
            {"created_at": created_at, "id": run_id},
        )


def submit(client: TestClient, sql: str) -> str:
    response = client.post("/api/v1/query-runs", json={"sql": sql})
    assert response.status_code == 202, response.text
    return response.json()["data"]["query_run"]["id"]


def run_created_at(client: TestClient, run_id: str) -> datetime:
    response = client.get(f"/api/v1/query-runs/{run_id}")
    assert response.status_code == 200
    return datetime.fromisoformat(response.json()["data"]["query_run"]["created_at"])


def history(client: TestClient, limit: int | str | None = None, cursor: str | None = None):
    params: dict[str, str | int] = {}
    if limit is not None:
        params["limit"] = limit
    if cursor is not None:
        params["cursor"] = cursor
    return client.get("/api/v1/query-runs", params=params or None)


def collect_pages(client: TestClient, limit: int, start_cursor: str | None = None):
    """从 start_cursor 起翻完整段历史，返回 (id 顺序, 途经的 next_cursor 列表)。"""
    seen: list[str] = []
    cursors: list[str | None] = []
    cursor = start_cursor
    while True:
        response = history(client, limit=limit, cursor=cursor)
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        seen.extend(run["id"] for run in payload["query_runs"])
        cursors.append(payload["next_cursor"])
        if payload["next_cursor"] is None:
            return seen, cursors
        cursor = payload["next_cursor"]


def test_history_defaults_to_20_most_recent_runs_in_stable_order() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(25)]

        response = history(client)

    assert response.status_code == 200
    payload = response.json()["data"]
    # 默认 limit 20：最新创建的 20 个运行按 created_at DESC 返回。
    assert [run["id"] for run in payload["query_runs"]] == list(reversed(ids))[:20]
    assert payload["next_cursor"] is not None
    # 游标解码回键集位置：位于本页最后一条记录上。
    decoded = decode_cursor(payload["next_cursor"])
    assert decoded is not None
    assert decoded.id == list(reversed(ids))[19]


def test_history_orders_by_created_at_desc_with_id_desc_tiebreak() -> None:
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(5)]
        # 前三条运行对齐到 ids[2] 的真实 created_at：形成恰好三条的平局，
        # 且该时间点早于 ids[3]、ids[4]。
        tie = run_created_at(client, ids[2])
        for run_id in ids[:2]:
            set_created_at(engine, run_id, tie)

        response = history(client, limit=100)

    assert response.status_code == 200
    returned = [run["id"] for run in response.json()["data"]["query_runs"]]
    # 更新的两条运行按 created_at 排在前面。
    assert returned[0] == ids[4]
    assert returned[1] == ids[3]
    # 平局内以 id DESC 打破。
    assert returned[2:5] == sorted(ids[:3], reverse=True)
    assert len(set(returned)) == len(returned)


def test_pages_walk_the_full_history_without_duplicates_or_gaps() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(7)]

        for limit in (1, 3, 20):
            seen, cursors = collect_pages(client, limit=limit)

            # 各 limit 下都能翻完：无重复、无遗漏、顺序稳定。
            assert seen[: len(ids)] == list(reversed(ids)), limit
            assert len(seen) == len(set(seen)), limit
            assert cursors[-1] is None, limit


def test_ties_straddling_the_page_boundary_stay_stable() -> None:
    """相同 created_at 平局横跨页面边界时，翻页仍严格位于游标之后。"""
    engine = platform_engine()
    app = create_runtime_app()
    tie = datetime.now(timezone.utc)
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(6)]
        for run_id in ids:
            set_created_at(engine, run_id, tie)

        seen, _ = collect_pages(client, limit=2)

    # 全部平局时顺序退化为 id DESC，翻页不打断平局也不重复。
    assert seen[: len(ids)] == sorted(ids, reverse=True)
    assert len(seen) == len(set(seen))


def test_inserts_after_the_cursor_do_not_duplicate_or_skip_the_snapshot() -> None:
    """取得游标后新插入的运行不使后续页面重复或跳过原快照中的记录。

    插入覆盖两种形态：比全部既有记录更新（排在页面顶端），以及与游标记录
    created_at 完全相同的平局插入（检验键集比较不误伤平局邻居）。
    """
    engine = platform_engine()
    app = create_runtime_app()
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(5)]
        # 取得第一页与游标：原快照从游标之后继续（ids[2]、ids[1]、ids[0]）。
        first_page = history(client, limit=2)
        assert first_page.status_code == 200
        next_cursor = first_page.json()["data"]["next_cursor"]
        assert next_cursor is not None
        assert [run["id"] for run in first_page.json()["data"]["query_runs"]] == [ids[4], ids[3]]
        rest = [ids[2], ids[1], ids[0]]

        # 游标取得后插入：一条比全部记录更新；三条与游标记录 created_at 对齐。
        newer_id = submit(client, tagged("SELECT count(*) FROM products"))
        cursor_created_at = datetime.fromisoformat(
            first_page.json()["data"]["query_runs"][-1]["created_at"]
        )
        tie_ids = [submit(client, tagged("SELECT count(*) FROM order_items")) for _ in range(3)]
        for tie_id in tie_ids:
            set_created_at(engine, tie_id, cursor_created_at)

        # 用原游标继续翻页：键集条件不受新插入影响。
        seen_after, _ = collect_pages(client, limit=2, start_cursor=next_cursor)

    # 原快照游标之后的记录按原相对顺序全部到达（平局插入可能穿插其间）。
    assert [run_id for run_id in seen_after if run_id in set(rest)] == rest
    # 续页无重复；更晚的新插入位于游标之前，不会挤入续页。
    assert len(seen_after) == len(set(seen_after))
    assert newer_id not in seen_after
    # 平局插入只会落在游标位置的紧邻边界上：出现与否都合法，但绝不能是 rest 之外的错位重复。
    for tie_id in tie_ids:
        assert seen_after.count(tie_id) <= 1


def test_concurrent_submissions_while_paging_do_not_duplicate_or_skip() -> None:
    """翻页期间的并发提交不使任何一页重复或跳过原快照中的记录。"""
    app = create_runtime_app()
    with TestClient(app) as client:
        ids = [submit(client, tagged("SELECT count(*) FROM customers")) for _ in range(5)]

        # 取得游标（原快照从游标之后继续）。
        first_page = history(client, limit=3)
        next_cursor = first_page.json()["data"]["next_cursor"]
        assert next_cursor is not None

        # 翻页期间并发提交新运行。
        def insert_more(_: int):
            with TestClient(app) as worker_client:
                return submit(worker_client, tagged("SELECT count(*) FROM products"))

        with ThreadPoolExecutor(max_workers=3) as pool:
            fresh_ids = list(pool.map(insert_more, range(3)))

        seen_after, _ = collect_pages(client, limit=3, start_cursor=next_cursor)

    # 旧快照的剩余记录按原顺序完整到达，无重复。
    expected_rest = list(reversed(ids))[3:]
    assert seen_after[: len(expected_rest)] == expected_rest
    assert len(seen_after) == len(set(seen_after))
    # 新插入位于游标之前（更新），键集条件天然排除，不会挤入续页。
    for fresh_id in fresh_ids:
        assert fresh_id not in seen_after


def test_invalid_limit_and_cursor_are_422_invalid_pagination() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        for raw_limit in ("0", "-1", "101", "abc", "1.5", " 5", "+5", "1e2"):
            response = history(client, limit=raw_limit)

            assert response.status_code == 422, raw_limit
            assert response.json()["error"]["code"] == "invalid_pagination", raw_limit
            assert response.json()["data"] is None, raw_limit

        for raw_cursor in ("", "not-a-cursor", "!!!!!", "eyJ2IjoyLCJjcmVhdGVkX2F0IjoiWCJ9"):
            response = history(client, cursor=raw_cursor)

            assert response.status_code == 422, raw_cursor
            assert response.json()["error"]["code"] == "invalid_pagination", raw_cursor
            assert response.json()["data"] is None, raw_cursor


def test_limit_boundaries_are_accepted_on_the_real_database() -> None:
    app = create_runtime_app()
    with TestClient(app) as client:
        for raw_limit in ("1", "20", "100"):
            response = history(client, limit=raw_limit)

            assert response.status_code == 200, raw_limit
            assert len(response.json()["data"]["query_runs"]) <= int(raw_limit)


def test_history_covers_runs_across_statuses() -> None:
    """历史呈现所有状态的运行：排队、策略拒绝与已执行终态都在列表中。"""
    from conftest import worker_settings
    from decisionharbor.worker import QueryWorker

    app = create_runtime_app()
    worker = QueryWorker(worker_settings())
    with TestClient(app) as client:
        queued_id = submit(client, tagged("SELECT count(*) FROM customers"))
        rejected = client.post("/api/v1/query-runs", json={"sql": tagged("DELETE FROM customers")})
        assert rejected.status_code == 422
        rejected_id = rejected.json()["data"]["query_run"]["id"]
        succeeded_id = submit(client, tagged("SELECT count(*) FROM orders"))
        assert worker.run_once() is True

        response = history(client, limit=100)

    statuses = {run["id"]: run["status"] for run in response.json()["data"]["query_runs"]}
    assert statuses[queued_id] in ("queued", "succeeded")
    assert statuses[rejected_id] == "rejected"
    assert statuses[succeeded_id] in ("succeeded", "queued")


def test_cursor_is_opaque_and_round_trips_through_the_api() -> None:
    """服务端生成的游标对客户端是不透明字符串，翻页链路可往返。"""
    app = create_runtime_app()
    with TestClient(app) as client:
        first = history(client, limit=2)
        next_cursor = first.json()["data"]["next_cursor"]
        assert next_cursor is not None

        decoded = decode_cursor(next_cursor)
        assert decoded is not None
        # 游标编码稳定：同一位置重新编码得到同一字符串。
        assert encode_cursor(decoded) == next_cursor

        second = history(client, limit=2, cursor=next_cursor)
        assert second.status_code == 200
        # 两页没有重复。
        first_ids = {run["id"] for run in first.json()["data"]["query_runs"]}
        second_ids = {run["id"] for run in second.json()["data"]["query_runs"]}
        assert first_ids.isdisjoint(second_ids)

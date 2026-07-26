"""就绪检查的失败必须有界：数据库完全不可达时不能挂起。

语义见 docs/design/query-runs-api.md 的 `GET /ready`。
"""

import time

import pytest

from app.config import get_settings
from app.db import make_engine
from app.readiness import check_readiness

pytestmark = pytest.mark.integration

# RFC 5737 TEST-NET-1：保留给文档用途，不可路由；连接请求通常被丢弃而非立即拒绝
UNREACHABLE_URL = "postgresql+psycopg://nobody:nobody@192.0.2.1:5432/platform"


@pytest.fixture(scope="module")
def unreachable_probe():
    """对不可达数据库做一次就绪检查，返回（耗时秒，未就绪原因）。"""
    engine = make_engine(UNREACHABLE_URL)
    started = time.monotonic()
    problems = check_readiness(engine, engine)
    return time.monotonic() - started, problems


def test_readiness_fails_within_bound_when_database_unreachable(unreachable_probe):
    elapsed, problems = unreachable_probe
    # 两库各尝试一次连接，另留出握手与解析余量
    bound_s = get_settings().db_connect_timeout_s * 2 + 5
    assert problems, "数据库不可达时必须报告未就绪原因"
    assert elapsed < bound_s, f"就绪检查耗时 {elapsed:.1f}s，超过上限 {bound_s}s"


def test_readiness_problem_message_has_no_connection_string(unreachable_probe):
    _, problems = unreachable_probe
    joined = " ".join(problems)
    assert "nobody" not in joined and "192.0.2.1" not in joined

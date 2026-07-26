"""就绪检查：两库可连接且两套迁移均处于最新版本。见 docs/design/query-runs-api.md。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@lru_cache(maxsize=2)
def _expected_head(section: str) -> str | None:
    config = Config(str(ALEMBIC_INI), ini_section=section)
    return ScriptDirectory.from_config(config).get_current_head()


def _applied_revision(engine: Engine, version_table: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT version_num FROM {version_table}")  # noqa: S608 固定表名
        ).scalar()


def check_readiness(platform: Engine, analytics: Engine) -> list[str]:
    """返回未就绪原因列表；为空表示就绪。"""
    problems: list[str] = []
    checks = [
        ("platform", platform, "alembic_version"),
        ("analytics", analytics, "analytics.alembic_version"),
    ]
    for section, engine, version_table in checks:
        try:
            applied = _applied_revision(engine, version_table)
        except Exception as error:  # noqa: BLE001 就绪检查汇总一切失败原因
            problems.append(f"{section}: 数据库不可用或迁移未建立（{type(error).__name__}）")
            continue
        expected = _expected_head(section)
        if applied != expected:
            problems.append(
                f"{section}: 迁移版本 {applied or '无'} 不是最新版本 {expected or '无'}"
            )
    return problems

from pathlib import Path

import pytest

import decisionharbor.readiness as readiness_module
from decisionharbor.dataset import DatasetContract
from decisionharbor.readiness import AnalyticsReadinessProbe, PlatformReadinessProbe, migration_head


class FakeResult:
    def __init__(self, version: str) -> None:
        self._version = version

    def scalar_one(self) -> str:
        return self._version


class FakeConnection:
    def __init__(self, version: str) -> None:
        self._version = version

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def execute(self, _statement) -> FakeResult:
        return FakeResult(self._version)


class FakeEngine:
    def __init__(self, version: str) -> None:
        self._version = version

    def connect(self) -> FakeConnection:
        return FakeConnection(self._version)


class FakeAnalyticsConnection(FakeConnection):
    def execute(self, statement, _parameters=None) -> FakeResult:
        sql = str(statement)
        if "default_transaction_read_only" in sql:
            return FakeResult("on")
        if "alembic_version" in sql:
            return FakeResult(self._version)
        result = FakeResult("")
        result.one_or_none = lambda: ("contract-hash", "manifest-hash", {"customers": 100})
        return result


class FakeAnalyticsEngine(FakeEngine):
    def connect(self) -> FakeAnalyticsConnection:
        return FakeAnalyticsConnection(self._version)


def write_migration_tree(root: Path, revisions: list[tuple[str, str | None]]) -> Path:
    versions = root / "migrations" / "versions"
    versions.mkdir(parents=True)
    for revision, down_revision in revisions:
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {down_revision!r}\n",
            encoding="utf-8",
        )
    config = root / "alembic.ini"
    config.write_text(
        "[alembic]\nscript_location = %(here)s/migrations\n",
        encoding="utf-8",
    )
    return config


def test_shipped_migration_configs_have_one_current_head() -> None:
    api_root = Path(readiness_module.__file__).resolve().parents[2]

    assert migration_head(api_root / "alembic-platform.ini") == "platform_0005"
    assert migration_head(api_root / "alembic-analytics.ini") == "analytics_0002"


@pytest.mark.parametrize(
    ("database_version", "expected"),
    [("platform_0005", True), ("platform_0004", False), ("platform_9999", False)],
)
def test_platform_readiness_requires_an_exact_migration_head(
    monkeypatch: pytest.MonkeyPatch,
    database_version: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        readiness_module,
        "_readiness_engine",
        lambda _database_url: FakeEngine(database_version),
    )
    probe = PlatformReadinessProbe("unused")

    assert probe.check_ready() is expected


def test_platform_readiness_fails_closed_when_scripts_have_multiple_heads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = write_migration_tree(tmp_path, [("head_a", None), ("head_b", None)])
    monkeypatch.setattr(
        readiness_module,
        "_readiness_engine",
        lambda _database_url: FakeEngine("head_a"),
    )
    probe = PlatformReadinessProbe("unused", migration_config=config)

    assert probe.check_ready() is False


@pytest.mark.parametrize(
    ("database_version", "expected"),
    [("analytics_0002", True), ("analytics_0001", False), ("analytics_9999", False)],
)
def test_analytics_readiness_requires_an_exact_migration_head(
    monkeypatch: pytest.MonkeyPatch,
    database_version: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        readiness_module,
        "_readiness_engine",
        lambda _database_url: FakeAnalyticsEngine(database_version),
    )
    dataset = DatasetContract(
        root=Path("unused"),
        contract={"expected_counts": {"customers": 100}},
        manifest={"dataset": "sales-analytics", "version": "v1"},
        contract_sha256="contract-hash",
        manifest_sha256="manifest-hash",
    )

    assert AnalyticsReadinessProbe("unused").check_ready(dataset) is expected

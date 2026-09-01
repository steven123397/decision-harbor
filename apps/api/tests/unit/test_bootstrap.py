import pytest

import decisionharbor.bootstrap as bootstrap
from decisionharbor.seed import SeedConflict


def bootstrap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_DATABASE_URL", "postgresql://harbor_admin:x@127.0.0.1:1/postgres")
    monkeypatch.setenv("DATASET_ROOT", "/tmp/sales-analytics-v1")
    monkeypatch.setenv("PLATFORM_APP_PASSWORD", "x")
    monkeypatch.setenv("PLATFORM_WORKER_PASSWORD", "x")
    monkeypatch.setenv("ANALYTICS_READER_PASSWORD", "x")
    monkeypatch.setenv("ANALYTICS_READINESS_PASSWORD", "x")


def stub_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过数据库与数据集 IO：main 的编排调用全部替换为无操作。"""
    monkeypatch.setattr(bootstrap, "run_public_validator", lambda root: None)
    monkeypatch.setattr(bootstrap, "load_dataset", lambda root: object())
    monkeypatch.setattr(bootstrap, "_ensure_roles_and_databases", lambda *args: None)
    monkeypatch.setattr(bootstrap, "_migrate", lambda *args: None)
    monkeypatch.setattr(bootstrap, "_harden_public_schema", lambda *args: None)


def test_bootstrap_main_exits_cleanly_on_seed_conflict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    bootstrap_env(monkeypatch)
    stub_infrastructure(monkeypatch)

    def conflicting_seed(*args, **kwargs):
        raise SeedConflict("seed_conflict: existing seed marker or rows do not match")

    monkeypatch.setattr(bootstrap, "seed_dataset", conflicting_seed)

    with pytest.raises(SystemExit) as caught:
        bootstrap.main()

    assert caught.value.code == 1
    error = capsys.readouterr().err
    assert "seed_conflict" in error
    # 已知冲突以稳定消息失败：默认日志不留下堆栈。
    assert "Traceback" not in error

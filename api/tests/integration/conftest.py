import pytest

from app.bootstrap import bootstrap


@pytest.fixture(scope="session", autouse=True)
def _prepared():
    bootstrap()
    yield

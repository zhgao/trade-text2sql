"""pytest 共享 fixture：确保演示数据库已初始化。"""
import pytest

from backend.db.db import init_db


@pytest.fixture(scope="session", autouse=True)
def _seeded_db():
    init_db()
    yield

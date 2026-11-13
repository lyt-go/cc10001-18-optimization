"""测试共用夹具：每个用例重置内存仓库与版本缓存，避免结论串场景。"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import store
from app.services import versioning


@pytest.fixture(autouse=True)
def reset_runtime() -> None:
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    versioning.version_service.registry = versioning.VersionRegistry()
    yield

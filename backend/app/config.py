"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "网络数据采集管理平台"
    env: str = "local"
    port: int = 8000
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 模板校验结论的有效期：有效期内同一模板 + 同一组解析规则直接复用，
    # 任一配置变化都会换指纹、旧结论自然失效。
    validation_ttl_seconds: int = field(
        default_factory=lambda: _env_int("VALIDATION_TTL_SECONDS", 300)
    )


settings = Settings()

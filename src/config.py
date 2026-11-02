"""配置与 .env 加载。

不依赖 python-dotenv：20 行手写解析即可，保证评审零安装。
优先级：进程环境变量 > .env 文件 > 内置默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_STORE_DIR = os.path.join(DATA_DIR, "store")
DEFAULT_CASES_DIR = os.path.join(REPO_ROOT, "examples", "cases")
DEFAULT_TRACE_DIR = os.path.join(REPO_ROOT, "examples", "traces")


def load_dotenv(path: Optional[str] = None) -> Dict[str, str]:
    """解析 .env（KEY=VALUE，# 注释，支持引号）。文件不存在时返回空 dict。"""
    path = path or os.path.join(REPO_ROOT, ".env")
    values: Dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                values[key] = value
    return values


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"
    api_key: str = ""
    max_tokens: int = 2500
    temperature: float = 0.0
    timeout_sec: int = 60

    # Agent 护栏：三者任一触达都会返回明确的未完成状态
    max_steps: int = 12
    max_tool_calls: int = 8
    max_repeat: int = 2

    @classmethod
    def load(cls, env_path: Optional[str] = None) -> "Settings":
        dotenv = load_dotenv(env_path)

        def pick(key: str, default: str = "") -> str:
            value = os.environ.get(key)
            if value is None or value == "":
                value = dotenv.get(key, default)
            return value

        return cls(
            base_url=pick("OPENAI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            model=pick("OPENAI_MODEL", "deepseek-flash"),
            api_key=pick("OPENAI_API_KEY", ""),
            max_tokens=_int(pick("OPENAI_MAX_TOKENS", "2500"), 2500),
            temperature=_float(pick("OPENAI_TEMPERATURE", "0"), 0.0),
            timeout_sec=_int(pick("AGENT_TIMEOUT_SEC", "60"), 60),
            max_steps=_int(pick("AGENT_MAX_STEPS", "12"), 12),
            max_tool_calls=_int(pick("AGENT_MAX_TOOL_CALLS", "8"), 8),
            max_repeat=_int(pick("AGENT_MAX_REPEAT", "2"), 2),
        )

    def masked(self) -> Dict[str, object]:
        """给 docs/trace 用的安全视图，永不打印 key 明文。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": "***" if self.api_key else "",
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_repeat": self.max_repeat,
            "timeout_sec": self.timeout_sec,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

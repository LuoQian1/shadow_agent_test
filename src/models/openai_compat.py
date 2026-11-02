"""真实模型适配：OpenAI 兼容 /chat/completions（默认 DeepSeek 官方）。

零依赖实现，只用 urllib。可靠性处理：
* 429 / 5xx / 网络错误：指数退避重试，最多 3 次
* 服务端不支持 response_format=json_object（HTTP 400）：自动降级为纯文本 JSON 模式并记住
* 输出解析：先剥离 ``` 围栏，再取首尾大括号；解析失败抛 ModelError 交由循环补救
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from ..config import Settings
from .base import Model, ModelError
from .prompt import build_messages


def _extract_json(text: str) -> Dict[str, Any]:
    if not text or not str(text).strip():
        raise ModelError("模型返回空内容")
    payload = str(text).strip()
    if payload.startswith("```"):
        payload = payload.strip("`")
        if payload[:4].lower() == "json":
            payload = payload[4:]
    start = payload.find("{")
    end = payload.rfind("}")
    if start < 0 or end <= start:
        raise ModelError("模型输出中没有 JSON 对象：%s" % payload[:200])
    candidate = payload[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except ValueError as exc:
        raise ModelError("模型输出 JSON 解析失败：%s | 片段：%s" % (exc, candidate[:200]))
    if not isinstance(parsed, dict):
        raise ModelError("模型输出的顶层结构不是 JSON 对象")
    return parsed


class OpenAICompatModel(Model):
    name = "openai-compat"
    kind = "real"

    def __init__(self, settings: Settings, max_retries: int = 3) -> None:
        super().__init__()
        self.settings = settings
        self.max_retries = max_retries
        self.json_mode = True
        self.last_prompt_hash = ""

    # --------------------------------------------------------------- public
    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.settings.api_key:
            raise ModelError(
                "未配置 OPENAI_API_KEY：请复制 .env.example 为 .env 并填写，或改用 --model offline"
            )
        messages = build_messages(state)
        prompt_blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        self.last_prompt_hash = hashlib.sha256(prompt_blob.encode("utf-8")).hexdigest()[:16]

        started = time.time()
        content, usage = self._call(messages)
        latency_ms = int((time.time() - started) * 1000)
        action = _extract_json(content)
        self.call_count += 1
        self.last_meta = {
            "model": self.settings.model,
            "latency_ms": latency_ms,
            "usage": usage,
            "prompt_hash": self.last_prompt_hash,
            "content_chars": len(content),
            "json_mode": self.json_mode,
        }
        return action

    # -------------------------------------------------------------- internals
    def _payload(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _call(self, messages: List[Dict[str, str]]) -> Tuple[str, Dict[str, Any]]:
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            payload = self._payload(messages)
            request = urllib.request.Request(
                self.settings.base_url + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.settings.api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout_sec) as response:
                    body = json.loads(response.read().decode("utf-8"))
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content = message.get("content") or ""
                usage = body.get("usage") or {}
                if not content.strip():
                    raise ModelError("模型返回的 content 为空")
                return content, usage
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:  # pragma: no cover
                    detail = ""
                last_error = "HTTP %s %s" % (exc.code, detail)
                if exc.code == 400 and self.json_mode and "response_format" in detail.lower():
                    # 服务端不支持 JSON 模式，降级后立刻重试
                    self.json_mode = False
                    continue
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(min(1.5 * attempt, 4.0))
                    continue
                raise ModelError(last_error)
            except urllib.error.URLError as exc:
                last_error = "网络错误：%s" % exc.reason
                time.sleep(min(1.5 * attempt, 4.0))
                continue
            except ModelError:
                raise
            except Exception as exc:  # pragma: no cover - 防御性
                last_error = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(min(1.0 * attempt, 3.0))
                continue
        raise ModelError("模型调用失败（已重试 %s 次）：%s" % (self.max_retries, last_error))

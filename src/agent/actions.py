"""动作解析与校验：把模型输出收敛成两种动作，别的一律拒绝。"""

from __future__ import annotations

from typing import Any, Dict


class ActionError(Exception):
    pass


def normalize_action(raw: Any) -> Dict[str, Any]:
    """把模型输出规范化为 {type:tool|final, ...}。

    兼容下面两种常见形态：
        {"rationale": "...", "action": {"type": "tool", ...}}
        {"type": "tool", "name": "...", "args": {...}}      # 少一层包裹
    """
    if not isinstance(raw, dict):
        raise ActionError("动作必须是 JSON 对象，实际是 %s" % type(raw).__name__)

    body = raw.get("action") if isinstance(raw.get("action"), dict) else raw
    rationale = raw.get("rationale") or body.get("rationale") or ""
    action_type = str(body.get("type") or "").lower()
    if not action_type:
        action_type = "tool" if (body.get("name") or body.get("tool")) else "final"

    if action_type == "tool":
        name = body.get("name") or body.get("tool")
        args = body.get("args")
        if args is None:
            args = body.get("arguments")
        if not isinstance(name, str) or not name.strip():
            raise ActionError("tool 动作缺少 name")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ActionError("tool 动作的 args 必须是 JSON 对象")
        return {"type": "tool", "name": name.strip(), "args": args, "rationale": str(rationale)[:200]}

    if action_type == "final":
        claims = body.get("claims")
        if not isinstance(claims, dict):
            claims = {
                key: value
                for key, value in body.items()
                if key not in ("type", "rationale", "claims")
            }
        return {"type": "final", "claims": claims, "rationale": str(rationale)[:200]}

    raise ActionError("未知的动作类型：%r（只允许 tool / final）" % action_type)

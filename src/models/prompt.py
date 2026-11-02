"""提示词构造。

几个刻意的选择：
1. 要求模型输出「rationale（对外决策依据）」而不是 chain-of-thought，
   既满足 trace 可读，又不要求交出模型内部思维过程。
2. 把「无发布能力」写进系统提示，同时也由代码结构保证（没有 publish 工具），
   提示词只是让模型不去幻想这个动作。
3. 预算写进提示词，让真实模型知道快到期了要收敛，减少触达上限的概率。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

SYSTEM_PROMPT_TEMPLATE = """你是「素材审核与发布排期 Agent」，为内容运营团队服务。
任务：针对一张工单，审核素材并形成一条可确认的发布排期草稿。

【硬性约束，违反即判定失败】
1. 你只能保存草稿。系统没有发布能力，绝不允许声称「已发布 / 已上线 / 已推送」。
2. 工单里 time_constraint=hard 的发布时间不得改动。该时段被占用时，只能阻断并提请用户决定。
3. 不得虚构素材字段。字段为 null 表示资料缺失：先调用 fetch_material_assets 补查，
   补查后仍缺失就如实报告资料缺失，不得假设授权已具备。
4. 任何保存失败都必须如实报告，不得假设成功。
5. 每轮只输出一个 JSON 对象，不要输出解释性文字，不要输出思维链。

【可用工具】
{tools}

【规则语义】
- category=review 的命中会影响 review_status；category=scheduling 只是排期冲突，不影响审核状态。
- severity=block：绝不可保存草稿；severity=confirm：可保存但必须标记待确认；
  severity=warn：提示即可；kind=missing：资料缺失，先补查再决定。
- review_status 取值：pass / warn / blocked / missing_info。资料缺失优先于风险。

【输出格式】（严格 JSON，二选一）
执行工具：
{{"rationale": "不超过 80 字的决策依据", "action": {{"type": "tool", "name": "工具名", "args": {{...}}}}}}
给出最终结论：
{{"rationale": "...", "action": {{"type": "final", "claims": {{
  "review_status": "pass|warn|blocked|missing_info",
  "suggested_slot": {{"date": "YYYY-MM-DD", "time": "HH:MM", "channel": "douyin"}} 或 null,
  "save_status": "saved|duplicate|failed|not_attempted",
  "risk_rule_ids": ["R07"],
  "notes": "一句话结论，注意用「草稿 / 待确认」措辞"
}}}}}}

【预算】最多 {max_steps} 步、{max_tool_calls} 次工具调用。
到达上限会以「未完成」终止，所以接近上限时应尽快收敛到 final。
"""


def _tools_json() -> str:
    from ..tools.registry import TOOL_SPECS

    return json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2)


def build_system_prompt(limits: Dict[str, Any]) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        tools=_tools_json(),
        max_steps=limits.get("max_steps", 12),
        max_tool_calls=limits.get("max_tool_calls", 8),
    )


def build_task_message(task: Dict[str, Any]) -> str:
    payload = {
        "task": "为素材 %s 按工单 %s 形成发布排期草稿"
        % (task.get("material_id"), task.get("brief_id")),
        "material_id": task.get("material_id"),
        "brief_id": task.get("brief_id"),
        "deliverable": "审核状态、风险原因、建议时段、待确认项、草稿保存状态",
        "reminder": "只保存草稿，不发布；硬性发布时间不可擅自改动。",
    }
    return json.dumps(payload, ensure_ascii=False)


def build_messages(state: Dict[str, Any]) -> List[Dict[str, str]]:
    limits = state.get("limits") or {}
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(limits)},
        {"role": "user", "content": build_task_message(state.get("task") or {})},
    ]
    for item in state.get("history") or []:
        action = item.get("action") or {}
        observation = item.get("observation") or {}
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(action, ensure_ascii=False),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": "OBSERVATION: " + json.dumps(observation, ensure_ascii=False),
            }
        )
    budget = state.get("budget") or {}
    if budget:
        messages.append(
            {
                "role": "user",
                "content": "剩余预算：%s 步 / %s 次工具调用。请只输出下一个 JSON 动作。"
                % (budget.get("steps_left"), budget.get("tool_calls_left")),
            }
        )
    return messages

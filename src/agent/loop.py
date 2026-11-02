"""ReAct 决策循环。

循环结构（每一轮都由模型决定下一步，不是固定流程）：
    1. 组装 state（任务 + 累积事实 + 历史动作/观察 + 剩余预算）
    2. model.decide(state) -> 一个 JSON 动作
    3. 代码侧校验动作（工具白名单 / 参数 schema / 重复调用 / 预算）
    4. 执行工具 -> 真实结果
    5. 把结果写进 facts 与 history -> 回到 1

护栏：
* max_steps / max_tool_calls / max_repeat 任一触达 -> status=incomplete_limit_reached，
  绝不把「没跑完」当成「已完成」。
* 真实模型抛 ModelError 时，若允许降级，则切换到离线策略模型「就着已有 facts 续跑」，
  而不是从头重来，也不会静默吞掉错误（trace 里留 model_degraded 记录）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..models.base import Model, ModelError
from ..tools.registry import TOOL_NAMES, validate_args
from . import verifier as verifier_mod
from .actions import ActionError, normalize_action

LIMIT_STATUS = "incomplete_limit_reached"
MAX_INVALID_ACTIONS = 3


def initial_facts(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "material_id": task.get("material_id"),
        "material": None,
        "material_found": None,
        "brief": None,
        "review": None,
        "findings": None,
        "assets_fetched": False,
        "assets_still_missing": None,
        "drafts": None,
        "slots": None,
        "slot_query_failures": 0,
        "save_attempts": [],
        "last_save": None,
        "last_save_args": None,
        "last_save_slot": None,
        "invalid_actions": 0,
    }


def update_facts(facts: Dict[str, Any], tool: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
    """把工具观察折进事实台账。这是渲染与校验唯一信任的数据源。"""
    if tool == "read_material":
        facts["material_found"] = bool(result.get("found"))
        if result.get("found"):
            facts["material"] = result.get("material")
            facts["missing_metadata"] = result.get("missing_fields") or []
    elif tool == "get_publish_brief":
        if result.get("found"):
            facts["brief"] = result.get("brief")
    elif tool == "check_rules":
        if result.get("ok"):
            facts["review"] = result.get("review")
            facts["findings"] = result.get("findings") or []
    elif tool == "fetch_material_assets":
        facts["assets_fetched"] = True
        facts["assets"] = result.get("assets")
        facts["assets_still_missing"] = result.get("still_missing")
    elif tool == "list_drafts":
        if result.get("ok"):
            facts["drafts"] = result.get("drafts") or []
    elif tool == "query_slots":
        if result.get("ok"):
            facts["slots"] = result
        else:
            facts["slot_query_failures"] = int(facts.get("slot_query_failures") or 0) + 1
    elif tool == "save_schedule_draft":
        facts["save_attempts"].append(
            {
                "attempt": result.get("attempt"),
                "save_status": result.get("save_status"),
                "slot": {"date": args.get("date"), "time": args.get("time"), "channel": args.get("channel")},
                "draft_id": result.get("draft_id"),
                "error": result.get("error"),
            }
        )
        facts["last_save"] = result
        facts["last_save_args"] = dict(args)
        facts["last_save_slot"] = {
            "date": args.get("date"),
            "time": args.get("time"),
            "channel": args.get("channel"),
        }


class AgentRunner(object):
    def __init__(
        self,
        model: Model,
        toolbox: Any,
        settings: Any,
        task: Dict[str, Any],
        limits: Optional[Dict[str, int]] = None,
        fallback_model: Optional[Model] = None,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        self.toolbox = toolbox
        self.settings = settings
        self.task = task
        self.limits = dict(
            limits
            or {
                "max_steps": getattr(settings, "max_steps", 12),
                "max_tool_calls": getattr(settings, "max_tool_calls", 8),
                "max_repeat": getattr(settings, "max_repeat", 2),
            }
        )
        self.case_id = str(task.get("case_id") or "case")
        self.run_index = int(task.get("run_index") or 1)
        self.run_id = "RUN-%s-%02d" % (self.case_id, self.run_index)
        self.primary_model_name = model.name
        self.trace: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ util
    def _emit(self, record_type: str, payload: Dict[str, Any]) -> None:
        record = {"run_id": self.run_id, "type": record_type}
        record.update(payload)
        self.trace.append(record)

    def _execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self.toolbox, name, None)
        if handler is None:
            return {"ok": False, "error": "TOOL_NOT_IMPLEMENTED", "summary": "工具 %s 未实现" % name}
        try:
            return handler(**args)
        except TypeError as exc:
            return {
                "ok": False,
                "error": "BAD_ARGUMENTS",
                "detail": str(exc),
                "summary": "工具参数不匹配：%s" % exc,
            }
        except Exception as exc:  # pragma: no cover - 防御性
            return {
                "ok": False,
                "error": "TOOL_EXCEPTION: %s" % exc,
                "summary": "工具执行异常：%s" % exc,
            }

    # ------------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        facts = initial_facts(self.task)
        history: List[Dict[str, Any]] = []
        steps = 0
        tool_calls = 0
        repeats: Dict[str, int] = {}
        status = LIMIT_STATUS
        limit_reason = ""
        claims: Dict[str, Any] = {}
        model_error: Optional[str] = None
        degraded = False

        max_steps = int(self.limits.get("max_steps") or 12)
        max_tool_calls = int(self.limits.get("max_tool_calls") or 8)
        max_repeat = int(self.limits.get("max_repeat") or 2)

        self._emit(
            "run_start",
            {
                "case_id": self.case_id,
                "run_index": self.run_index,
                "task": self.task,
                "model": self.model.describe(),
                "limits": dict(self.limits),
            },
        )

        while True:
            if steps >= max_steps:
                status = LIMIT_STATUS
                limit_reason = "达到最大步数 %s" % max_steps
                break

            steps += 1
            budget = {
                "steps_left": max(0, max_steps - steps),
                "tool_calls_left": max(0, max_tool_calls - tool_calls),
            }
            state = {
                "task": self.task,
                "facts": facts,
                "history": history,
                "budget": budget,
                "limits": self.limits,
            }

            try:
                raw_action = self.model.decide(state)
            except ModelError as exc:
                if not degraded and self.fallback_model is not None:
                    degraded = True
                    self._emit(
                        "model_degraded",
                        {
                            "step": steps,
                            "error": str(exc),
                            "from_model": self.model.name,
                            "to_model": self.fallback_model.name,
                            "note": "保留已获得的事实，由离线策略模型续跑，结论不静默降级",
                        },
                    )
                    self.model = self.fallback_model
                    steps -= 1
                    continue
                model_error = str(exc)
                status = "failed"
                break

            try:
                action = normalize_action(raw_action)
            except ActionError as exc:
                facts["invalid_actions"] = int(facts.get("invalid_actions") or 0) + 1
                self._emit("invalid_action", {"step": steps, "error": str(exc), "raw": raw_action})
                history.append(
                    {
                        "action": raw_action if isinstance(raw_action, dict) else {"raw": str(raw_action)},
                        "observation": {"ok": False, "error": "INVALID_ACTION", "detail": str(exc)},
                    }
                )
                if facts["invalid_actions"] >= MAX_INVALID_ACTIONS:
                    status = "failed"
                    limit_reason = "模型连续输出非法动作 %s 次" % facts["invalid_actions"]
                    break
                continue

            self._emit(
                "model_action",
                {
                    "step": steps,
                    "rationale": action.get("rationale"),
                    "action": {
                        "type": action["type"],
                        "name": action.get("name"),
                        "args": action.get("args"),
                    }
                    if action["type"] == "tool"
                    else {"type": "final", "claims": action.get("claims")},
                    "model": self.model.name,
                    "meta": dict(self.model.last_meta),
                    "budget": budget,
                },
            )

            if action["type"] == "final":
                claims = action.get("claims") or {}
                status = "completed"
                break

            name = action["name"]
            args = action["args"]

            if name not in TOOL_NAMES:
                observation = {
                    "ok": False,
                    "error": "TOOL_NOT_AVAILABLE",
                    "detail": "工具 %r 不存在。本系统只有查/存草稿能力，没有发布或确认类工具。" % name,
                    "summary": "工具 %r 不可用（不存在发布类能力）" % name,
                }
                self._emit(
                    "tool_rejected",
                    {"step": steps, "tool": name, "args": args, "reason": "TOOL_NOT_AVAILABLE"},
                )
                history.append({"action": action, "observation": observation})
                continue

            arg_errors = validate_args(name, args)
            if arg_errors:
                observation = {
                    "ok": False,
                    "error": "INVALID_ARGS",
                    "detail": "; ".join(arg_errors),
                    "summary": "参数校验失败：%s" % "; ".join(arg_errors),
                }
                self._emit(
                    "tool_rejected",
                    {"step": steps, "tool": name, "args": args, "reason": "INVALID_ARGS", "detail": arg_errors},
                )
                history.append({"action": action, "observation": observation})
                continue

            repeat_key = name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            used = repeats.get(repeat_key, 0)
            repeats[repeat_key] = used + 1
            if used >= max_repeat:
                observation = {
                    "ok": False,
                    "error": "REPEAT_LIMIT",
                    "detail": "同一工具同一参数已调用 %s 次，达到上限，请更换策略或给出最终结论。" % used,
                    "summary": "重复调用被拒绝：%s %s" % (name, json.dumps(args, ensure_ascii=False)),
                }
                self._emit(
                    "tool_rejected", {"step": steps, "tool": name, "args": args, "reason": "REPEAT_LIMIT"}
                )
                history.append({"action": action, "observation": observation})
                continue

            if tool_calls >= max_tool_calls:
                status = LIMIT_STATUS
                limit_reason = "达到最大工具调用次数 %s" % max_tool_calls
                self._emit(
                    "limit_reached",
                    {"step": steps, "tool_calls_used": tool_calls, "max_tool_calls": max_tool_calls},
                )
                break

            result = self._execute(name, args)
            tool_calls += 1
            update_facts(facts, name, args, result)

            observation = {
                "ok": bool(result.get("ok")),
                "tool": name,
                "summary": result.get("summary"),
                "result": result,
                "budget": {
                    "steps_left": max(0, max_steps - steps),
                    "tool_calls_left": max(0, max_tool_calls - tool_calls),
                },
            }
            history.append({"action": action, "observation": observation})
            self._emit(
                "tool_result",
                {
                    "step": steps,
                    "tool": name,
                    "args": args,
                    "ok": result.get("ok"),
                    "save_status": result.get("save_status"),
                    "summary": result.get("summary"),
                    "result": result,
                    "tool_calls_used": tool_calls,
                },
            )

        # ------------------------------------------------------------ 收尾
        verified_claims, overrides = verifier_mod.verify(claims, facts)
        if verifier_mod.has_critical(overrides):
            status = "failed"
            if not limit_reason:
                limit_reason = "校验层发现契约违约：%s" % "、".join(
                    item["code"] for item in overrides if item["code"] in verifier_mod.CRITICAL_CODES
                )
        if not claims and status == "completed":
            status = "failed"
            limit_reason = "循环结束但没有拿到最终结论"

        # 交付物里包含「草稿保存状态」：保存失败或无法核实时段，整次运行就不算完成。
        # 这样 CLI 退出码 2 与 output.status=failed 一致，避免「看起来完成了其实没存下来」。
        if status == "completed":
            last_save = facts.get("last_save") or {}
            if last_save.get("save_status") == "failed":
                status = "failed"
                limit_reason = "草稿保存失败：%s" % (last_save.get("error") or "unknown")
            elif int(facts.get("slot_query_failures") or 0) >= 2 and not last_save:
                status = "failed"
                limit_reason = "时段查询失败 %s 次，无法核实空闲时段，任务未完成" % facts[
                    "slot_query_failures"
                ]

        self._emit(
            "run_end",
            {
                "status": status,
                "limit_reason": limit_reason,
                "steps_used": steps,
                "tool_calls_used": tool_calls,
                "claims": verified_claims,
                "overrides": overrides,
                "model_error": model_error,
                "degraded": degraded,
            },
        )

        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "run_index": self.run_index,
            "status": status,
            "limit_reason": limit_reason,
            "claims": verified_claims,
            "overrides": overrides,
            "facts": facts,
            "trace": self.trace,
            "usage": {
                "steps_used": steps,
                "max_steps": max_steps,
                "tool_calls_used": tool_calls,
                "max_tool_calls": max_tool_calls,
                "max_repeat": max_repeat,
            },
            "model_info": {
                "primary": self.primary_model_name,
                "final": self.model.name,
                "kind": self.model.kind,
                "degraded": degraded,
            },
            "model_error": model_error,
        }

"""渲染层：把「工具事实 + 校验后的声明」变成交付物。

原则：
* 报告里的保存状态 / 草稿 ID / 发布状态，全部来自工具台账与存储快照，
  不采用模型自述 —— 这是「虚报保存成功属于关键缺陷」的结构性解法。
* published 恒为 False，且措辞固定为「草稿 / 待确认 / 未发布」，不可能出现「已发布」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .verifier import has_critical


def _decision(run_status: str, facts: Dict[str, Any]) -> str:
    last_save = facts.get("last_save") or {}
    save_status = last_save.get("save_status")
    if save_status == "saved":
        record_pending = bool(last_save.get("draft_status") == "pending_confirmation") or bool(
            last_save.get("pending_confirmations")
        )
        return "draft_saved_pending_confirmation" if record_pending else "draft_saved"
    if save_status == "duplicate":
        return "draft_exists"
    if save_status == "failed":
        return "save_failed"
    if save_status == "denied":
        return "policy_denied"
    if run_status == "incomplete_limit_reached":
        return "not_attempted"

    review = facts.get("review") or {}
    if review.get("missing"):
        return "needs_info"
    if review.get("block"):
        return "review_blocked"
    if int(facts.get("slot_query_failures") or 0) >= 2:
        return "tool_failure"
    brief = facts.get("brief") or {}
    slots = facts.get("slots") or {}
    conflict = slots.get("conflict")
    if conflict and str(brief.get("time_constraint") or "soft") == "hard":
        return "conflict_blocked"
    if conflict and not (slots.get("candidates") or []):
        return "conflict_blocked"
    return "not_attempted"


def _pending_confirmations(
    facts: Dict[str, Any], draft_record: Optional[Dict[str, Any]], decision: str
) -> List[Dict[str, Any]]:
    if draft_record and draft_record.get("pending_confirmations"):
        return [
            {
                "id": item.get("id"),
                "reason": item.get("reason"),
                "needs": item.get("needs"),
            }
            for item in draft_record["pending_confirmations"]
        ]

    pending: List[Dict[str, Any]] = []
    review = facts.get("review") or {}
    brief = facts.get("brief") or {}
    if review.get("missing"):
        pending.append(
            {
                "id": "C-INFO",
                "reason": "素材信息缺失：%s" % "；".join(item.get("message", "") for item in review["missing"]),
                "needs": "material_metadata",
            }
        )
    for group in ("block", "confirm"):
        for finding in review.get(group) or []:
            pending.append(
                {
                    "id": "C-%s" % finding.get("rule_id"),
                    "reason": finding.get("message"),
                    "needs": "manual_review",
                }
            )
    conflict = (facts.get("slots") or {}).get("conflict") or {}
    if decision == "conflict_blocked" and conflict:
        occupant = conflict.get("occupant") or conflict.get("reason")
        pending.append(
            {
                "id": "C-HARD" if str(brief.get("time_constraint")) == "hard" else "C-CONFLICT",
                "reason": "请求时段 %s 不可用（占用/禁发：%s），需用户决定是否改期或放宽硬性要求"
                % (brief.get("requested_time"), occupant),
                "needs": "user_decision",
            }
        )
    return pending


def _limitations(run: Dict[str, Any], facts: Dict[str, Any]) -> List[str]:
    limits: List[str] = []
    model_info = run.get("model_info") or {}
    if model_info.get("kind") == "offline":
        limits.append(
            "本次运行使用离线策略模型（deterministic policy），只验证流程与护栏，不代表真实模型的决策效果。"
        )
    if model_info.get("degraded"):
        limits.append(
            "真实模型调用失败，已降级为离线策略模型续跑：%s" % (run.get("model_error") or "未知错误")
        )
    if run.get("status") == "incomplete_limit_reached":
        limits.append("运行触达上限（%s），结论为「未完成」，不代表审核结论。" % run.get("limit_reason"))
    if int(facts.get("slot_query_failures") or 0) >= 2:
        limits.append(
            "时段查询连续失败 %s 次，未核实目标时段是否空闲，因此没有保存草稿。" % facts["slot_query_failures"]
        )
    last_save = facts.get("last_save") or {}
    if last_save.get("save_status") == "failed":
        limits.append(
            "草稿保存失败：%s（尝试 %s 次）" % (last_save.get("error"), len(facts.get("save_attempts") or []))
        )
    if last_save.get("save_status") == "denied":
        limits.append("保存被策略拒绝：%s" % last_save.get("error"))
    if facts.get("assets_fetched") and facts.get("assets_still_missing"):
        limits.append(
            "补查素材服务后仍缺失：%s（上游确实没有该信息，未做任何假设）"
            % "、".join(facts["assets_still_missing"])
        )
    return limits


def build_output(
    run: Dict[str, Any],
    case: Dict[str, Any],
    store: Any,
    trace_path: Optional[str] = None,
) -> Dict[str, Any]:
    facts = run.get("facts") or {}
    claims = run.get("claims") or {}
    brief = facts.get("brief") or {}
    material_raw = facts.get("material") or {}
    review = facts.get("review") or {}
    last_save = facts.get("last_save") or {}
    material_id = facts.get("material_id") or case.get("material_id")

    draft_record = None
    if last_save.get("draft_id"):
        draft_record = store.get(last_save["draft_id"])

    decision = _decision(run.get("status"), facts)
    review_status = claims.get("review_status") or review.get("expected_status") or "pass"

    suggested_slot = claims.get("suggested_slot")
    if not suggested_slot and last_save.get("save_status") in ("saved", "duplicate"):
        suggested_slot = dict(facts.get("last_save_slot") or {})
    # 没有落成草稿的结论不报「建议时段」：资料缺失/审核阻断时给个时间点会误导运营。
    if not last_save and decision in (
        "needs_info",
        "review_blocked",
        "not_attempted",
        "conflict_blocked",
    ):
        suggested_slot = None

    findings = facts.get("findings") or []
    risk_reasons = [
        {
            "rule_id": item.get("rule_id"),
            "name": item.get("name"),
            "category": item.get("category"),
            "severity": item.get("severity"),
            "kind": item.get("kind"),
            "message": item.get("message"),
            "evidence": item.get("evidence") or {},
        }
        for item in findings
    ]

    store_summary = (
        store.summary(material_id=material_id)
        if material_id
        else {"total": 0, "active": [], "superseded": []}
    )

    material_obj = {
        "material_id": material_id,
        "title": material_raw.get("title"),
        "review_status": review_status,
        "decision": decision,
        "risk_reasons": risk_reasons,
        "requested_slot": {
            "date": brief.get("requested_date"),
            "time": brief.get("requested_time"),
            "channel": brief.get("channel"),
            "time_constraint": brief.get("time_constraint") or "soft",
        },
        "suggested_slot": suggested_slot,
        "conflict": (facts.get("slots") or {}).get("conflict"),
        "pending_confirmations": _pending_confirmations(facts, draft_record, decision),
        "draft": {
            "save_status": last_save.get("save_status") or "not_attempted",
            "draft_id": last_save.get("draft_id"),
            "draft_status": (draft_record or {}).get("status"),
            "attempts": len(facts.get("save_attempts") or []),
            "error": last_save.get("error"),
            "superseded": last_save.get("superseded") or [],
            "attempted_slot": facts.get("last_save_slot"),
        },
        "published": False,
        "evidence": {
            "rules_checked": review.get("review_rule_ids") or [],
            "scheduling_rule_ids": [item.get("rule_id") for item in review.get("scheduling") or []],
            "assets_fetched": bool(facts.get("assets_fetched")),
            "slot_query_failures": int(facts.get("slot_query_failures") or 0),
            "candidates": [item.get("time") for item in ((facts.get("slots") or {}).get("candidates") or [])],
        },
    }

    return {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "status": run.get("status"),
        "limit_reason": run.get("limit_reason") or None,
        "model": run.get("model_info"),
        "limits": run.get("usage"),
        "no_publish_capability": True,
        "materials": [material_obj],
        "verifier": {
            "overrides": run.get("overrides") or [],
            "critical": has_critical(run.get("overrides") or []),
        },
        "store": {
            "total": store_summary.get("total", 0),
            "active": store_summary.get("active", []),
            "superseded": store_summary.get("superseded", []),
        },
        "limitations": _limitations(run, facts),
        "trace_path": trace_path,
    }


def build_report(output: Dict[str, Any], trace: List[Dict[str, Any]], case: Dict[str, Any]) -> str:
    material = (output.get("materials") or [{}])[0]
    lines: List[str] = []
    lines.append("# 运行记录：%s" % case.get("case_id"))
    lines.append("")
    lines.append("> %s" % (case.get("title") or ""))
    lines.append("")
    lines.append(
        "**运行状态**：`%s` ｜ **模型**：`%s` ｜ **步数/工具调用**：%s/%s"
        % (
            output.get("status"),
            (output.get("model") or {}).get("final"),
            (output.get("limits") or {}).get("steps_used"),
            (output.get("limits") or {}).get("tool_calls_used"),
        )
    )
    if output.get("limit_reason"):
        lines.append("")
        lines.append("**未完成原因**：%s" % output["limit_reason"])
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append("| 素材 | %s %s |" % (material.get("material_id"), material.get("title") or ""))
    lines.append("| 审核状态 | `%s` |" % material.get("review_status"))
    lines.append("| 处置结论 | `%s` |" % material.get("decision"))
    requested = material.get("requested_slot") or {}
    lines.append(
        "| 用户要求 | %s %s（%s） |"
        % (requested.get("date"), requested.get("time"), requested.get("time_constraint"))
    )
    suggested = material.get("suggested_slot") or {}
    lines.append("| 建议时段 | %s |" % (suggested.get("time") or "无"))
    draft = material.get("draft") or {}
    lines.append(
        "| 草稿保存状态 | `%s`%s |"
        % (
            draft.get("save_status"),
            "（draft_id=%s）" % draft.get("draft_id") if draft.get("draft_id") else "",
        )
    )
    lines.append("| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |")
    lines.append("")

    reasons = material.get("risk_reasons") or []
    lines.append("## 风险与冲突")
    lines.append("")
    if reasons:
        lines.append("| 规则 | 分类 | 级别 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for item in reasons:
            lines.append(
                "| %s | %s | %s | %s |"
                % (item.get("rule_id"), item.get("category"), item.get("severity"), item.get("message"))
            )
    else:
        lines.append("无规则命中。")
    lines.append("")

    pending = material.get("pending_confirmations") or []
    lines.append("## 待确认项")
    lines.append("")
    if pending:
        for item in pending:
            lines.append("- `%s`（%s）：%s" % (item.get("id"), item.get("needs"), item.get("reason")))
    else:
        lines.append("无。")
    lines.append("")

    lines.append("## 决策与执行链（模型动作 → 工具结果）")
    lines.append("")
    step_lines: List[Dict[str, Any]] = []
    for record in trace:
        if record.get("type") == "model_action":
            step_lines.append(
                {
                    "step": record.get("step"),
                    "action": record.get("action"),
                    "rationale": record.get("rationale"),
                }
            )
        elif record.get("type") == "tool_result":
            if step_lines:
                step_lines[-1]["observation"] = record.get("summary")
                step_lines[-1]["save_status"] = record.get("save_status")
        elif record.get("type") in ("tool_rejected", "invalid_action", "model_degraded", "limit_reached"):
            step_lines.append(
                {
                    "step": record.get("step"),
                    "action": {"type": record.get("type")},
                    "rationale": record.get("reason") or record.get("error") or "",
                    "observation": record.get("detail") or "",
                }
            )
    for item in step_lines:
        action = item.get("action") or {}
        if action.get("type") == "tool":
            head = "`%s` %s" % (action.get("name"), action.get("args"))
        elif action.get("type") == "final":
            head = "final: review_status=%s" % (action.get("claims") or {}).get("review_status")
        else:
            head = str(action.get("type"))
        lines.append("%s. %s" % (item.get("step"), head))
        if item.get("rationale"):
            lines.append("   - 决策依据：%s" % item["rationale"])
        if item.get("observation"):
            lines.append("   - 观察结果：%s" % item["observation"])
    lines.append("")

    overrides = (output.get("verifier") or {}).get("overrides") or []
    if overrides:
        lines.append("## 校验层拦截")
        lines.append("")
        for item in overrides:
            lines.append("- `%s`：%s" % (item.get("code"), item.get("detail")))
        lines.append("")

    lines.append("## 限制与说明")
    lines.append("")
    limitations = output.get("limitations") or []
    if limitations:
        for item in limitations:
            lines.append("- %s" % item)
    else:
        lines.append("- 无额外限制。")
    lines.append("")
    lines.append("---")
    lines.append(
        "本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。"
    )
    lines.append("")
    return "\n".join(lines)

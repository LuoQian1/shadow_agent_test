"""离线策略模型：没有网络/没有 key 时的兜底，也是回归测试的确定性基线。

它和「写死脚本」的区别（也是我在 docs/04 里主动说明的边界）：
* 它只读 state["facts"] 与 history，不读 case_id、不读题面文本，
  同一个函数服务全部 11 个用例，没有 per-case 分支。
* 它真的会「观察后调整」：补查后仍缺才收尾、查询失败会重试、保存失败会重试一次、
  保存被策略拒绝就不再重试、时段被占则改投最近可用时段。
* 但它仍然不是「模型决策效果」的证据：它的决策规则是人写的。
  真实模型的两条 trace 在 examples/traces/real/ 下，那才是 ReAct 的证据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Model


def _tool(name: str, args: Dict[str, Any], rationale: str) -> Dict[str, Any]:
    return {"rationale": rationale, "action": {"type": "tool", "name": name, "args": args}}


def _slot(brief: Dict[str, Any], time_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not time_text:
        return None
    return {
        "date": brief.get("requested_date"),
        "time": time_text,
        "channel": brief.get("channel"),
    }


def _final(
    review_status: str,
    brief: Dict[str, Any],
    time_text: Optional[str],
    save_status: str,
    rule_ids: List[str],
    notes: str,
    rationale: str = "",
) -> Dict[str, Any]:
    return {
        "rationale": rationale or notes,
        "action": {
            "type": "final",
            "claims": {
                "review_status": review_status,
                "suggested_slot": _slot(brief, time_text),
                "save_status": save_status,
                "risk_rule_ids": list(rule_ids or []),
                "notes": notes,
            },
        },
    }


class OfflinePolicyModel(Model):
    name = "offline-policy"
    kind = "offline"

    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task = state.get("task") or {}
        facts = state.get("facts") or {}
        material_id = task.get("material_id")
        brief = facts.get("brief") or {}
        review = facts.get("review") or {}
        rule_ids = review.get("review_rule_ids") or []
        self.call_count += 1

        # 1) 素材
        if facts.get("material") is None:
            return _tool(
                "read_material",
                {"material_id": material_id},
                "先读取素材元数据，确认字段是否齐全",
            )
        if facts.get("material_found") is False:
            return _final("blocked", brief, None, "not_attempted", rule_ids, "素材不存在，无法排期")

        # 2) 工单
        if not brief:
            return _tool(
                "get_publish_brief",
                {"material_id": material_id, "brief_id": task.get("brief_id")},
                "读取工单，确认时间是硬性还是软性要求",
            )

        # 3) 规则
        if not review:
            return _tool(
                "check_rules",
                {"material_id": material_id, "channel": brief.get("channel")},
                "按规则表审核素材，先判断是否存在阻断级风险",
            )

        # 4) 资料缺失 → 补查
        if review.get("missing") and not facts.get("assets_fetched"):
            return _tool(
                "fetch_material_assets",
                {"material_id": material_id},
                "资料缺失，先向上游补查再决定，不能假设授权已具备",
            )
        if review.get("missing"):
            return _final(
                "missing_info",
                brief,
                None,
                "not_attempted",
                rule_ids,
                "补查后资料仍然缺失，需用户补齐时长/授权信息后再排期，本次未保存草稿",
                "补查无效，资料确实缺失，收尾并请用户补料",
            )

        # 5) 阻断级风险
        if review.get("block"):
            return _final(
                "blocked",
                brief,
                None,
                "not_attempted",
                rule_ids,
                "命中阻断级规则，不可排期，已转人工/法务确认，本次未保存草稿",
                "存在 block 级风险，直接阻断",
            )

        # 6) 查重
        if facts.get("drafts") is None:
            return _tool(
                "list_drafts",
                {"material_id": material_id},
                "先确认是否已有草稿，避免重复提交",
            )

        # 7) 保存结果回看
        last_save = facts.get("last_save")
        if last_save:
            status = last_save.get("save_status")
            if status in ("saved", "duplicate"):
                note = (
                    "草稿已存在，本次未新建（幂等命中），草稿状态为待确认，尚未发布"
                    if status == "duplicate"
                    else "草稿已保存，仅为草稿、尚未发布，等待用户确认"
                )
                return _final(
                    review.get("expected_status") or "pass",
                    brief,
                    (facts.get("last_save_slot") or {}).get("time"),
                    status,
                    rule_ids,
                    note,
                    "保存已成功，收尾",
                )
            if status == "failed" and int(last_save.get("attempt") or 0) < 2:
                return _tool(
                    "save_schedule_draft",
                    facts.get("last_save_args") or {},
                    "保存失败，先用相同参数重试一次",
                )
            if status in ("failed", "denied"):
                reason = last_save.get("error") or "unknown"
                attempted = (facts.get("last_save_slot") or {}).get("time")
                return _final(
                    review.get("expected_status") or "pass",
                    brief,
                    attempted if status == "failed" else None,
                    "not_attempted",
                    rule_ids,
                    "草稿保存未成功（%s），需要人工处理，本次没有生成草稿" % reason,
                    "保存失败/被拒绝且重试无效，如实报告失败",
                )

        # 8) 时段
        slots = facts.get("slots")
        if slots is None:
            failures = int(facts.get("slot_query_failures") or 0)
            if failures >= 2:
                return _final(
                    review.get("expected_status") or "pass",
                    brief,
                    None,
                    "not_attempted",
                    rule_ids,
                    "时段查询连续失败，无法确认目标时段是否空闲，本次未保存草稿",
                    "时段服务不可用且重试无效，不做无依据的保存",
                )
            return _tool(
                "query_slots",
                {
                    "date": brief.get("requested_date"),
                    "channel": brief.get("channel"),
                    "near_time": brief.get("requested_time"),
                },
                "查询请求时段是否可用以及有哪些替代时段",
            )

        requested = brief.get("requested_time")
        conflict = slots.get("conflict")
        if not conflict:
            target = requested
            rationale = "请求时段可用，直接在请求时段保存草稿"
        elif str(brief.get("time_constraint")) == "hard":
            return _final(
                review.get("expected_status") or "pass",
                brief,
                None,
                "not_attempted",
                rule_ids,
                "硬性发布日期 %s %s 已被占用；未经用户同意不得改动，已升级为待确认项，未保存任何草稿"
                % (brief.get("requested_date"), requested),
                "硬性要求不可擅自变更，阻断并升级",
            )
        else:
            candidates = [item.get("time") for item in (slots.get("candidates") or []) if item.get("time")]
            if not candidates:
                return _final(
                    review.get("expected_status") or "pass",
                    brief,
                    None,
                    "not_attempted",
                    rule_ids,
                    "请求时段冲突且当天没有可用替代时段，需用户重新指定日期，未保存草稿",
                    "无可行时段，升级给用户",
                )
            target = candidates[0]
            rationale = "请求时段被占用，改到最近的可用时段 %s，并标记待确认" % target

        return _tool(
            "save_schedule_draft",
            {
                "material_id": material_id,
                "date": brief.get("requested_date"),
                "time": target,
                "channel": brief.get("channel"),
            },
            rationale,
        )

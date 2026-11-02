"""工具实现（全部为本地函数，无网络）。

三层防护都在这一层落地：
1. 能力缺失   —— 不存在 publish / confirm 类工具。
2. 策略拒绝   —— 硬性要求不符、缺失资料、block 级规则命中时，save 直接 denied 且不写盘。
                 注意：这里会「重算规则」，不信任模型传入的任何结论。
3. 故障注入   —— 用例可以按工具注入失败，用于验证「保存失败必须明确返回失败」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..data import Dataset
from ..domain import rules as rules_engine
from ..domain.models import Brief, Material
from ..domain.slots import SlotPlanner, to_minutes
from ..store.draft_store import DraftStore


class FaultInjector(object):
    """按工具名注入确定性故障：前 N 次调用直接返回错误。"""

    def __init__(self, faults: Optional[Dict[str, Any]] = None) -> None:
        self.specs: Dict[str, Dict[str, Any]] = dict(faults or {})
        self.counters: Dict[str, int] = {}

    def take(self, tool_name: str) -> Optional[Dict[str, Any]]:
        spec = self.specs.get(tool_name)
        if not isinstance(spec, dict):
            return None
        used = self.counters.get(tool_name, 0)
        self.counters[tool_name] = used + 1
        fail_times = int(spec.get("fail_times") or 0)
        if used < fail_times:
            return {"error": spec.get("error") or "injected failure", "fault": "injected"}
        return None


class ToolBox(object):
    def __init__(
        self,
        dataset: Dataset,
        planner: SlotPlanner,
        store: DraftStore,
        session_material: Optional[str] = None,
        session_brief: Optional[Dict[str, Any]] = None,
        faults: Optional[Dict[str, Any]] = None,
        overlay_entries: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.dataset = dataset
        self.planner = planner
        self.store = store
        self.session_material = session_material
        self.session_brief = session_brief
        self.faults = FaultInjector(faults)
        self.overlay_entries = list(overlay_entries or [])
        self.save_attempts = 0
        self.slot_query_attempts = 0

    # ------------------------------------------------------------- internals
    def _brief_for(self, material_id: Optional[str]) -> Dict[str, Any]:
        """会话绑定的 brief 优先。模型无法通过参数替换需求 —— 防止「改需求来消除冲突」。"""
        if self.session_brief and (
            self.session_material is None or str(material_id) == str(self.session_material)
        ):
            return dict(self.session_brief)
        found = self.dataset.brief_for_material(material_id)
        return dict(found or {})

    def entries(self) -> List[Dict[str, Any]]:
        """静态排期 + 用例 overlay + 活跃草稿，统一成占用条目。"""
        return self.planner.build_entries(
            self.dataset.schedule,
            self.store.list_drafts(),
            self.overlay_entries,
        )

    def _review(self, material_id: str, brief: Dict[str, Any]):
        material_raw = self.dataset.material(material_id) or {}
        return rules_engine.evaluate_and_summarize(
            material_raw,
            brief,
            self.entries(),
            self.dataset.rules,
            self.dataset.blacklist,
            self_material=str(material_id),
        )

    # ---------------------------------------------------------------- tools
    def read_material(self, material_id: str) -> Dict[str, Any]:
        raw = self.dataset.material(material_id)
        if raw is None:
            return {
                "ok": False,
                "found": False,
                "error": "MATERIAL_NOT_FOUND",
                "material_id": material_id,
                "summary": "素材 %s 不存在" % material_id,
            }
        material = Material(raw)
        missing = material.missing_metadata()
        return {
            "ok": True,
            "found": True,
            "material": material.to_dict(),
            "missing_fields": missing,
            "duration_min": material.duration_minutes(),
            "summary": "素材 %s「%s」读取成功；缺失元数据：%s"
            % (material_id, raw.get("title"), "、".join(missing) or "无"),
        }

    def get_publish_brief(self, material_id: str, brief_id: Optional[str] = None) -> Dict[str, Any]:
        brief_raw = self.dataset.brief(brief_id) if brief_id else None
        if brief_raw is None:
            brief_raw = self._brief_for(material_id) or None
        if not brief_raw:
            return {
                "ok": False,
                "found": False,
                "error": "BRIEF_NOT_FOUND",
                "summary": "素材 %s 没有对应的发布需求" % material_id,
            }
        brief = Brief(brief_raw)
        return {
            "ok": True,
            "found": True,
            "brief": brief.to_dict(),
            "summary": "需求 %s：%s %s 发布，约束=%s，截止=%s"
            % (
                brief.brief_id,
                brief.requested_date,
                brief.requested_time,
                brief.time_constraint,
                brief.get("deadline"),
            ),
        }

    def check_rules(self, material_id: str, channel: Optional[str] = None) -> Dict[str, Any]:
        raw = self.dataset.material(material_id)
        if raw is None:
            return {
                "ok": False,
                "error": "MATERIAL_NOT_FOUND",
                "summary": "素材 %s 不存在" % material_id,
            }
        brief = self._brief_for(material_id)
        if channel:
            brief = dict(brief)
            brief["channel"] = channel
        findings, review = self._review(material_id, brief)
        return {
            "ok": True,
            "findings": [f.to_dict() for f in findings],
            "review": review,
            "summary": "规则校验完成：expected_status=%s，命中 %s"
            % (review["expected_status"], "、".join(review["all_rule_ids"]) or "无"),
        }

    def query_slots(
        self,
        date: str,
        channel: str,
        duration_min: Optional[int] = None,
        near_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.slot_query_attempts += 1
        fault = self.faults.take("query_slots")
        if fault:
            return {
                "ok": False,
                "error": fault["error"],
                "fault": "injected",
                "attempt": self.slot_query_attempts,
                "summary": "时段查询失败（第 %s 次）：%s" % (self.slot_query_attempts, fault["error"]),
            }
        raw = self.dataset.material(self.session_material) or {}
        if duration_min is None:
            duration_min = Material(raw).duration_minutes()
        plan = self.planner.plan(
            date=date,
            channel=channel,
            duration_min=int(duration_min or 1),
            near_time=near_time,
            entries=self.entries(),
            self_material=self.session_material,
        )
        candidates = plan.get("candidates") or []
        plan.update(
            {
                "ok": True,
                "attempt": self.slot_query_attempts,
                "summary": "%s %s 时段查询：冲突=%s，候选=%s"
                % (
                    date,
                    channel,
                    (plan.get("conflict") or {}).get("type") or "无",
                    "、".join(item["time"] for item in candidates) or "无",
                ),
            }
        )
        return plan

    def fetch_material_assets(self, material_id: str) -> Dict[str, Any]:
        """模拟向素材服务补查。上游确实没有的字段，补查也不会变出来。"""
        raw = self.dataset.material(material_id)
        if raw is None:
            return {
                "ok": False,
                "error": "MATERIAL_NOT_FOUND",
                "summary": "素材 %s 不存在" % material_id,
            }
        fault = self.faults.take("fetch_material_assets")
        if fault:
            return {
                "ok": False,
                "error": fault["error"],
                "fault": "injected",
                "summary": "素材服务不可用：%s" % fault["error"],
            }
        assets = {
            "music": {"license": raw.get("music_license"), "track_id": None},
            "portrait": {"has_person": raw.get("has_person"), "consent": raw.get("portrait_consent")},
            "file": {"ref": raw.get("file_ref"), "probe_duration_sec": raw.get("duration_sec")},
            "brand": {"license": raw.get("brand_license")},
        }
        still_missing = []
        if raw.get("music_license") is None:
            still_missing.append("music_license")
        if raw.get("has_person") and raw.get("portrait_consent") is None:
            still_missing.append("portrait_consent")
        if raw.get("duration_sec") is None:
            still_missing.append("duration_sec")
        if raw.get("brand_license") is None:
            still_missing.append("brand_license")
        return {
            "ok": True,
            "upstream": "mock-asset-service",
            "assets": assets,
            "still_missing": still_missing,
            "summary": "补查完成，仍缺失：%s" % ("、".join(still_missing) or "无"),
        }

    def list_drafts(self, material_id: Optional[str] = None) -> Dict[str, Any]:
        target = material_id or self.session_material
        records = self.store.list_drafts(material_id=target)
        brief = []
        for record in records:
            slot = record.get("slot") or {}
            brief.append(
                {
                    "draft_id": record.get("draft_id"),
                    "date": slot.get("date"),
                    "time": slot.get("time"),
                    "status": record.get("status"),
                    "superseded_by": record.get("superseded_by"),
                }
            )
        active = [item for item in brief if item["status"] in ("draft", "pending_confirmation")]
        return {
            "ok": True,
            "drafts": brief,
            "active_count": len(active),
            "summary": "素材 %s 已有草稿 %s 份（活跃 %s 份）" % (target, len(brief), len(active)),
        }

    def save_schedule_draft(
        self,
        material_id: str,
        date: str,
        time: str,
        channel: str,
        duration_min: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.save_attempts += 1
        attempt = self.save_attempts
        raw = self.dataset.material(material_id)
        if raw is None:
            return {
                "ok": False,
                "save_status": "denied",
                "error": "MATERIAL_NOT_FOUND",
                "attempt": attempt,
                "summary": "素材 %s 不存在，拒绝保存" % material_id,
            }

        try:
            to_minutes(time)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "save_status": "denied",
                "error": "INVALID_TIME_FORMAT",
                "attempt": attempt,
                "summary": "时段格式非法：%r" % (time,),
            }

        brief = self._brief_for(material_id)

        # 护栏 1：硬性发布时间不得被改动（未经用户同意）
        if str(brief.get("time_constraint") or "soft") == "hard" and str(time) != str(
            brief.get("requested_time")
        ):
            return {
                "ok": False,
                "save_status": "denied",
                "error": "POLICY_HARD_CONSTRAINT",
                "attempt": attempt,
                "policy": {
                    "time_constraint": "hard",
                    "requested_time": brief.get("requested_time"),
                    "attempted_time": time,
                },
                "summary": "硬性发布要求为 %s，尝试保存 %s 被拒绝；需用户明确同意后才能变更"
                % (brief.get("requested_time"), time),
            }

        # 护栏 2：重算规则，不信任模型结论
        try:
            _, review = self._review(material_id, brief)
        except Exception as exc:  # pragma: no cover - 防御性
            return {
                "ok": False,
                "save_status": "failed",
                "error": "RULE_ENGINE_ERROR: %s" % exc,
                "attempt": attempt,
                "summary": "规则引擎异常，拒绝保存",
            }
        if review["missing"]:
            rule_ids = [f["rule_id"] for f in review["missing"]]
            return {
                "ok": False,
                "save_status": "denied",
                "error": "POLICY_MISSING_INFO",
                "blocked_by": rule_ids,
                "attempt": attempt,
                "summary": "素材资料缺失（%s），不能保存草稿" % "、".join(rule_ids),
            }
        if review["block"]:
            rule_ids = [f["rule_id"] for f in review["block"]]
            return {
                "ok": False,
                "save_status": "denied",
                "error": "POLICY_BLOCK_RULE",
                "blocked_by": rule_ids,
                "attempt": attempt,
                "summary": "命中 block 级规则（%s），不能保存草稿" % "、".join(rule_ids),
            }

        # 护栏 3：目标时段必须真的空闲 —— 不允许把草稿排进别人的时段，
        # 否则「无冲突的排期」会变成一个模型可以自说自话绕过的承诺。
        material_obj = Material(raw)
        try:
            plan = self.planner.plan(
                date=date,
                channel=channel,
                duration_min=int(duration_min or material_obj.duration_minutes()),
                near_time=time,
                entries=self.entries(),
                self_material=str(material_id),
            )
            conflict = plan.get("conflict")
        except Exception as exc:  # pragma: no cover - 防御性
            conflict = {"type": "internal_error", "reason": str(exc)}
        if conflict and conflict.get("type") in ("slot_occupied", "blackout"):
            return {
                "ok": False,
                "save_status": "denied",
                "error": "POLICY_SLOT_CONFLICT",
                "conflict": conflict,
                "attempt": attempt,
                "summary": "目标时段 %s 不可用（%s），拒绝保存，请改到可用时段"
                % (time, conflict.get("type")),
            }

        # 护栏 4：故障注入（模拟磁盘/权限失败）
        fault = self.faults.take("save_schedule_draft")
        if fault:
            return {
                "ok": False,
                "save_status": "failed",
                "error": fault["error"],
                "fault": "injected",
                "attempt": attempt,
                "summary": "草稿保存失败（第 %s 次）：%s" % (attempt, fault["error"]),
            }

        pending: List[Dict[str, Any]] = []
        for finding in review["confirm"]:
            pending.append(
                {
                    "id": "C-%s" % finding["rule_id"],
                    "reason": finding["message"],
                    "needs": "manual_review",
                }
            )
        if str(time) != str(brief.get("requested_time")):
            pending.append(
                {
                    "id": "C-SLOT",
                    "reason": "建议时段 %s 与用户要求 %s 不一致"
                    % (time, brief.get("requested_time")),
                    "needs": "owner_approval",
                }
            )

        material = Material(raw)
        result = self.store.save(
            material_id=material_id,
            date=date,
            time=time,
            channel=channel,
            duration_min=duration_min or material.duration_minutes(),
            brief=brief,
            pending_confirmations=pending,
            review_status=review["expected_status"],
            risk_rule_ids=review["review_rule_ids"],
        )
        status = result.get("save_status")
        record = result.get("record") or {}
        summary_map = {
            "saved": "草稿已保存：%s %s（draft_id=%s，仅草稿未发布）"
            % (date, time, result.get("draft_id")),
            "duplicate": "已存在同一素材同一时段的草稿，返回原 draft_id=%s（未新建）"
            % result.get("draft_id"),
            "failed": "草稿保存失败：%s" % result.get("error"),
        }
        return {
            "ok": status in ("saved", "duplicate"),
            "save_status": status,
            "draft_id": result.get("draft_id"),
            "draft_status": record.get("status"),
            "pending_confirmations": record.get("pending_confirmations") or pending,
            "superseded": result.get("superseded") or [],
            "error": result.get("error"),
            "attempt": attempt,
            "note": note,
            "summary": summary_map.get(status, "保存状态未知"),
        }

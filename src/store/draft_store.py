"""排期草稿存储：幂等 + 原子写 + supersede。

为什么不用「先查再写」：
    两个并行请求同时读到「不存在」，会各写一份，重复提交就破功。
    这里用 os.open(O_CREAT | O_EXCL) 让文件系统充当锁，写入是原子的，
    不存在竞态窗口；已存在则直接返回 duplicate 与原 draft_id。

为什么 draft_id 由代码算而不让模型传：
    幂等键必须不可伪造。模型若能自定义 key，只要换个 key 就能给同一请求造出
    第二份草稿。键 = sha256(material_id|date|time|channel) 前 12 位，完全确定。

为什么同一素材的新时段要 supersede 旧草稿：
    「更换占用时段并重复提交」场景下，运营期望活跃草稿只有一份；
    但历史方案不能物理删除（要可审计），因此把旧记录标记 superseded。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

ACTIVE_STATUSES = ("draft", "pending_confirmation")
DRAFT_ID_PREFIX = "D-"


class DraftStore(object):
    def __init__(self, root: str) -> None:
        self.root = os.path.join(root, "drafts")
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def compute_draft_id(material_id: str, date: str, time: str, channel: str) -> str:
        raw = "|".join([str(material_id), str(date), str(time), str(channel)])
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return DRAFT_ID_PREFIX + digest[:12]

    def path_for(self, draft_id: str) -> str:
        return os.path.join(self.root, "%s.json" % draft_id)

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---------------------------------------------------------------- write
    def save(
        self,
        material_id: str,
        date: str,
        time: str,
        channel: str,
        duration_min: int,
        brief: Dict[str, Any],
        pending_confirmations: Optional[List[Dict[str, Any]]] = None,
        review_status: str = "pass",
        risk_rule_ids: Optional[List[str]] = None,
        created_by: str = "agent",
    ) -> Dict[str, Any]:
        """写一份草稿。返回 save_status ∈ saved / duplicate / failed。"""
        draft_id = self.compute_draft_id(material_id, date, time, channel)
        pending = list(pending_confirmations or [])
        status = "pending_confirmation" if pending else "draft"
        record = {
            "draft_id": draft_id,
            "material_id": material_id,
            "slot": {
                "date": date,
                "time": time,
                "channel": channel,
                "duration_min": int(duration_min or 1),
            },
            "brief_id": brief.get("brief_id"),
            "brief_version": brief.get("brief_version", 1),
            "requirement": {
                "requested_date": brief.get("requested_date"),
                "requested_time": brief.get("requested_time"),
                "time_constraint": brief.get("time_constraint", "soft"),
            },
            "review_status": review_status,
            "risk_rule_ids": list(risk_rule_ids or []),
            "pending_confirmations": pending,
            "status": status,
            "superseded_by": None,
            "created_by": created_by,
            "created_at": self._now(),
        }

        path = self.path_for(draft_id)
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return {
                "save_status": "duplicate",
                "draft_id": draft_id,
                "record": self.get(draft_id),
            }
        except OSError as exc:
            return {
                "save_status": "failed",
                "draft_id": draft_id,
                "error": "%s: %s" % (type(exc).__name__, exc),
            }

        try:
            os.write(fd, payload)
        except OSError as exc:
            # 写失败要清理半成品，否则下次会被误判为 duplicate
            try:
                os.close(fd)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return {
                "save_status": "failed",
                "draft_id": draft_id,
                "error": "%s: %s" % (type(exc).__name__, exc),
            }
        else:
            os.close(fd)

        superseded = self._supersede_others(material_id, date, channel, draft_id)
        return {
            "save_status": "saved",
            "draft_id": draft_id,
            "record": record,
            "superseded": superseded,
        }

    def _supersede_others(self, material_id: str, date: str, channel: str, new_draft_id: str) -> List[str]:
        superseded: List[str] = []
        for record in self.list_drafts(material_id=material_id):
            slot = record.get("slot") or {}
            if record.get("draft_id") == new_draft_id:
                continue
            if slot.get("date") != date or slot.get("channel") != channel:
                continue
            if record.get("status") not in ACTIVE_STATUSES:
                continue
            record["status"] = "superseded"
            record["superseded_by"] = new_draft_id
            if self._rewrite(record):
                superseded.append(record["draft_id"])
        return superseded

    def _rewrite(self, record: Dict[str, Any]) -> bool:
        path = self.path_for(record["draft_id"])
        temp_path = ""
        try:
            handle, temp_path = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            os.replace(temp_path, path)
            return True
        except OSError:
            if temp_path and os.path.isfile(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return False

    # ----------------------------------------------------------------- read
    def get(self, draft_id: str) -> Optional[Dict[str, Any]]:
        path = self.path_for(draft_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def list_drafts(
        self,
        material_id: Optional[str] = None,
        date: Optional[str] = None,
        channel: Optional[str] = None,
        include_inactive: bool = True,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if not os.path.isdir(self.root):
            return records
        for name in sorted(os.listdir(self.root)):
            if not name.endswith(".json") or name.startswith(".tmp-"):
                continue
            try:
                with open(os.path.join(self.root, name), "r", encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                continue
            if material_id and record.get("material_id") != material_id:
                continue
            slot = record.get("slot") or {}
            if date and slot.get("date") != date:
                continue
            if channel and slot.get("channel") != channel:
                continue
            if not include_inactive and record.get("status") not in ACTIVE_STATUSES:
                continue
            records.append(record)
        return records

    def active_drafts(
        self,
        material_id: Optional[str] = None,
        date: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return [
            record
            for record in self.list_drafts(material_id=material_id, date=date, channel=channel)
            if record.get("status") in ACTIVE_STATUSES
        ]

    def superseded_drafts(self, material_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return [
            record
            for record in self.list_drafts(material_id=material_id)
            if record.get("status") == "superseded"
        ]

    def count_active(self, material_id: Optional[str] = None, date: Optional[str] = None) -> int:
        return len(self.active_drafts(material_id=material_id, date=date))

    # ----------------------------------------------------------------- misc
    def seed(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """用例 setup 用：预置一份历史草稿（例如更早的方案），便于复现 supersede。"""
        return self.save(
            material_id=spec["material_id"],
            date=spec["date"],
            time=spec["time"],
            channel=spec.get("channel") or "douyin",
            duration_min=spec.get("duration_min") or 1,
            brief={"brief_id": spec.get("brief_id"), "brief_version": spec.get("brief_version", 1)},
            pending_confirmations=spec.get("pending_confirmations") or [],
            review_status=spec.get("review_status") or "pass",
            created_by="seed",
        )

    def reset(self) -> int:
        """清空草稿库（供 --reset-store / 测试用）。返回删除数量。"""
        removed = 0
        if not os.path.isdir(self.root):
            return removed
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
        return removed

    def summary(self, material_id: Optional[str] = None) -> Dict[str, Any]:
        records = self.list_drafts(material_id=material_id)
        return {
            "total": len(records),
            "active": [r["draft_id"] for r in records if r.get("status") in ACTIVE_STATUSES],
            "superseded": [r["draft_id"] for r in records if r.get("status") == "superseded"],
            "records": records,
        }


def records_as_entries(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把草稿记录投影成排期条目，供时段规划统一计算占用。"""
    entries = []
    for record in records or []:
        slot = record.get("slot") or {}
        entries.append(
            {
                "date": slot.get("date"),
                "time": slot.get("time"),
                "channel": slot.get("channel"),
                "duration_min": slot.get("duration_min") or 30,
                "material_id": record.get("material_id"),
                "status": record.get("status"),
            }
        )
    return entries

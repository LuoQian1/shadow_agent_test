"""领域模型：素材、发布需求、时段、规则命中。

设计取舍：
* Material / Brief 用「包装 dict」而不是逐字段 dataclass —— 素材字段会随业务增删，
  包装法不会因为多/少一个字段就 KeyError，同时保留原始 JSON 便于 trace 取证。
* RuleFinding / Slot 字段固定，用 dataclass，便于类型检查与排序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# R01 使用的最小元数据集合：缺任何一个都无法安全排期
REQUIRED_METADATA_FIELDS = ("title", "owner", "channel", "duration_sec")

# 审核状态严重度：数字越大越严格。verifier 只允许模型「更严格」，不允许「更宽松」。
STATUS_RANK = {"pass": 0, "warn": 1, "blocked": 2, "missing_info": 3}


class Material(object):
    """素材元数据的只读视图。"""

    def __init__(self, raw: Optional[Dict[str, Any]] = None) -> None:
        self.raw: Dict[str, Any] = dict(raw or {})

    @property
    def material_id(self) -> Optional[str]:
        return self.raw.get("material_id")

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def missing_metadata(self) -> List[str]:
        missing = []
        for field_name in REQUIRED_METADATA_FIELDS:
            value = self.raw.get(field_name)
            if value is None or value == "":
                missing.append(field_name)
        return missing

    def duration_minutes(self) -> int:
        """把秒级时长换算成排期占用分钟数（向上取整，最少 1 分钟）。"""
        seconds = self.raw.get("duration_sec")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return 1
        minutes = int(seconds) // 60
        if int(seconds) % 60:
            minutes += 1
        return max(minutes, 1)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.raw)


class Brief(object):
    """发布需求（工单）。time_constraint 是「硬性/软性」的唯一事实来源。"""

    def __init__(self, raw: Optional[Dict[str, Any]] = None) -> None:
        self.raw: Dict[str, Any] = dict(raw or {})

    @property
    def brief_id(self) -> Optional[str]:
        return self.raw.get("brief_id")

    @property
    def material_id(self) -> Optional[str]:
        return self.raw.get("material_id")

    @property
    def requested_date(self) -> Optional[str]:
        return self.raw.get("requested_date")

    @property
    def requested_time(self) -> Optional[str]:
        return self.raw.get("requested_time")

    @property
    def time_constraint(self) -> str:
        """hard = 用户/合同约定的硬性发布时间；soft = 偏好，可协商但需确认。"""
        value = str(self.raw.get("time_constraint") or "soft").lower()
        return value if value in ("hard", "soft") else "soft"

    @property
    def is_hard(self) -> bool:
        return self.time_constraint == "hard"

    @property
    def channel(self) -> Optional[str]:
        return self.raw.get("channel")

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def requested_at(self) -> Optional[str]:
        date = self.requested_date
        time = self.requested_time
        if not date or not time:
            return None
        return "%sT%s" % (date, time)

    def to_dict(self) -> Dict[str, Any]:
        data = dict(self.raw)
        data["time_constraint"] = self.time_constraint
        return data


@dataclass
class Slot:
    """一个可发布的时段。矩阵对齐到 grid_minutes（默认 30 分钟）。"""

    date: str
    time: str
    channel: str
    duration_min: int = 30

    def as_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "time": self.time,
            "channel": self.channel,
            "duration_min": self.duration_min,
        }


@dataclass
class RuleFinding:
    """一条规则命中记录。

    category: review（素材风险，影响审核状态） / scheduling（排期冲突，不影响审核状态）
    kind:     missing（资料缺失，可补查） / risk（明确的风险）
    """

    rule_id: str
    name: str
    category: str
    severity: str
    kind: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "category": self.category,
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


def status_rank(status: Optional[str]) -> int:
    return STATUS_RANK.get(str(status or ""), -1)

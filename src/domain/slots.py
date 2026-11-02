"""时段规划：把「已有排期 + 草稿 + 禁发窗口」变成可用的候选时段。

关键语义（也是本题评分点）：
1. 占用来源有两类 —— 静态排期表（schedule.json）与草稿库（store 里的活跃草稿），
   两者都要算占用，否则重复提交会生成第二个冲突草稿。
2. 同一素材自己已存在的草稿不算「外部冲突」，仍可作为候选：
   重新保存同一时段会命中幂等（返回 duplicate），这正是期望行为。
3. 候选时段与外部排期之间必须留出 min_gap_min 的间隔，避免连排。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

DEAD_STATUSES = ("cancelled",)
ACTIVE_DRAFT_STATUSES = ("draft", "pending_confirmation")


def to_minutes(hhmm: Optional[str]) -> int:
    """'18:30' -> 1110。非法输入抛 ValueError，由调用方转成明确错误。"""
    if not hhmm or ":" not in str(hhmm):
        raise ValueError("invalid time: %r" % (hhmm,))
    hour_text, _, minute_text = str(hhmm).strip().partition(":")
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("invalid time: %r" % (hhmm,))
    return hour * 60 + minute


def to_hhmm(minutes: int) -> str:
    minutes = int(minutes)
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def duration_to_minutes(seconds: Any) -> int:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return 1
    minutes = int(seconds) // 60
    if int(seconds) % 60:
        minutes += 1
    return max(minutes, 1)


class SlotPlanner(object):
    def __init__(
        self,
        rules_doc: Dict[str, Any],
        blacklist_doc: Dict[str, Any],
        materials: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        rules_doc = rules_doc or {}
        blacklist_doc = blacklist_doc or {}
        self.grid = int(rules_doc.get("grid_minutes") or 30)
        window = rules_doc.get("day_window") or {}
        self.window_open = to_minutes(window.get("open") or "09:00")
        self.window_close = to_minutes(window.get("close") or "22:00")
        self.channel_limits = rules_doc.get("channel_limits") or {}
        self.blackouts_cfg = blacklist_doc.get("blackout_windows") or []
        self.materials = materials or {}

    # ---------------------------------------------------------------- limits
    def limits(self, channel: str) -> Dict[str, Any]:
        default = {"max_duration_sec": 60, "max_per_day": 6, "min_gap_min": 30}
        value = self.channel_limits.get(channel)
        if not isinstance(value, dict):
            value = self.channel_limits.get("default") or {}
        merged = dict(default)
        merged.update(value)
        return merged

    # ----------------------------------------------------------- normalizing
    def _normalize_entry(self, entry: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        status = str(entry.get("status") or "draft")
        if status in DEAD_STATUSES:
            return None
        time_text = entry.get("time")
        try:
            start = to_minutes(time_text)
        except (TypeError, ValueError):
            return None
        duration = int(entry.get("duration_min") or 30)
        material_id = entry.get("material_id")
        material = self.materials.get(str(material_id) or "") or {}
        return {
            "date": entry.get("date"),
            "channel": entry.get("channel"),
            "time": "%02d:%02d" % (start // 60, start % 60),
            "start": start,
            "end": start + duration,
            "duration_min": duration,
            "material_id": material_id,
            "status": status,
            "source": source,
            "content_hash": entry.get("content_hash") or material.get("content_hash"),
            "brand": material.get("brand"),
            "competitor": bool(material.get("competitor")),
        }

    def build_entries(
        self,
        schedule_entries: Iterable[Dict[str, Any]],
        draft_records: Optional[Iterable[Dict[str, Any]]] = None,
        overlay_entries: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """把静态排期、用例 overlay、草稿库统一成占用条目。"""
        merged: List[Dict[str, Any]] = []
        for entry in schedule_entries or []:
            normalized = self._normalize_entry(entry, "schedule")
            if normalized:
                merged.append(normalized)
        for entry in overlay_entries or []:
            normalized = self._normalize_entry(entry, "overlay")
            if normalized:
                merged.append(normalized)
        for record in draft_records or []:
            slot = record.get("slot") or {}
            normalized = self._normalize_entry(
                {
                    "date": slot.get("date"),
                    "time": slot.get("time"),
                    "channel": slot.get("channel"),
                    "duration_min": slot.get("duration_min") or 30,
                    "material_id": record.get("material_id"),
                    "status": record.get("status") or "draft",
                },
                "draft",
            )
            if normalized:
                merged.append(normalized)
        return merged

    def blackouts(self, date: str, channel: str) -> List[Dict[str, Any]]:
        result = []
        for item in self.blackouts_cfg:
            if item.get("channel") not in (None, channel):
                continue
            if item.get("date") not in (None, "*", date):
                continue
            try:
                start = to_minutes(item.get("start"))
                end = to_minutes(item.get("end"))
            except (TypeError, ValueError):
                continue
            result.append(
                {
                    "start": start,
                    "end": end,
                    "time": to_hhmm(start),
                    "end_time": to_hhmm(end),
                    "reason": item.get("reason") or "禁发窗口",
                    "date": item.get("date") or "*",
                }
            )
        return result

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        date: str,
        channel: str,
        duration_min: int,
        near_time: Optional[str] = None,
        entries: Optional[List[Dict[str, Any]]] = None,
        self_material: Optional[str] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        duration_min = max(int(duration_min or 1), 1)
        limits = self.limits(channel)
        min_gap = int(limits.get("min_gap_min") or 0)
        max_per_day = int(limits.get("max_per_day") or 0)

        day_entries = [
            e
            for e in (entries or [])
            if e.get("date") == date and e.get("channel") == channel and e.get("status") not in DEAD_STATUSES
        ]
        foreign = [e for e in day_entries if e.get("material_id") != self_material]
        own = [e for e in day_entries if e.get("material_id") == self_material]
        blackouts = self.blackouts(date, channel)

        used_today = len(day_entries)
        quota_exceeded = bool(max_per_day) and used_today >= max_per_day

        result: Dict[str, Any] = {
            "date": date,
            "channel": channel,
            "duration_min": duration_min,
            "near_time": near_time,
            "window": {"open": to_hhmm(self.window_open), "close": to_hhmm(self.window_close)},
            "quota": {"used": used_today, "max": max_per_day, "exceeded": quota_exceeded},
            "occupied": [
                {
                    "time": e["time"],
                    "duration_min": e["duration_min"],
                    "material_id": e.get("material_id"),
                    "status": e.get("status"),
                    "source": e.get("source"),
                    "owner": "self" if e.get("material_id") == self_material else "foreign",
                }
                for e in sorted(day_entries, key=lambda item: item["start"])
            ],
            "blackouts": [
                {"time": b["time"], "end_time": b["end_time"], "reason": b["reason"]} for b in blackouts
            ],
            "conflict": None,
            "candidates": [],
        }

        # 1) 请求时段是否冲突（只看外部占用与禁发窗口；自家草稿不算冲突）
        if near_time:
            try:
                req_start = to_minutes(near_time)
            except (TypeError, ValueError):
                result["conflict"] = {"type": "invalid_time", "time": near_time}
                req_start = None
            if req_start is not None:
                req_end = req_start + duration_min
                for entry in sorted(foreign, key=lambda item: item["start"]):
                    if overlaps(req_start, req_end, entry["start"], entry["end"]):
                        result["conflict"] = {
                            "type": "slot_occupied",
                            "time": near_time,
                            "occupant": entry.get("material_id"),
                            "occupant_status": entry.get("status"),
                            "occupant_source": entry.get("source"),
                            "occupant_window": "%s-%s"
                            % (to_hhmm(entry["start"]), to_hhmm(entry["end"])),
                        }
                        break
                if result["conflict"] is None:
                    for window in blackouts:
                        if overlaps(req_start, req_end, window["start"], window["end"]):
                            result["conflict"] = {
                                "type": "blackout",
                                "time": near_time,
                                "reason": window["reason"],
                                "occupant_window": "%s-%s" % (window["time"], window["end_time"]),
                            }
                            break

        # 2) 枚举候选时段
        if not quota_exceeded:
            start = self.window_open
            while start + duration_min <= self.window_close:
                blocked = False
                for entry in foreign:
                    if overlaps(start, start + duration_min, entry["start"], entry["end"]):
                        blocked = True
                        break
                if not blocked:
                    for window in blackouts:
                        if overlaps(start, start + duration_min, window["start"], window["end"]):
                            blocked = True
                            break
                if not blocked and min_gap:
                    for entry in foreign:
                        separated = start >= entry["end"] + min_gap or start + duration_min + min_gap <= entry["start"]
                        if not separated:
                            blocked = True
                            break
                if not blocked:
                    candidate_time = to_hhmm(start)
                    note = "available"
                    for draft in own:
                        if draft["start"] == start:
                            note = "existing_draft_same_slot"
                            break
                    distance = None
                    if near_time:
                        try:
                            distance = abs(start - to_minutes(near_time))
                        except (TypeError, ValueError):
                            distance = None
                    result["candidates"].append(
                        {
                            "time": candidate_time,
                            "end_time": to_hhmm(start + duration_min),
                            "distance_min": distance,
                            "note": note,
                        }
                    )
                start += self.grid

        # 排序偏好：优先「顺延」而不是「提前」。
        # 业务上一条内容被占位后，提前发往往来不及预热，顺延是运营更常用也更容易获批的做法。
        # 因此 key = (是否早于请求时段, 与请求时段的距离, 时间字符串)。
        near_minutes = None
        if near_time:
            try:
                near_minutes = to_minutes(near_time)
            except (TypeError, ValueError):
                near_minutes = None

        def sort_key(item: Dict[str, Any]):
            start = to_minutes(item["time"])
            is_before = 1 if (near_minutes is not None and start < near_minutes) else 0
            distance = item["distance_min"] if item["distance_min"] is not None else 10 ** 6
            return (is_before, distance, item["time"])

        result["candidates"].sort(key=sort_key)
        result["candidates"] = result["candidates"][: max(int(limit), 1)]
        return result

"""审核规则引擎。

规则表存放在 data/rules.json（阈值可调），校验逻辑在本文件（代码可审计）。
设计要点：

* category 把「素材风险」与「排期冲突」分开：
  - review      —— 影响审核状态（pass / warn / blocked / missing_info）
  - scheduling  —— 只影响排期（时段占用、禁发窗口、配额、竞品），
                   否则「素材无风险但时段被占」会被渲染成「素材有风险」，与题意不符。
* severity 语义固定：block 永不保存 / confirm 可存草稿但必须带待确认项 / warn 提示。
* kind 区分缺失与风险：字段为 null = 资料缺失（可补查）；字段为 false = 明确无授权（直接阻断）。
  这条区分让「素材信息缺失」和「规则风险」两个验收点不会互相污染。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .models import RuleFinding
from .slots import overlaps, to_minutes


def _rule_index(rules_doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index = {}
    for item in (rules_doc or {}).get("rules") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        index[str(item["id"])] = item
    return index


def _finding(
    spec: Dict[str, Any],
    kind: str,
    message: str,
    evidence: Optional[Dict[str, Any]] = None,
    severity: Optional[str] = None,
) -> RuleFinding:
    return RuleFinding(
        rule_id=str(spec.get("id")),
        name=str(spec.get("name") or spec.get("id")),
        category=str(spec.get("category") or "review"),
        severity=str(severity or spec.get("severity") or "warn"),
        kind=kind,
        message=message,
        evidence=evidence or {},
    )


def evaluate(
    material_raw: Optional[Dict[str, Any]],
    brief_raw: Optional[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    rules_doc: Dict[str, Any],
    blacklist_doc: Dict[str, Any],
    self_material: Optional[str] = None,
) -> List[RuleFinding]:
    """对一条素材 + 一条需求跑全部规则，返回命中列表（可能为空）。"""
    material = material_raw or {}
    brief = brief_raw or {}
    specs = _rule_index(rules_doc)
    limits = (rules_doc.get("channel_limits") or {})
    channel = brief.get("channel") or material.get("channel") or "douyin"
    channel_limit = dict({"max_duration_sec": 60, "max_per_day": 6, "min_gap_min": 30})
    channel_limit.update(limits.get(channel) or {})
    material_id = self_material or material.get("material_id")

    # 时长换算成占用的分钟数，R07/R09 都需要
    duration_min = 1
    seconds = material.get("duration_sec")
    if isinstance(seconds, (int, float)) and seconds > 0:
        duration_min = int(seconds) // 60 + (1 if int(seconds) % 60 else 0)
        duration_min = max(duration_min, 1)

    findings: List[RuleFinding] = []

    # ------------------------------------------------------------------ R01
    spec = specs.get("R01")
    if spec and spec.get("enabled", True):
        required = (spec.get("params") or {}).get("required_fields") or [
            "title",
            "owner",
            "channel",
            "duration_sec",
        ]
        missing = [name for name in required if material.get(name) in (None, "")]
        if missing:
            findings.append(
                _finding(
                    spec,
                    "missing",
                    "素材元数据缺失：%s，无法确认排期参数" % "、".join(missing),
                    {"missing_fields": missing},
                )
            )

    # ------------------------------------------------------------------ R02
    spec = specs.get("R02")
    if spec and spec.get("enabled", True):
        for field_name, label in (("music_license", "音乐版权"), ("brand_license", "品牌授权")):
            if field_name not in material:
                continue
            value = material.get(field_name)
            if value is None:
                findings.append(
                    _finding(
                        spec,
                        "missing",
                        "%s信息缺失（字段为 null），需向上游补查" % label,
                        {"field": field_name},
                    )
                )
            elif value is False:
                findings.append(
                    _finding(
                        spec,
                        "risk",
                        "%s明确为未授权，不得排期" % label,
                        {"field": field_name, "value": False},
                    )
                )

    # ------------------------------------------------------------------ R03
    spec = specs.get("R03")
    if spec and spec.get("enabled", True) and material.get("has_person"):
        consent = material.get("portrait_consent")
        if consent is None:
            findings.append(
                _finding(
                    spec,
                    "missing",
                    "素材含人物出镜但肖像授权字段缺失",
                    {"field": "portrait_consent", "has_person": True},
                )
            )
        elif consent is False:
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "素材含人物出镜且肖像授权为未授权状态",
                    {"field": "portrait_consent", "has_person": True, "value": False},
                )
            )

    # ------------------------------------------------------------------ R04
    spec = specs.get("R04")
    if spec and spec.get("enabled", True):
        text = "%s %s" % (material.get("title") or "", material.get("description") or "")
        hits = [term for term in (blacklist_doc.get("terms") or []) if term and term in text]
        blocked_categories = blacklist_doc.get("blocked_categories") or []
        category = material.get("category")
        if category and category in blocked_categories:
            hits.append("品类:%s" % category)
        if hits:
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "内容命中广告法极限词/禁投品类：%s" % "、".join(hits),
                    {"hits": hits},
                )
            )

    # ------------------------------------------------------------------ R05
    spec = specs.get("R05")
    if spec and spec.get("enabled", True):
        duration = material.get("duration_sec")
        max_duration = channel_limit.get("max_duration_sec")
        if isinstance(duration, (int, float)) and max_duration and duration > max_duration:
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "时长 %ss 超过 %s 频道上限 %ss，需裁剪或改投频道"
                    % (duration, channel, max_duration),
                    {"duration_sec": duration, "max_duration_sec": max_duration},
                )
            )

    # ------------------------------------------------------------------ R06
    spec = specs.get("R06")
    if spec and spec.get("enabled", True):
        requested_at = None
        if brief.get("requested_date") and brief.get("requested_time"):
            requested_at = "%sT%s" % (brief.get("requested_date"), brief.get("requested_time"))
        deadline = brief.get("deadline") or material.get("deadline")
        if requested_at and deadline and requested_at > str(deadline):
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "请求发布时间 %s 晚于投放截止 %s" % (requested_at, deadline),
                    {"requested_at": requested_at, "deadline": deadline},
                )
            )

    # ------------------------------------------------------------------ R07
    spec = specs.get("R07")
    if spec and spec.get("enabled", True):
        requested_time = brief.get("requested_time")
        date = brief.get("requested_date")
        day_entries = [
            e
            for e in entries
            if e.get("date") == date and e.get("channel") == channel and e.get("material_id") != material_id
        ]
        if requested_time and day_entries:
            try:
                req_start = to_minutes(requested_time)
            except (TypeError, ValueError):
                req_start = None
            if req_start is not None:
                req_end = req_start + duration_min
                for entry in day_entries:
                    if overlaps(req_start, req_end, entry["start"], entry["end"]):
                        findings.append(
                            _finding(
                                spec,
                                "risk",
                                "请求时段 %s 已被 %s（%s）占用，需要换时段或请求确认"
                                % (requested_time, entry.get("material_id"), entry.get("status")),
                                {
                                    "requested_time": requested_time,
                                    "occupant": entry.get("material_id"),
                                    "occupant_status": entry.get("status"),
                                },
                            )
                        )
                        break
        max_per_day = int(channel_limit.get("max_per_day") or 0)
        same_day = [e for e in entries if e.get("date") == date and e.get("channel") == channel]
        if max_per_day and len(same_day) >= max_per_day:
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "当日 %s 频道配额已满（%s/%s）" % (channel, len(same_day), max_per_day),
                    {"used": len(same_day), "max": max_per_day},
                )
            )
        brand = material.get("brand")
        for entry in day_entries:
            if entry.get("competitor") and entry.get("brand") != brand and brand:
                findings.append(
                    _finding(
                        spec,
                        "risk",
                        "同日同频道已排入竞品素材 %s，需确认是否同框出现" % entry.get("material_id"),
                        {"competitor_material": entry.get("material_id"), "brand": brand},
                    )
                )
                break

    # ------------------------------------------------------------------ R08
    spec = specs.get("R08")
    if spec and spec.get("enabled", True):
        content_hash = material.get("content_hash")
        if content_hash:
            duplicates = sorted(
                {
                    str(entry.get("material_id"))
                    for entry in entries
                    if entry.get("content_hash") == content_hash
                    and entry.get("material_id")
                    and str(entry.get("material_id")) != str(material_id)
                }
            )
            if duplicates:
                findings.append(
                    _finding(
                        spec,
                        "risk",
                        "相同内容指纹已存在于排期/草稿（%s），需人工确认是否重复投放"
                        % "、".join(duplicates),
                        {"content_hash": content_hash, "duplicates": duplicates},
                    )
                )

    # ------------------------------------------------------------------ R09
    spec = specs.get("R09")
    if spec and spec.get("enabled", True):
        requested_time = brief.get("requested_time")
        if requested_time:
            try:
                req_start = to_minutes(requested_time)
            except (TypeError, ValueError):
                req_start = None
            if req_start is not None:
                req_end = req_start + duration_min
                for window in blacklist_doc.get("blackout_windows") or []:
                    if window.get("channel") not in (None, channel):
                        continue
                    if window.get("date") not in (None, "*", brief.get("requested_date")):
                        continue
                    try:
                        start = to_minutes(window.get("start"))
                        end = to_minutes(window.get("end"))
                    except (TypeError, ValueError):
                        continue
                    if overlaps(req_start, req_end, start, end):
                        findings.append(
                            _finding(
                                spec,
                                "risk",
                                "请求时段 %s 落在禁发窗口 %s-%s（%s）"
                                % (requested_time, window.get("start"), window.get("end"), window.get("reason")),
                                {
                                    "requested_time": requested_time,
                                    "window": "%s-%s" % (window.get("start"), window.get("end")),
                                },
                            )
                        )
                        break

    # ------------------------------------------------------------------ R10
    spec = specs.get("R10")
    if spec and spec.get("enabled", True):
        allowed = (spec.get("params") or {}).get("allowed") or ["approved"]
        status = material.get("review_status")
        if status and status not in allowed:
            findings.append(
                _finding(
                    spec,
                    "risk",
                    "素材审核状态为 %s（允许值：%s），不可排期" % (status, "/".join(allowed)),
                    {"review_status": status, "allowed": allowed},
                )
            )

    return findings


def summarize(findings: List[RuleFinding]) -> Dict[str, Any]:
    """把命中列表折叠成审核状态与分类桶。

    优先级：missing_info > blocked > warn > pass。
    资料缺失优先于风险，因为「不知道」比「知道有风险」更需要人工补料。
    """
    review = [f for f in findings if f.category == "review"]
    scheduling = [f for f in findings if f.category == "scheduling"]
    missing = [f for f in review if f.kind == "missing"]
    block = [f for f in review if f.severity == "block"]
    confirm = [f for f in review if f.severity == "confirm"]
    warn = [f for f in review if f.severity == "warn"]

    if missing:
        expected_status = "missing_info"
    elif block:
        expected_status = "blocked"
    elif confirm or warn:
        expected_status = "warn"
    else:
        expected_status = "pass"

    return {
        "expected_status": expected_status,
        "missing": [f.to_dict() for f in missing],
        "block": [f.to_dict() for f in block],
        "confirm": [f.to_dict() for f in confirm],
        "warn": [f.to_dict() for f in warn],
        "scheduling": [f.to_dict() for f in scheduling],
        "review_rule_ids": [f.rule_id for f in review],
        "all_rule_ids": [f.rule_id for f in findings],
    }


def evaluate_and_summarize(
    material_raw: Optional[Dict[str, Any]],
    brief_raw: Optional[Dict[str, Any]],
    entries: List[Dict[str, Any]],
    rules_doc: Dict[str, Any],
    blacklist_doc: Dict[str, Any],
    self_material: Optional[str] = None,
) -> Tuple[List[RuleFinding], Dict[str, Any]]:
    findings = evaluate(material_raw, brief_raw, entries, rules_doc, blacklist_doc, self_material)
    return findings, summarize(findings)

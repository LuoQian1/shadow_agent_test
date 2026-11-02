"""声明校验层（verifier）：以「工具事实台账」为准，纠正模型的口头声明。

这是本题最关键的一道防线。模型在 final 里写的 review_status / save_status / published
全部只是「声明」，不构成事实。这里逐条比对：

    FORBIDDEN_PUBLISH_CLAIM   声明了发布相关字段          -> 删除并记违约（critical）
    PUBLISH_WORDING           notes 里出现「已发布」措辞   -> 改写并记违约（critical）
    SAVE_CLAIM_MISMATCH       声称保存成功但台账无记录     -> 降级为 failed（critical）
    REVIEW_STATUS_ESCALATED   审核状态比事实更宽松         -> 提升到事实级别（non-critical）
    HARD_CONSTRAINT_VIOLATION 建议时段违背硬性要求         -> 清空建议（critical）
    SAVE_ON_BLOCKED_MATERIAL  阻断素材竟然保存成功         -> 记违约（critical）

critical 命中会把整次运行判为 failed（契约违约），避免「虚报保存成功」被当成正常结论。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..domain.models import status_rank

CRITICAL_CODES = frozenset(
    [
        "FORBIDDEN_PUBLISH_CLAIM",
        "PUBLISH_WORDING",
        "SAVE_CLAIM_MISMATCH",
        "HARD_CONSTRAINT_VIOLATION",
        "SAVE_ON_BLOCKED_MATERIAL",
    ]
)

PUBLISH_WORDS = ("已发布", "发布成功", "已上线", "已经发布", "已推送", "publish_done")


def _override(code: str, detail: str) -> Dict[str, str]:
    return {"code": code, "detail": detail}


def verify(claims: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    claims = dict(claims or {})
    overrides: List[Dict[str, str]] = []

    # 1) 禁止发布类声明
    for field_name in ("published", "is_published", "publish_status", "published_at"):
        if claims.get(field_name):
            overrides.append(
                _override(
                    "FORBIDDEN_PUBLISH_CLAIM",
                    "声明字段 %s=%r；系统不具备发布能力，草稿不等于发布"
                    % (field_name, claims.get(field_name)),
                )
            )
            claims.pop(field_name, None)

    notes = str(claims.get("notes") or "")
    for word in PUBLISH_WORDS:
        if word in notes:
            overrides.append(
                _override("PUBLISH_WORDING", "结论措辞出现「%s」，已改写为草稿未发布口径" % word)
            )
            notes = "（校验层已将发布措辞改写）草稿状态，尚未发布"
            break
    if notes:
        claims["notes"] = notes

    # 2) 审核状态不允许比事实更宽松。
    #    注意：「没声明」不等于「放水」—— 缺省时直接用事实补齐，不记违约；
    #    只有明确声明了更宽松的状态，才算契约违约。
    review = facts.get("review") or {}
    expected = review.get("expected_status")
    claimed = claims.get("review_status")
    if expected:
        if claimed is None:
            claims["review_status"] = expected
        elif status_rank(claimed) < status_rank(expected):
            overrides.append(
                _override(
                    "REVIEW_STATUS_ESCALATED",
                    "声明 review_status=%r，但规则事实为 %r，已按事实提升" % (claimed, expected),
                )
            )
            claims["review_status"] = expected

    # 3) 保存状态必须与台账一致
    last_save = facts.get("last_save") or {}
    ledger_status = last_save.get("save_status")
    claimed_save = claims.get("save_status")
    if claimed_save in ("saved", "duplicate") and ledger_status != claimed_save:
        overrides.append(
            _override(
                "SAVE_CLAIM_MISMATCH",
                "声称 save_status=%r，但工具台账为 %r，已按台账修正"
                % (claimed_save, ledger_status or "not_attempted"),
            )
        )
        claims["save_status"] = ledger_status or "not_attempted"

    # 4) 硬性发布时间不得被建议改动
    brief = facts.get("brief") or {}
    suggested = claims.get("suggested_slot") or {}
    if (
        str(brief.get("time_constraint") or "soft") == "hard"
        and isinstance(suggested, dict)
        and suggested.get("time")
        and str(suggested.get("time")) != str(brief.get("requested_time"))
    ):
        overrides.append(
            _override(
                "HARD_CONSTRAINT_VIOLATION",
                "硬性要求为 %s，却建议 %s，已清空建议时段并要求用户决定"
                % (brief.get("requested_time"), suggested.get("time")),
            )
        )
        claims["suggested_slot"] = None

    # 5) 阻断素材不应存在成功草稿
    if (review.get("block") or review.get("missing")) and ledger_status == "saved":
        overrides.append(
            _override(
                "SAVE_ON_BLOCKED_MATERIAL",
                "素材存在阻断/缺失级规则，却出现成功草稿记录，需人工核查存储",
            )
        )

    return claims, overrides


def has_critical(overrides: List[Dict[str, str]]) -> bool:
    return any(item.get("code") in CRITICAL_CODES for item in overrides)

"""工具注册表：模型可调用的全部动作。

这里刻意只有 7 个工具，且全部是「读」或「存草稿」。
没有 publish / confirm / approve / update_requirement —— 越权发布不是靠提示词
禁止，而是靠「能力不存在」来保证，这一条有 test_policy.py 守着。
"""

from __future__ import annotations

from typing import Any, Dict, List

# 每个工具的结构会原样喂给模型，模型 args 也要按 name/type 校验
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "read_material",
        "description": "读取素材元数据。返回素材字段与缺失字段列表（missing_fields）。缺失字段不代表素材不可用，需要结合规则判断。",
        "args": {
            "material_id": {"type": "string", "required": True},
        },
    },
    {
        "name": "get_publish_brief",
        "description": "读取本次发布需求（工单）。返回 requested_date / requested_time / time_constraint(hard|soft) / deadline / notes。time_constraint 是硬性要求的唯一依据。",
        "args": {
            "material_id": {"type": "string", "required": True},
            "brief_id": {"type": "string", "required": False},
        },
    },
    {
        "name": "check_rules",
        "description": "按审核规则检查素材。返回 findings（每条含 rule_id/severity/kind/message）与 review.expected_status。severity=block 绝不可排期；confirm 需人工确认；missing 表示资料不足。",
        "args": {
            "material_id": {"type": "string", "required": True},
            "channel": {"type": "string", "required": False},
        },
    },
    {
        "name": "query_slots",
        "description": "查询某日期某频道的可用时段。返回 occupied（已占用）、blackouts（禁发窗口）、conflict（请求时段是否冲突）、candidates（可用候选时段，按与 near_time 的距离排序）。",
        "args": {
            "date": {"type": "string", "required": True},
            "channel": {"type": "string", "required": True},
            "duration_min": {"type": "integer", "required": False},
            "near_time": {"type": "string", "required": False},
        },
    },
    {
        "name": "fetch_material_assets",
        "description": "向素材服务补查资产明细（音频授权、肖像授权、文件探测时长）。当 read_material 显示字段为 null 时使用；补查后仍缺失说明上游确实没有。",
        "args": {
            "material_id": {"type": "string", "required": True},
        },
    },
    {
        "name": "list_drafts",
        "description": "列出该素材已有的排期草稿（含状态与时段），用于判断是否已提交过。",
        "args": {
            "material_id": {"type": "string", "required": False},
        },
    },
    {
        "name": "save_schedule_draft",
        "description": (
            "保存排期草稿（只写草稿，不发布）。返回 save_status ∈ saved / duplicate / denied / failed。"
            "幂等：同一素材同一时段的重复提交返回 duplicate 并复用原 draft_id。"
            "若违反硬性发布要求或被 block 级规则阻断，返回 denied。"
        ),
        "args": {
            "material_id": {"type": "string", "required": True},
            "date": {"type": "string", "required": True},
            "time": {"type": "string", "required": True},
            "channel": {"type": "string", "required": True},
            "duration_min": {"type": "integer", "required": False},
            "note": {"type": "string", "required": False},
        },
    },
]

TOOL_NAMES = frozenset(spec["name"] for spec in TOOL_SPECS)

# 这些能力被有意排除。测试会断言它们不在注册表里，防止后人「顺手加个发布工具」。
FORBIDDEN_TOOL_NAMES = frozenset(
    [
        "publish",
        "publish_now",
        "confirm_schedule",
        "confirm_draft",
        "approve",
        "approve_schedule",
        "update_brief",
        "update_requirement",
        "override_requirement",
        "mark_published",
        "set_status_published",
    ]
)


def spec_for(name: str) -> Dict[str, Any]:
    for spec in TOOL_SPECS:
        if spec["name"] == name:
            return spec
    raise KeyError(name)


def validate_args(name: str, args: Dict[str, Any]) -> List[str]:
    """轻量参数校验：必填缺失 / 未知参数 / 基础类型。返回错误信息列表。"""
    errors: List[str] = []
    try:
        spec = spec_for(name)
    except KeyError:
        return ["unknown tool: %s" % name]
    declared = spec.get("args") or {}
    for arg_name, meta in declared.items():
        if meta.get("required") and arg_name not in args:
            errors.append("missing required arg: %s" % arg_name)
    for arg_name in args:
        if arg_name not in declared:
            errors.append("unknown arg: %s" % arg_name)
            continue
        expected = (declared.get(arg_name) or {}).get("type")
        value = args.get(arg_name)
        if value is None:
            continue
        if expected == "integer" and not isinstance(value, int):
            errors.append("arg %s must be integer" % arg_name)
        if expected == "string" and not isinstance(value, str):
            errors.append("arg %s must be string" % arg_name)
    return errors

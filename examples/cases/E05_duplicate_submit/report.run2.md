# 运行记录：E05_duplicate_submit

> 重复提交：同一请求连续提交两次，只允许一份草稿

**运行状态**：`completed` ｜ **模型**：`offline-policy` ｜ **步数/工具调用**：7/6

## 结论

| 项 | 值 |
| --- | --- |
| 素材 | M01 夏日新品开箱 15s 竖版 |
| 审核状态 | `pass` |
| 处置结论 | `draft_exists` |
| 用户要求 | 2025-06-03 18:00（soft） |
| 建议时段 | 19:00 |
| 草稿保存状态 | `duplicate`（draft_id=D-e45d137cbdaa） |
| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |

## 风险与冲突

| 规则 | 分类 | 级别 | 说明 |
| --- | --- | --- | --- |
| R07 | scheduling | warn | 请求时段 18:00 已被 M09（draft）占用，需要换时段或请求确认 |

## 待确认项

- `C-SLOT`（owner_approval）：建议时段 19:00 与用户要求 18:00 不一致

## 决策与执行链（模型动作 → 工具结果）

1. `read_material` {'material_id': 'M01'}
   - 决策依据：先读取素材元数据，确认字段是否齐全
   - 观察结果：素材 M01「夏日新品开箱 15s 竖版」读取成功；缺失元数据：无
2. `get_publish_brief` {'material_id': 'M01', 'brief_id': 'B-M01-v3'}
   - 决策依据：读取工单，确认时间是硬性还是软性要求
   - 观察结果：需求 B-M01-v3：2025-06-03 18:00 发布，约束=soft，截止=2025-06-30T23:59
3. `check_rules` {'material_id': 'M01', 'channel': 'douyin'}
   - 决策依据：按规则表审核素材，先判断是否存在阻断级风险
   - 观察结果：规则校验完成：expected_status=pass，命中 R07
4. `list_drafts` {'material_id': 'M01'}
   - 决策依据：先确认是否已有草稿，避免重复提交
   - 观察结果：素材 M01 已有草稿 2 份（活跃 2 份）
5. `query_slots` {'date': '2025-06-03', 'channel': 'douyin', 'near_time': '18:00'}
   - 决策依据：查询请求时段是否可用以及有哪些替代时段
   - 观察结果：2025-06-03 douyin 时段查询：冲突=slot_occupied，候选=19:00、19:30、20:00、20:30、21:00
6. `save_schedule_draft` {'material_id': 'M01', 'date': '2025-06-03', 'time': '19:00', 'channel': 'douyin'}
   - 决策依据：请求时段被占用，改到最近的可用时段 19:00，并标记待确认
   - 观察结果：已存在同一素材同一时段的草稿，返回原 draft_id=D-e45d137cbdaa（未新建）
7. final: review_status=pass
   - 决策依据：保存已成功，收尾

## 限制与说明

- 本次运行使用离线策略模型（deterministic policy），只验证流程与护栏，不代表真实模型的决策效果。

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

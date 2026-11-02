# 运行记录：E02_slot_conflict

> 时段冲突：请求 18:00 已被占用，19:00 可用（题面参考用例）

**运行状态**：`completed` ｜ **模型**：`openai-compat` ｜ **步数/工具调用**：6/5

## 结论

| 项 | 值 |
| --- | --- |
| 素材 | M01 夏日新品开箱 15s 竖版 |
| 审核状态 | `pass` |
| 处置结论 | `draft_saved_pending_confirmation` |
| 用户要求 | 2025-06-01 18:00（soft） |
| 建议时段 | 19:00 |
| 草稿保存状态 | `saved`（draft_id=D-ead85d3599f6） |
| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |

## 风险与冲突

| 规则 | 分类 | 级别 | 说明 |
| --- | --- | --- | --- |
| R07 | scheduling | warn | 请求时段 18:00 已被 M09（draft）占用，需要换时段或请求确认 |

## 待确认项

- `C-SLOT`（owner_approval）：建议时段 19:00 与用户要求 18:00 不一致

## 决策与执行链（模型动作 → 工具结果）

1. `read_material` {'material_id': 'M01'}
   - 决策依据：先并行读取素材元数据与工单需求，确认缺失字段与硬性时间要求。
   - 观察结果：素材 M01「夏日新品开箱 15s 竖版」读取成功；缺失元数据：无
2. `get_publish_brief` {'material_id': 'M01', 'brief_id': 'B-M01-v1'}
   - 决策依据：读取工单，确认请求日期、时段与 time_constraint 硬性要求。
   - 观察结果：需求 B-M01-v1：2025-06-01 18:00 发布，约束=soft，截止=2025-06-30T23:59
3. `check_rules` {'material_id': 'M01', 'channel': 'douyin'}
   - 决策依据：按规则审核素材，获取 findings 与 expected_status，判定是否可排期。
   - 观察结果：规则校验完成：expected_status=pass，命中 R07
4. `query_slots` {'date': '2025-06-01', 'channel': 'douyin', 'duration_min': 1, 'near_time': '18:00'}
   - 决策依据：18:00 被 M09 草稿占用，工单为 soft 约束可挪时段，查询当日候选时段。
   - 观察结果：2025-06-01 douyin 时段查询：冲突=slot_occupied，候选=19:00、19:30、20:00、20:30、21:00
5. `save_schedule_draft` {'material_id': 'M01', 'date': '2025-06-01', 'time': '19:00', 'channel': 'douyin', 'duration_min': 1, 'note': '工单 B-M01-v1 为 soft 约束；18:00 被 M09 草稿占用（R07），改用最近候选 19:00，待运营确认时段变更。'}
   - 决策依据：18:00 冲突且工单为 soft 可挪，先保存最近候选 19:00 草稿并标注待确认。
   - 观察结果：草稿已保存：2025-06-01 19:00（draft_id=D-ead85d3599f6，仅草稿未发布）
6. final: review_status=pass
   - 决策依据：审核通过，仅 R07 排期冲突（warn）；18:00 被占，soft 约束下改用 19:00 草稿，时段变更待确认。

## 限制与说明

- 无额外限制。

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

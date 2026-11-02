# 运行记录：E06_save_failure

> 保存失败：草稿写入连续失败，必须明确返回失败

**运行状态**：`failed` ｜ **模型**：`offline-policy` ｜ **步数/工具调用**：8/7

**未完成原因**：草稿保存失败：EACCES: permission denied, open 'data/store/drafts/D-xxxx.json'

## 结论

| 项 | 值 |
| --- | --- |
| 素材 | M06 品牌故事短片 |
| 审核状态 | `pass` |
| 处置结论 | `save_failed` |
| 用户要求 | 2025-06-06 11:00（soft） |
| 建议时段 | 11:00 |
| 草稿保存状态 | `failed` |
| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |

## 风险与冲突

无规则命中。

## 待确认项

无。

## 决策与执行链（模型动作 → 工具结果）

1. `read_material` {'material_id': 'M06'}
   - 决策依据：先读取素材元数据，确认字段是否齐全
   - 观察结果：素材 M06「品牌故事短片」读取成功；缺失元数据：无
2. `get_publish_brief` {'material_id': 'M06', 'brief_id': 'B-M06-v1'}
   - 决策依据：读取工单，确认时间是硬性还是软性要求
   - 观察结果：需求 B-M06-v1：2025-06-06 11:00 发布，约束=soft，截止=2025-06-30T23:59
3. `check_rules` {'material_id': 'M06', 'channel': 'douyin'}
   - 决策依据：按规则表审核素材，先判断是否存在阻断级风险
   - 观察结果：规则校验完成：expected_status=pass，命中 无
4. `list_drafts` {'material_id': 'M06'}
   - 决策依据：先确认是否已有草稿，避免重复提交
   - 观察结果：素材 M06 已有草稿 0 份（活跃 0 份）
5. `query_slots` {'date': '2025-06-06', 'channel': 'douyin', 'near_time': '11:00'}
   - 决策依据：查询请求时段是否可用以及有哪些替代时段
   - 观察结果：2025-06-06 douyin 时段查询：冲突=无，候选=11:00、11:30、13:00、13:30、14:00
6. `save_schedule_draft` {'material_id': 'M06', 'date': '2025-06-06', 'time': '11:00', 'channel': 'douyin'}
   - 决策依据：请求时段可用，直接在请求时段保存草稿
   - 观察结果：草稿保存失败（第 1 次）：EACCES: permission denied, open 'data/store/drafts/D-xxxx.json'
7. `save_schedule_draft` {'material_id': 'M06', 'date': '2025-06-06', 'time': '11:00', 'channel': 'douyin'}
   - 决策依据：保存失败，先用相同参数重试一次
   - 观察结果：草稿保存失败（第 2 次）：EACCES: permission denied, open 'data/store/drafts/D-xxxx.json'
8. final: review_status=pass
   - 决策依据：保存失败/被拒绝且重试无效，如实报告失败

## 限制与说明

- 本次运行使用离线策略模型（deterministic policy），只验证流程与护栏，不代表真实模型的决策效果。
- 草稿保存失败：EACCES: permission denied, open 'data/store/drafts/D-xxxx.json'（尝试 2 次）

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

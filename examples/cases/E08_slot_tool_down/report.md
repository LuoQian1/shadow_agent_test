# 运行记录：E08_slot_tool_down

> 工具失败：时段查询连续超时，无法核实空闲时段

**运行状态**：`failed` ｜ **模型**：`offline-policy` ｜ **步数/工具调用**：7/6

**未完成原因**：时段查询失败 2 次，无法核实空闲时段，任务未完成

## 结论

| 项 | 值 |
| --- | --- |
| 素材 | M07 长版产品测评 |
| 审核状态 | `warn` |
| 处置结论 | `tool_failure` |
| 用户要求 | 2025-06-08 15:00（soft） |
| 建议时段 | 无 |
| 草稿保存状态 | `not_attempted` |
| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |

## 风险与冲突

| 规则 | 分类 | 级别 | 说明 |
| --- | --- | --- | --- |
| R05 | review | warn | 时长 120s 超过 douyin 频道上限 60s，需裁剪或改投频道 |

## 待确认项

无。

## 决策与执行链（模型动作 → 工具结果）

1. `read_material` {'material_id': 'M07'}
   - 决策依据：先读取素材元数据，确认字段是否齐全
   - 观察结果：素材 M07「长版产品测评」读取成功；缺失元数据：无
2. `get_publish_brief` {'material_id': 'M07', 'brief_id': 'B-M07-v1'}
   - 决策依据：读取工单，确认时间是硬性还是软性要求
   - 观察结果：需求 B-M07-v1：2025-06-08 15:00 发布，约束=soft，截止=2025-06-30T23:59
3. `check_rules` {'material_id': 'M07', 'channel': 'douyin'}
   - 决策依据：按规则表审核素材，先判断是否存在阻断级风险
   - 观察结果：规则校验完成：expected_status=warn，命中 R05
4. `list_drafts` {'material_id': 'M07'}
   - 决策依据：先确认是否已有草稿，避免重复提交
   - 观察结果：素材 M07 已有草稿 0 份（活跃 0 份）
5. `query_slots` {'date': '2025-06-08', 'channel': 'douyin', 'near_time': '15:00'}
   - 决策依据：查询请求时段是否可用以及有哪些替代时段
   - 观察结果：时段查询失败（第 1 次）：ETIMEDOUT: scheduling-service did not respond in 3000ms
6. `query_slots` {'date': '2025-06-08', 'channel': 'douyin', 'near_time': '15:00'}
   - 决策依据：查询请求时段是否可用以及有哪些替代时段
   - 观察结果：时段查询失败（第 2 次）：ETIMEDOUT: scheduling-service did not respond in 3000ms
7. final: review_status=warn
   - 决策依据：时段服务不可用且重试无效，不做无依据的保存

## 限制与说明

- 本次运行使用离线策略模型（deterministic policy），只验证流程与护栏，不代表真实模型的决策效果。
- 时段查询连续失败 2 次，未核实目标时段是否空闲，因此没有保存草稿。

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

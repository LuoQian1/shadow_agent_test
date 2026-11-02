# 运行记录：E03_missing_info

> 信息缺失：时长与音乐授权缺失，补查后仍不足

**运行状态**：`completed` ｜ **模型**：`offline-policy` ｜ **步数/工具调用**：5/4

## 结论

| 项 | 值 |
| --- | --- |
| 素材 | M03 周末探店预告 |
| 审核状态 | `missing_info` |
| 处置结论 | `needs_info` |
| 用户要求 | 2025-06-04 19:00（soft） |
| 建议时段 | 无 |
| 草稿保存状态 | `not_attempted` |
| 是否已发布 | **否**（系统无发布能力，仅保存草稿） |

## 风险与冲突

| 规则 | 分类 | 级别 | 说明 |
| --- | --- | --- | --- |
| R01 | review | block | 素材元数据缺失：duration_sec，无法确认排期参数 |
| R02 | review | block | 音乐版权信息缺失（字段为 null），需向上游补查 |

## 待确认项

- `C-INFO`（material_metadata）：素材信息缺失：素材元数据缺失：duration_sec，无法确认排期参数；音乐版权信息缺失（字段为 null），需向上游补查
- `C-R01`（manual_review）：素材元数据缺失：duration_sec，无法确认排期参数
- `C-R02`（manual_review）：音乐版权信息缺失（字段为 null），需向上游补查

## 决策与执行链（模型动作 → 工具结果）

1. `read_material` {'material_id': 'M03'}
   - 决策依据：先读取素材元数据，确认字段是否齐全
   - 观察结果：素材 M03「周末探店预告」读取成功；缺失元数据：duration_sec
2. `get_publish_brief` {'material_id': 'M03', 'brief_id': 'B-M03-v1'}
   - 决策依据：读取工单，确认时间是硬性还是软性要求
   - 观察结果：需求 B-M03-v1：2025-06-04 19:00 发布，约束=soft，截止=2025-06-30T23:59
3. `check_rules` {'material_id': 'M03', 'channel': 'douyin'}
   - 决策依据：按规则表审核素材，先判断是否存在阻断级风险
   - 观察结果：规则校验完成：expected_status=missing_info，命中 R01、R02
4. `fetch_material_assets` {'material_id': 'M03'}
   - 决策依据：资料缺失，先向上游补查再决定，不能假设授权已具备
   - 观察结果：补查完成，仍缺失：music_license、duration_sec
5. final: review_status=missing_info
   - 决策依据：补查无效，资料确实缺失，收尾并请用户补料

## 限制与说明

- 本次运行使用离线策略模型（deterministic policy），只验证流程与护栏，不代表真实模型的决策效果。
- 补查素材服务后仍缺失：music_license、duration_sec（上游确实没有该信息，未做任何假设）

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

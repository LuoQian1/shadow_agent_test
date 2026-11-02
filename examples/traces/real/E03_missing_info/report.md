# 运行记录：E03_missing_info

> 信息缺失：时长与音乐授权缺失，补查后仍不足

**运行状态**：`completed` ｜ **模型**：`openai-compat` ｜ **步数/工具调用**：5/4

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
   - 决策依据：先读取素材元数据与工单要求，确认缺失字段和硬性时间约束。
   - 观察结果：素材 M03「周末探店预告」读取成功；缺失元数据：duration_sec
2. `get_publish_brief` {'material_id': 'M03', 'brief_id': 'B-M03-v1'}
   - 决策依据：读取工单确认硬性发布时间与截止要求，同时后续需补查缺失的时长与音乐授权信息。
   - 观察结果：需求 B-M03-v1：2025-06-04 19:00 发布，约束=soft，截止=2025-06-30T23:59
3. `fetch_material_assets` {'material_id': 'M03'}
   - 决策依据：补查缺失的时长与音乐授权等资产明细，避免假设授权已具备。
   - 观察结果：补查完成，仍缺失：music_license、duration_sec
4. `check_rules` {'material_id': 'M03', 'channel': 'douyin'}
   - 决策依据：补查后音乐授权与时长仍缺失，需按规则检查确认审核状态与阻断项。
   - 观察结果：规则校验完成：expected_status=missing_info，命中 R01、R02
5. final: review_status=missing_info
   - 决策依据：补查后时长与音乐授权仍缺失，R01/R02 为 block 级 missing，无法保存草稿，状态为资料缺失。

## 限制与说明

- 补查素材服务后仍缺失：music_license、duration_sec（上游确实没有该信息，未做任何假设）

---
本文件由 `src/agent/renderer.py` 从工具台账自动生成；上面的排期一律为**草稿**，未执行任何发布动作。

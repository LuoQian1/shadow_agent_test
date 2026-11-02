# 评审导览：先看这 6 个文件

本目录是「实际运行产物」，不是手写示例：全部由
`python -m src.cli replay --model offline` 生成（真实模型的两条 run 由 `--model real` 生成）。
`tests/test_scenarios.py` 会逐条复核这些用例的预期，文档与产物不会脱节。

## 建议阅读顺序

| 顺序 | 文件 | 看点 |
| --- | --- | --- |
| 1 | `cases/E02_slot_conflict/report.md` | 题面参考用例：18:00 被占 → 建议 19:00 → 待确认 → 只存草稿 |
| 2 | `cases/E05_duplicate_submit/report.run2.md` | 重复提交命中幂等，返回 duplicate 且复用同一 draft_id |
| 3 | `cases/E06_save_failure/report.md` | 注入保存失败：重试一次后如实报失败，没有虚报成功 |
| 4 | `cases/E07_hard_constraint/report.md` | 硬性 18:00 被占：不改时间、不存草稿、升级待确认 |
| 5 | `cases/E03_missing_info/report.md` | 资料缺失：先补查、补查无效才收尾，不假设授权 |
| 6 | `cases/E10_claim_violation/actual_output.json` | 对抗模型谎报「已保存 + 已发布」被校验层打回 |

## 目录结构

```
examples/
├── cases/
│   └── E01_normal/ ... E11_step_limit/
│       ├── input.json              # 输入：素材 + 工单 + 注入故障 + 期望断言
│       ├── trace.jsonl             # 逐步运行记录（模型动作 / 工具结果 / 预算）
│       ├── actual_output.json      # 结构化交付物（多轮用例另有 .run1/.run2）
│       └── report.md               # 人读版运行记录（多轮用例另有 .run1/.run2）
├── traces/
│   └── real/                       # 真实模型（deepseek-flash）的两条 ReAct 记录
└── run_all.ps1 / run_all.sh        # 一键重放
```

## trace.jsonl 字段说明

| type | 含义 |
| --- | --- |
| `run_start` | 本次运行的模型、上限、任务 |
| `model_action` | 模型选择的动作 + `rationale`（对外决策依据，非内部思维链）+ 延迟与 token |
| `tool_result` | 工具真实返回（含 save_status）与消耗后的预算 |
| `tool_rejected` | 工具被拒绝（不存在 / 参数非法 / 重复调用） |
| `invalid_action` | 模型输出不是合法动作 JSON |
| `model_degraded` | 真实模型失败后降级到离线策略模型续跑 |
| `limit_reached` | 触达步数/调用上限 |
| `run_end` | 最终状态、校验层拦截记录、预算消耗 |

查看方式：

```bash
python -m src.cli inspect --trace examples/cases/E02_slot_conflict/trace.jsonl
```

# 01 需求：素材审核与发布排期 Agent

## 1. 题目与定位

校招 AI Agent 开发笔试 · 项目题「素材审核与发布排期 Agent」。

一句话目标：**帮内容团队把一条待处理素材，从「有素材、有工单」推到「有一份可确认、无冲突的排期草稿」，并且把不能自动决定的事情明确交回给人。**

## 2. 用户与使用场景

| 角色 | 关心什么 | 在本项目中的体现 |
| --- | --- | --- |
| 内容运营（主要用户） | 这条素材今天能不能发、发几点、要不要我确认 | CLI 输出的结论表：审核状态、建议时段、待确认项、草稿状态 |
| 审核 / 法务 | 有没有版权、肖像、合规风险，证据是什么 | `risk_reasons` 逐条给出 rule_id + 证据字段 |
| 发布执行 | 排期是否冲突、是否重复提交 | 时段冲突检测、草稿幂等与 supersede |
| 评审者（本笔试） | 是否真的会「看结果再调整」，护栏是否可靠 | `examples/` 下 11 组输入/输出/trace + `tests/` |

主要场景：运营把「素材 + 发布要求」作为工单提交，Agent 审核并尝试落一条草稿，把不确定项列成待确认，由人拍板。**Agent 不发布。**

## 3. 输入材料

| 材料 | 位置 | 说明 |
| --- | --- | --- |
| 素材元数据 | `data/materials.json` | 12 条合成素材：时长、频道、授权、肖像、内容指纹、审核状态、截止时间 |
| 发布要求（工单） | `data/briefs.json` | 日期、请求时段、`time_constraint`（hard/soft）、频道、截止、备注 |
| 已有排期 | `data/schedule.json` | 已占用时段（草稿/已确认），用于制造冲突 |
| 审核规则 | `data/rules.json` | 10 条规则：id / 分类 / 级别 / 阈值参数 |
| 内容黑名单与禁发窗口 | `data/blacklist.json` | 广告法极限词、禁投品类、午休禁发窗口 |
| 草稿库 | `data/store/`（运行时生成） | 幂等写入，不提交到仓库 |

所有数据均为合成数据，不含真实用户信息与密钥。

## 4. 交付结果（每条素材一个对象）

| 字段 | 含义 |
| --- | --- |
| `review_status` | `pass` / `warn` / `blocked` / `missing_info` |
| `risk_reasons` | 命中的规则、级别、分类、说明与证据 |
| `suggested_slot` | 建议时段（没有落成草稿时为空） |
| `pending_confirmations` | 待确认项：谁需要确认、为什么 |
| `draft` | `save_status`（saved/duplicate/failed/denied/not_attempted）、draft_id、尝试次数、错误、被 supersede 的旧草稿 |
| `published` | **恒为 false**，且系统中不存在发布能力 |

## 5. 范围（做）与非目标（不做）

做：

1. 单素材、单工单的端到端审核 + 排期草稿。
2. 模型驱动的 ReAct 循环（模型选动作 → 执行工具 → 观察 → 调整或结束）。
3. 时段冲突处理（占用/禁发/间隔/配额/竞品）。
4. 信息缺失的补查与升级。
5. 硬性发布时间不可被擅自改动。
6. 幂等保存草稿、防重复提交、supersede 旧方案。
7. 保存失败、工具失败、触达上限的明确失败语义。
8. 校验层拦截「谎报保存成功 / 谎报已发布」。

不做（题目明确非必需，或本次时间预算内主动砍掉）：

- 真实发布对接（结构上就不提供该能力）。
- Web UI / 线上部署 / 多 Agent 协作 / 生产级 Runtime。
- 多素材批量拆单、跨频道联动排期。
- 真实素材服务、真实排期服务的对接（用本地 mock，标注边界）。
- 权限体系、审计后台、消息通知。

## 6. 成功标准（可测量）

| 编号 | 成功标准 | 验证证据 |
| --- | --- | --- |
| S1 | 无风险且时段空闲 → 审核 pass，草稿落在请求时段 | `examples/cases/E01_normal/`，`tests/test_scenarios.py` |
| S2 | 请求时段被占 → 给出替代时段并标记待确认，草稿为 `pending_confirmation`，绝不称已发布 | `examples/cases/E02_slot_conflict/`（题面参考用例） |
| S3 | 素材字段缺失 → 先补查，补查无效则 `missing_info` + 不保存草稿 | `examples/cases/E03_missing_info/` |
| S4 | 命中 block 级风险 → `blocked`，不保存草稿，列出风险与待确认 | `examples/cases/E04_rule_risk/` |
| S5 | 相同请求重复提交 → 草稿数恒为 1，第二次返回 `duplicate` 且复用同一 draft_id | `examples/cases/E05_duplicate_submit/`，`tests/test_store.py::test_concurrent_saves_produce_exactly_one` |
| S6 | 保存失败 → 明确 `failed` + 错误原因，退出码 2，不虚报成功 | `examples/cases/E06_save_failure/` |
| S7 | 硬性发布时间被占 → 不改时间、不保存、升级待确认 | `examples/cases/E07_hard_constraint/`，`tests/test_policy.py` |
| S8 | 触达步数/调用上限 → `incomplete_limit_reached`，退出码 3 | `examples/cases/E11_step_limit/` |
| S9 | 模型谎报「已保存 + 已发布」→ 校验层按工具事实打回，运行记为违约 | `examples/cases/E10_claim_violation/`，`tests/test_verifier.py` |
| S10 | 全部断言可通过两条命令复现 | `python -m unittest discover -s tests`；`python -m src.cli replay --model offline` |

## 7. 关键业务规则（决定实现形态）

1. **hard / soft 是唯一事实来源**：`brief.time_constraint=hard` 表示合同/用户约定的首发时间，未经用户同意不得改动；`soft` 可以挪，但必须标待确认。
2. **缺失 ≠ 无授权**：字段为 `null` 表示「还不知道」→ 可补查，补查无效则 `missing_info`；字段为 `false` 表示「明确没有」→ 直接 `blocked`。这条区分让「信息缺失」与「规则风险」两个验收点不会互相污染。
3. **排期冲突不算素材风险**：`category=scheduling` 的命中（时段占用、禁发、配额、竞品）不改变 `review_status`，只影响排期决策。否则题面里「M01 无风险但有冲突」会被渲染成「素材有风险」。
4. **草稿不等于发布**：系统里没有任何发布工具，输出层也不允许出现发布措辞。

## 8. 假设与依赖

- 单实例、文件系统级草稿库（`os.O_CREAT|O_EXCL` 提供原子性），不假设有数据库。
- 时区固定为运营侧本地时间（字符串 `HH:MM`），不做跨时区换算。
- 排期网格 30 分钟，发布窗口 09:00–22:00，内容间隔 ≥ 30 分钟，单频道单日上限 6 条（均在 `rules.json` 可调）。
- 补查服务与排期服务为本地 mock，会如实返回「上游也没有这条信息」。

# 03 评估：测试预期、实际结果、失败与限制

## 1. 怎么复现（两条命令）

```bash
python -m unittest discover -s tests -v      # 111 个测试，零安装
python -m src.cli replay --model offline     # 11 个用例全跑 + 自检断言 + 刷新 examples 产物
```

`replay` 会用每个用例自带的 `expect` 断言，并打印 PASS/FAIL；全部通过时退出码 0。
实测输出（节选，完整表格见仓库根 README 的「实测结果」）：

```
E02_slot_conflict   completed  pass  draft_saved_pending_confirmation  saved  7/6  PASS
E05_duplicate_submit completed pass  draft_exists                      duplicate 7/6 PASS
E06_save_failure    failed     pass  save_failed                       failed 8/7  PASS
全部用例符合预期（含刻意失败/未完成的用例）。
```

## 2. 测试清单（111 个，全绿）

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_rules.py` | 16 | 10 条规则的三态（pass/missing/risk）、review 与 scheduling 分类隔离、汇总优先级 |
| `tests/test_slots.py` | 13 | 占用/禁发/间隔/配额、自家草稿可复用、顺延优先排序、非法时间 |
| `tests/test_store.py` | 14 | 幂等（10 次只 1 份）、**8 线程并发只成功 1 次**、supersede、真实 OSError 失败路径 |
| `tests/test_policy.py` | 19 | 注册表无发布能力、硬性要求拒绝、block 拒绝、缺失拒绝、**占用时段拒绝**、故障注入 |
| `tests/test_verifier.py` | 15 | 谎报发布/保存、审核状态放水、硬性要求被改建议、阻断素材出现草稿 |
| `tests/test_agent_loop.py` | 13 | 步数/调用/重复上限、非法动作、参数错误、未知工具、模型失败降级、trace 结构不变量 |
| `tests/test_scenarios.py` | 11 | 11 个端到端用例 + 产物与代码一致性 + 「输出中不得出现已发布措辞」 |
| `tests/test_cli.py` | 10 | 退出码 0/1/2/3/4、产物落盘、inspect、reset-store、replay 自检能发现被改坏的期望 |

## 3. 验收点矩阵（题目必验项 → 证据）

| 验收点 | 用例 | 关键断言 | 证据文件 |
| --- | --- | --- | --- |
| 正常排期 | E01 | `review=pass`、`decision=draft_saved`、草稿落在 10:00 | `examples/cases/E01_normal/{actual_output.json,report.md,trace.jsonl}` |
| 素材信息缺失 | E03 | 先 `fetch_material_assets`，`review=missing_info`，**不保存草稿** | `examples/cases/E03_missing_info/` |
| 规则风险 | E04 | `review=blocked`，风险含 R03（肖像）+ R04（极限词），无草稿 | `examples/cases/E04_rule_risk/` |
| 时段冲突 | E02 | 18:00 被 M09 占用 → 建议 19:00 → `pending_confirmation` → 未发布 | `examples/cases/E02_slot_conflict/` |
| 重复提交 | E05 | 同一请求跑两次：run1 `saved`、run2 `duplicate`，**同一 draft_id，草稿数 1** | `examples/cases/E05_duplicate_submit/{report.run1.md,report.run2.md}` |
| 保存失败 | E06 | 注入 2 次 EACCES → 重试一次 → `failed` + 退出码 2 | `examples/cases/E06_save_failure/` |
| 不得擅自改硬性要求 | E07 | 硬性 18:00 被占 → `conflict_blocked`，**suggested_slot=null**，无草稿 | `examples/cases/E07_hard_constraint/` |
| 工具失败（第二种） | E08 | `query_slots` 连续超时 → 不猜不存 → `tool_failure`，退出码 2 | `examples/cases/E08_slot_tool_down/` |
| 更换占用时段并重复提交 | E09 | 18:00 空出后重排 18:00，旧 20:30 草稿 `superseded`，活跃仍 1 份 | `examples/cases/E09_resubmit_after_change/` |
| 越权发布 / 虚报保存成功 | E10 | 谎报 `published` + `save_status=saved` → 两条 override + `critical=true` | `examples/cases/E10_claim_violation/` |
| 执行上限与未完成态 | E11 | `incomplete_limit_reached`，`tool_calls_used=8`，退出码 3 | `examples/cases/E11_step_limit/` |
| ReAct 决策证据 | 真实模型 | 两条路径的动作序列不同，且都是「看到工具结果才改动作」 | `examples/traces/real/E02_slot_conflict/`、`examples/traces/real/E03_missing_info/` |

## 4. 11 个用例实测结果（`--model offline`，一次性重置草稿库）

| 用例 | 运行状态 | 审核状态 | 处置 | 保存状态 | 步数/调用 | 断言 |
| --- | --- | --- | --- | --- | --- | --- |
| E01_normal | completed | pass | draft_saved | saved | 7/6 | PASS |
| E02_slot_conflict | completed | pass | draft_saved_pending_confirmation | saved | 7/6 | PASS |
| E03_missing_info | completed | missing_info | needs_info | not_attempted | 5/4 | PASS |
| E04_rule_risk | completed | blocked | review_blocked | not_attempted | 4/3 | PASS |
| E05_duplicate_submit | completed | pass | draft_exists | duplicate | 7/6 | PASS |
| E06_save_failure | failed | pass | save_failed | failed | 8/7 | PASS |
| E07_hard_constraint | completed | pass | conflict_blocked | not_attempted | 6/5 | PASS |
| E08_slot_tool_down | failed | warn | tool_failure | not_attempted | 7/6 | PASS |
| E09_resubmit_after_change | completed | pass | draft_saved | saved | 7/6 | PASS |
| E10_claim_violation | failed | pass | not_attempted | not_attempted | 1/0 | PASS |
| E11_step_limit | incomplete_limit_reached | pass | not_attempted | not_attempted | 9/8 | PASS |

## 5. 真实模型的两条 ReAct 路径（`deepseek-flash`，temperature=0）

位置：`examples/traces/real/E02_slot_conflict/trace.jsonl`、`examples/traces/real/E03_missing_info/trace.jsonl`
（用 `python -m src.cli inspect --trace <path>` 可读）。

**路径 A：E02 冲突调整（6 步 / 5 次工具）**

| 步 | 动作 | 观察到的关键信息 | 后续动作的决策依据（模型自述） |
| --- | --- | --- | --- |
| 1 | `read_material(M01)` | 无缺失字段 | 先读素材 |
| 2 | `get_publish_brief(M01)` | `time_constraint=soft`，请求 18:00 | 确认是软性要求，可协商 |
| 3 | `check_rules(M01)` | `expected_status=pass`，命中 R07 | 素材本身无风险，只是排期冲突 |
| 4 | `query_slots(2025-06-01, near=18:00)` | `conflict=slot_occupied`，占用者 M09，候选 19:00 起 | **因为看到冲突**，才决定换时段 |
| 5 | `save_schedule_draft(M01, 19:00)` | 保存成功，`pending_confirmation` | 工单是 soft，挪到最近候选并标待确认 |
| 6 | `final` | — | `suggested_slot=19:00`、`save_status=saved`、notes 写明「仅草稿未发布」 |

对照题面参考实例：M01 无风险、用户要 18:00、该时段被占、19:00 可用 → **建议 19:00 并标记待确认、未称已发布**，与要求一致。

**路径 B：E03 信息缺失补查（5 步 / 4 次工具）**

| 步 | 动作 | 观察 | 决策依据 |
| --- | --- | --- | --- |
| 1 | `read_material(M03)` | `duration_sec` 缺失 | 先看素材 |
| 2 | `get_publish_brief(M03)` | soft，2025-06-04 19:00 | 读工单 |
| 3 | `fetch_material_assets(M03)` | 仍缺 `music_license`、`duration_sec` | **补查后仍缺**，不能假设授权 |
| 4 | `check_rules(M03)` | `missing_info`，命中 R01、R02 | 确认资料缺失级别 |
| 5 | `final` | — | `review_status=missing_info`、`save_status=not_attempted`、`suggested_slot=null` |

两条路径的动作序列不同（B 甚至把补查放在规则检查之前，而离线模型是先查规则再补查），且第 4→5 步的转向都由第 3/4 步的返回内容触发 —— 这是 ReAct 而不是固定流程的直接证据。

## 6. 失败、差异与限制（如实记录）

### 6.1 实现过程中真实踩到并修掉的问题

1. **候选排序选了 17:00 而不是 19:00**：初版按「到请求时段的距离」排序，17:00 与 19:00 距离相同、按字典序选中了更早的时段，与题面期望不符。改为「优先顺延，其次提前」后 E02 才与参考实例一致。
2. **保存失败却返回 `completed`**：初版循环只要模型给出 final 就算完成，E06 因此显示「完成但没存下来」。增加收尾策略：保存失败、或时段查询连续失败导致无法核实，整次运行判 `failed`（退出码 2）。
3. **`verifier` 在模型没写 `review_status` 时误报违约**：初版把「未声明」当作「放水」。改为缺省时用事实补齐、只有明确声明更宽松才算违约，由 `tests/test_verifier.py` 固化。
4. **相对时间导致的排序不稳定**：一度出现黑名单词表与时段占用判定互相污染，最终把规则拆成 `review` / `scheduling` 两类，`review_status` 只由前者决定。
5. **`deepseek-v41-flash` 这个模型名不存在**：接口返回可用名为 `deepseek-flash` / `deepseek-v4-pro`，改用 `deepseek-flash` 并写进 `.env.example`。
6. **本机 Python 是 3.8 且没有 pytest**：因此全项目按 3.8+ 语法写（`from __future__ import annotations`）、测试改用标准库 `unittest`，保证评审零安装可跑。

### 6.2 已知限制

1. **离线策略模型不是决策效果证据**：它的规则是人写的，只证明流程与护栏。真实模型只有两条 trace 且是单次采样（temperature=0 但服务端仍可能有细微差异），不代表稳定性。
2. **单素材、单工单**：一次运行只处理一条素材；批量拆单与多素材联动未实现。
3. **并发只在存储层验证**：`os.O_EXCL` 保证同进程/跨进程单文件原子，但没有做多实例压测；`supersede` 的扫描-改写不是事务，极端并发下可能漏标（已记录为下一步）。
4. **时区固定**：时间为本地 `HH:MM` 字符串，未处理跨时区与夏令时。
5. **规则与数据是合成的**：10 条规则覆盖了常见审核要素，但不是真实业务规则库；黑名单只有 5 个词。
6. **补查服务是 mock**：`fetch_material_assets` 直接读本地 JSON，不会真的去上游拿数据；它只能模拟「上游也没有」。
7. **排期服务是本地文件**：`query_slots` 没有网络超时语义，E08 的超时是注入的。
8. **未做权限与审计后台**：谁能批准待确认项、批准记录如何落库，都超出本次范围。
9. **Windows 控制台编码**：CLI 内部强制 UTF-8 输出；极老终端仍可能乱码，可设 `PYTHONIOENCODING=utf-8`。
10. **replay 会改写 `examples/`**：它刷新产物是设计使然（保证文档不过期），因此不要在只想「看」的时候跑它。

### 6.3 如果再多给一些时间，会先补

1. 把「待确认项」做成可回写的确认流（同一草稿从 `pending_confirmation` → `confirmed`，并记录确认人）。
2. 多素材批量排期 + 同一工单拆多条素材的配额分配。
3. supersede 的目录级锁，替换扫描-改写。
4. 真实模型跑满 11 个用例，统计「合规率 / 需人工纠正率」，把评审从「能不能跑」变成「跑得好不好」。

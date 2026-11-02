# 素材审核与发布排期 Agent

> 小影科技校招 · AI Agent 开发岗笔试 · **题三：素材审核与发布排期 Agent**
> 给一条待发素材 + 一张工单，输出「审核结论 + 无冲突的建议时段 + 待确认项 + 草稿保存状态」。
> **只保存草稿，系统里根本不存在发布能力。**

## 交付信息

| 项 | 值 |
| --- | --- |
| 题号 | 题三 · 素材审核与发布排期 Agent |
| GitHub | `https://github.com/LuoQian1/shadow_agent_test` |
| 最终 commit | 见仓库最新提交（`git rev-parse HEAD`）；核心代码与产物的那次提交为 `448a6d7` |
| 评审可见性 | 计划公开仓库；若为私有，请把评审账号加为 Read |
| 运行方式 | **CLI**（题目允许 CLI / HTTP API / Notebook 三选一） |
| 技术栈 | Python 3.8+，运行时**零第三方依赖**（仅标准库），测试用标准库 `unittest` |
| 模型 | `deepseek-flash`（OpenAI 兼容接口）；也可完全离线运行 |

## 三条命令（评审请从这里开始）

```bash
# 1) 跑测试（零安装，111 个用例）
python -m unittest discover -s tests -v

# 2) 一键重放 11 个场景并刷新 examples 产物（离线，无需 key、无需网络）
python -m src.cli replay --model offline

# 3) 用真实模型跑题面参考用例（需先在 .env 里填 key）
python -m src.cli run --case examples/cases/E02_slot_conflict/input.json --model real
```

环境准备：

```bash
git clone <仓库地址> && cd <仓库目录>
python --version          # 需要 3.8 及以上；不需要 pip install
cp .env.example .env      # 只在跑 --model real 时需要填 OPENAI_API_KEY
```

Windows PowerShell 一键跑（测试 + 重放）：

```powershell
.\examples\run_all.ps1            # 离线
.\examples\run_all.ps1 -Model real  # 真实模型
```

## 实测结果（`python -m src.cli replay --model offline`）

```
case                             run_status               review         decision                         save           steps/tools
E01_normal                       completed                pass           draft_saved                      saved          7/6   PASS
E02_slot_conflict                completed                pass           draft_saved_pending_confirmation saved          7/6   PASS
E03_missing_info                 completed                missing_info   needs_info                       not_attempted  5/4   PASS
E04_rule_risk                    completed                blocked        review_blocked                   not_attempted  4/3   PASS
E05_duplicate_submit             completed                pass           draft_exists                     duplicate      7/6   PASS
E06_save_failure                 failed                   pass           save_failed                      failed         8/7   PASS
E07_hard_constraint              completed                pass           conflict_blocked                 not_attempted  6/5   PASS
E08_slot_tool_down               failed                   warn           tool_failure                     not_attempted  7/6   PASS
E09_resubmit_after_change        completed                pass           draft_saved                      saved          7/6   PASS
E10_claim_violation              failed                   pass           not_attempted                    not_attempted  1/0   PASS
E11_step_limit                   incomplete_limit_reached pass           not_attempted                    not_attempted  9/8   PASS
全部用例符合预期（含刻意失败/未完成的用例）。
```

测试：`Ran 111 tests ... OK`（规则 16 / 时段 13 / 存储 14 / 护栏 19 / 校验 15 / 循环 13 / 场景 11 / CLI 10）。

真实模型 `deepseek-flash` 的两条 ReAct 记录：`examples/traces/real/E02_slot_conflict/`、`examples/traces/real/E03_missing_info/`。

## 评审入口（按顺序看这 6 处就够）

| 顺序 | 文件 | 看点 |
| --- | --- | --- |
| 1 | [`examples/cases/E02_slot_conflict/report.md`](examples/cases/E02_slot_conflict/report.md) | 题面参考用例：18:00 被占 → 建议 19:00 → 待确认 → 仅草稿 |
| 2 | [`examples/cases/E05_duplicate_submit/report.run2.md`](examples/cases/E05_duplicate_submit/report.run2.md) | 重复提交：`duplicate` + 同一 draft_id，草稿数 1 |
| 3 | [`examples/cases/E06_save_failure/report.md`](examples/cases/E06_save_failure/report.md) | 注入保存失败 → 明确 `failed`，不虚报 |
| 4 | [`examples/cases/E07_hard_constraint/report.md`](examples/cases/E07_hard_constraint/report.md) | 硬性 18:00 被占：不改时间、不保存、升级 |
| 5 | [`examples/traces/real/E02_slot_conflict/trace.jsonl`](examples/traces/real/E02_slot_conflict/trace.jsonl) | 真实模型的逐步 ReAct 记录 |
| 6 | [`docs/02-solution.md`](docs/02-solution.md) | 四层防线与取舍说明 |

## 目录结构

```
README.md                 本文件：环境、运行、评审入口
src/
  cli.py                  CLI：run / replay / inspect / reset-store / check-env
  runner.py               单次运行编排（CLI 与测试共用；含 expect 断言）
  config.py               .env 加载与护栏默认值
  data.py                 合成数据加载
  domain/                 领域层：模型、规则引擎、时段规划（纯函数）
  store/                  草稿库：幂等、原子写、supersede
  tools/                  7 个工具：能力边界、策略拒绝、故障注入
  models/                 模型适配：真实 / 离线策略 / 对抗（护栏验证）
  agent/                  动作校验、ReAct 循环、声明校验、渲染
tests/                    111 个用例，标准库 unittest
examples/
  cases/E01..E11/         每组：input.json + trace.jsonl + actual_output.json + report.md
  traces/real/            真实模型的两条运行记录
  README.md               产物字段与 trace 格式说明
  run_all.ps1 / run_all.sh 一键重放
data/                     素材、工单、排期、规则、黑名单（合成数据）
docs/                     01 需求 / 02 方案 / 03 评估 / 04 AI 使用 / 05 复盘 / 06 提交说明
requirements.txt          说明「运行时零依赖」
requirements-dev.txt      可选：想用 pytest 时安装
.env.example              需要哪些变量（不填也能离线跑）
```

## 关键设计（四句话）

1. **模型选动作，代码守边界。** 模型决定查什么、补不补查、换不换时段；硬性时间不可改、block 素材不入库、时段被占不许写，由工具层直接拒绝。
2. **草稿不等于发布。** 没有 `publish` / `confirm` 工具，`published` 恒为 `false`，「已发布」措辞有测试兜着。
3. **幂等键由代码派生。** `D-` + `sha256(素材|日期|时段|频道)`，`O_CREAT|O_EXCL` 原子写；8 线程并发只成功一次。
4. **报告只信工具台账。** 模型 final 里的声明会被 `verifier` 逐条核对，谎报保存/发布一律降级并记 `critical`。

## 验收点 → 证据

| 验收点 | 用例 | 证据 |
| --- | --- | --- |
| 正常排期 | E01 | `examples/cases/E01_normal/` |
| 素材信息缺失（补查无效） | E03 | `examples/cases/E03_missing_info/` |
| 规则风险（肖像+极限词） | E04 | `examples/cases/E04_rule_risk/` |
| 时段冲突（题面参考实例） | E02 | `examples/cases/E02_slot_conflict/` |
| 重复提交只产生一份草稿 | E05 | `examples/cases/E05_duplicate_submit/` |
| 保存失败明确返回失败 | E06 | `examples/cases/E06_save_failure/` |
| 不得擅自改硬性发布要求 | E07 | `examples/cases/E07_hard_constraint/` |
| 工具失败不猜结果 | E08 | `examples/cases/E08_slot_tool_down/` |
| 更换占用时段并重复提交 | E09 | `examples/cases/E09_resubmit_after_change/` |
| 防越权发布 / 防虚报成功 | E10 | `examples/cases/E10_claim_violation/` |
| 执行上限 + 明确未完成 | E11 | `examples/cases/E11_step_limit/` |
| ReAct 决策（两条不同路径） | 真实模型 | `examples/traces/real/` |

完整矩阵与失败分析见 [`docs/03-evaluation.md`](docs/03-evaluation.md)。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 完成（含「阻断 / 需补料 / 待确认」这类正常业务结论） |
| 1 | 参数或环境错误（如缺少 `OPENAI_API_KEY`、用例文件不存在） |
| 2 | 保存失败，或契约违约 / 工具失败导致任务未完成 |
| 3 | 触达步数或工具调用上限，返回明确「未完成」 |
| 4 | `replay` 自检断言不符 |

## 已知限制

- 离线策略模型只验证流程与护栏，**不代表真实模型决策效果**；真实模型只做了 2 条单次采样。
- 一次运行只处理一条素材，未做批量拆单与多素材联动。
- 补查 / 排期 / 发布均为本地 mock，不含真实第三方服务与时区处理。
- `supersede` 的扫描-改写不是事务，多实例并发下可能漏标（单实例有测试保障）。
- 规则与数据为合成数据，规则表 10 条、黑名单 5 词，仅覆盖常见审核要素。

详细分析见 [`docs/03-evaluation.md` 第 6 节](docs/03-evaluation.md)。

## 安全与隐私

- `.env` 已在 `.gitignore` 中，仓库只提交 `.env.example`（占位符）。
- 全部数据为合成数据，不含真实用户信息。
- 系统不执行任何真实发布动作。

## 实际用时

| 阶段 | 用时 |
| --- | --- |
| 环境核对（Python 版本 / 模型可用性） | 约 2 分钟 |
| 数据与规则设计 + 11 个用例定义 | 约 3 分钟 |
| 领域层 / 工具层 / 循环 / 校验 / CLI | 约 8 分钟 |
| 测试补齐与缺陷返工（排序、状态语义、校验层） | 约 4 分钟 |
| 真实模型两条 trace | 约 2 分钟 |
| 文档与产物整理 | 约 5 分钟 |
| **合计（AI 协作下的墙钟时间）** | **约 24 分钟** |

题目建议投入 3 小时。本实现按「3 小时能交付的核心链路」取舍：把时间压在冲突调整、确认边界、防重复、防虚报这四件事的可验证性上，未做 UI / 部署 / 多 Agent / 批量排期。

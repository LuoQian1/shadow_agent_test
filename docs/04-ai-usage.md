# 04 AI 使用说明：范围、修改与验证

> 本文按「如实记录」的要求写：哪些由 AI 生成、哪些判断由人做出、如何验证。
> 标注 `TODO(候选人)` 的部分需要你按自己的实际操作补全后再提交 —— 这部分只有你知道。

## 1. 使用范围

| 环节 | AI 参与度 | 产物 | 人工把关点 |
| --- | --- | --- | --- |
| 题目拆解、验收点定义 | 低（人定方向，AI 补充清单） | `docs/01-requirements.md` | 是否覆盖题面 6 个必验项 |
| 数据与规则设计 | 中（AI 生成合成数据，人确认规则语义） | `data/*.json` | null 与 false 的语义、hard/soft 的判定 |
| 领域层（规则引擎/时段规划） | 高（AI 生成初版） | `src/domain/` | 冲突排序、分类隔离是否与业务一致 |
| 工具层与护栏 | 高（AI 生成初版） | `src/tools/` | 幂等键能否被模型伪造、硬性要求是否真的拦得住 |
| ReAct 循环与校验层 | 高（AI 生成初版） | `src/agent/` | 「没跑完不许说完成」「虚报必须打回」是否成立 |
| 测试用例 | 高（AI 生成） | `tests/` | 断言是否真的能发现回归（见 3.2 的反向验证） |
| 真实模型接入与两条 trace | 中（AI 写适配器并执行） | `src/models/openai_compat.py`、`examples/traces/real/` | 模型名、额度、是否允许把 key 写进 `.env` |
| 文档 | 高（AI 起草） | `docs/`、`README.md` | 结论是否与实测数字一致 |

技术栈与环境约束（Python 3.8 兼容、零第三方依赖、标准库 `unittest`、CLI 运行方式）是**人在开工前决定的约束**，AI 在这些约束内实现；本题「3 小时 MVP、只做单素材核心链路」的范围也是人定的。

## 2. AI 生成后被推翻/修正的具体点

以下每一条都能在代码或测试里找到对应位置，属于「不修改就会扣分」的缺陷：

| # | AI 初版的问题 | 人工判定 | 最终实现 |
| --- | --- | --- | --- |
| 1 | 候选时段按「距离请求时段」排序，E02 会建议 **17:00**（提前），与题面参考实例的 19:00 不符 | 发布被占位后应先顺延，提前发来不及预热 | `src/domain/slots.py`：排序键改为 `(是否早于请求时段, 距离, 时间)` |
| 2 | 只要模型给出 final 就判 `completed`，导致「保存失败」也显示完成 | 交付物含草稿状态，没存下来就不算完成 | `src/agent/loop.py`：收尾阶段按台账把运行降级为 `failed` |
| 3 | `verifier` 把「模型没写 review_status」当成「放水」报违约 | 未声明 ≠ 违约，缺省应补事实值 | `src/agent/verifier.py`：仅当明确声明更宽松才记 `REVIEW_STATUS_ESCALATED` |
| 4 | 规则命中不分类型，时段占用会把 `review_status` 拖成 `warn`，与「M01 无风险」矛盾 | 排期冲突不是素材风险 | `rules.json` 增加 `category`，汇总只看 `review` 类 |
| 5 | 幂等键可由模型通过 `idempotency_key` 参数传入 | 能让模型换 key 就等于没有幂等 | 改为代码派生 `sha256(material|date|time|channel)`，参数校验拒绝该字段 |
| 6 | `save_schedule_draft` 不检查目标时段是否已被占用 | 会把「无冲突排期」变成一个可绕过的承诺 | 增加 `POLICY_SLOT_CONFLICT` 拒绝 |
| 7 | 题面要求「更换占用时段并重复提交」时，两份草稿都算活跃 | 同一素材同一天应只有一份活跃草稿 | 新草稿 supersede 旧草稿，保留 `superseded_by` 审计 |
| 8 | 模型名写成 `deepseek-v41-flash`（不存在） | 以接口返回的可用名为准 | 改为 `deepseek-flash`，写进 `.env.example` |
| 9 | 初版按 Python 3.11 + pytest 设计 | 本机是 3.8 且无 pytest，评审不应被安装卡住 | 全项目 3.8 兼容 + `unittest`，`tests/conftest.py` 兼容 pytest |
| 10 | 资料缺失时仍回填「建议时段 19:00」 | 没落草稿却给时间点会误导运营 | `renderer` 规定未落草稿的结论不给建议时段 |

## 3. 验证方式

### 3.1 正向验证

- `python -m unittest discover -s tests` → 111 个测试全部通过（含 8 线程并发只写 1 份、真实 `OSError` 写入失败）。
- `python -m src.cli replay --model offline` → 11 个用例逐条比对 `expect`，全部 PASS，退出码 0。
- 真实模型 `deepseek-flash` 跑通 E02 / E03 两条路径，产物在 `examples/traces/real/`，用 `inspect` 可逐步复核。

### 3.2 反向验证（证明断言不是摆设）

- `tests/test_cli.py::test_replay_detects_broken_expectation`：故意把一个用例的期望改成 `09:00`，replay 必须判 FAIL 并以退出码 4 退出。
- `tests/test_policy.py::test_occupied_slot_denies_save`：绕过 Agent 直接调工具保存到被占时段，必须被拒绝且草稿库为空。
- `tests/test_verifier.py`：伪造 `published=true` / `save_status=saved`，必须产生 `critical` override。
- `examples/cases/E10_claim_violation`：用故意说谎的假模型跑完整链路，输出里 `published=false`、`save_status=not_attempted`。

### 3.3 我没有让 AI 单独决定的事

1. **是否允许把 API key 写进 `.env`**（已 gitignore），以及用哪个模型。
2. **提交到哪个 GitHub 仓库、是否公开**（私有仓库需给评审账号授权）。
3. **04/05 两篇文档里关于「个人贡献与反思」的表述** —— 这部分必须由本人按真实情况填写。
4. **是否接受「离线策略模型」作为兜底证据**：如果评审严格要求真实模型证据，需要额外跑更多真实用例。

## 4. 边界声明

- 所有业务数据、规则、素材均为合成，不含真实用户信息与密钥。
- 真实模型只在本地 `.env` 中读取 key，未写入任何受版本管理的文件；`.env.example` 只有占位符。
- 离线策略模型只用于流程与护栏验证，不代表真实模型的决策质量（见 `docs/03-evaluation.md` 第 6.2 节）。

# 02 方案：调用流程、动态决策与取舍

## 1. 分层架构

```
CLI (src/cli.py)
  └── runner.py            单次运行编排：装数据 → 建工具 → 建模型 → 跑循环 → 渲染 → 落盘
        ├── agent/loop.py      ReAct 循环 + 预算护栏 + 事实台账
        │     ├── agent/actions.py    动作规范化（tool / final 两种，其余拒绝）
        │     └── agent/verifier.py   声明校验（以工具事实为准纠偏）
        ├── agent/renderer.py  结构化交付物 + 人读报告（全部取自台账）
        ├── models/            openai_compat（真实） / offline_policy（离线） / adversarial（护栏验证）
        ├── tools/             7 个工具：能力边界 + 策略拒绝 + 故障注入
        ├── domain/            规则引擎、时段规划（纯函数，无 IO）
        └── store/             草稿库：幂等 + 原子写 + supersede
```

数据流：`工单 + 素材 + 排期/草稿 + 规则` → 工具 → **事实台账（facts）** → 渲染与校验。模型只负责「下一步做什么」，不负责「事实是什么」。

## 2. 工具清单（模型能做的全部事情）

| 工具 | 职责 | 副作用 |
| --- | --- | --- |
| `read_material` | 读素材元数据与缺失字段 | 无 |
| `get_publish_brief` | 读工单（含 hard/soft） | 无 |
| `check_rules` | 10 条规则判定 + 汇总审核状态 | 无 |
| `query_slots` | 查占用、禁发窗口、冲突、候选时段 | 无 |
| `fetch_material_assets` | 向上游补查资产明细 | 无（mock） |
| `list_drafts` | 查已有草稿 | 无 |
| `save_schedule_draft` | 保存草稿 | **唯一写操作** |

刻意不提供的：`publish` / `confirm` / `approve` / `update_requirement`。
**越权发布不是靠提示词禁止，而是靠「能力不存在」**，`tests/test_policy.py` 断言注册表里没有它们，且调用时参数校验直接判 `unknown tool`。

## 3. ReAct 循环与动作协议

每一轮的 `state`：`{task, facts(累积事实), history(动作+观察), budget, limits}`。

模型每轮只能输出一个 JSON：

```json
{"rationale": "对外可见的一句话决策依据", "action": {"type": "tool", "name": "...", "args": {...}}}
{"rationale": "...", "action": {"type": "final", "claims": {"review_status": "...", "suggested_slot": {...}, "save_status": "...", "notes": "..."}}}
```

代码侧校验后才执行：工具白名单 → 参数 schema → 同工具同参数次数上限 → 工具调用预算。任何一步被拒都会作为「观察」回灌给模型，模型据此改变策略。

预算与终止：`max_steps=12`、`max_tool_calls=8`、`max_repeat=2`。触达即 `status=incomplete_limit_reached`，退出码 3，输出明确写「未完成」，不假装完成。

## 4. 动态决策：三条路径说明（不是固定分支）

真实模型（`deepseek-flash`）的两条 trace 在 `examples/traces/real/`，离线模型在 `examples/cases/`。它们走的路径**不一样**，说明动作确实由观察结果驱动：

| 路径 | 真实模型实际动作序列 | 关键判断 |
| --- | --- | --- |
| `E02_slot_conflict`（真实模型） | read_material → get_publish_brief → check_rules → **query_slots（看到 18:00 被 M09 占用）** → save(19:00) → final | 因为观察到 R07 冲突 + 工单是 soft，才把时间从 18:00 改到 19:00，并写明「待运营确认」 |
| `E03_missing_info`（真实模型） | read_material（看到 duration 缺失）→ get_publish_brief → **fetch_material_assets（补查）** → check_rules（missing_info）→ final（不保存） | 先补查再判定，补查结果为空才收尾；没有假设授权存在 |
| `E03_missing_info`（离线模型） | read_material → get_publish_brief → **check_rules** → fetch_material_assets → final | 顺序不同但结论一致：说明流程骨架可复用，决策点在于「观察到什么」 |
| `E09_resubmit_after_change` | … list_drafts（看到旧草稿）→ query_slots（18:00 已空出）→ save(18:00) | 新方案覆盖旧方案时把旧草稿标记 superseded，活跃草稿仍为 1 |

离线策略模型是「读 facts 的状态机」而不是「按 case_id 写死的脚本」：同一份函数服务 11 个用例，并且真的会根据观察改变动作（补查无效才收尾、查询失败会重试、保存失败重试一次、被策略拒绝就不再重试）。但它仍是**人写的策略**，所以文档与产物都标注：**它只证明流程与护栏，不证明真实模型决策效果**。

## 5. 四层防线的分工

| 层 | 位置 | 拦截什么 | 证据 |
| --- | --- | --- | --- |
| L1 能力边界 | `tools/registry.py` | 越权发布/确认 | `test_policy.py::test_no_publish_or_confirm_capability` |
| L2 策略拒绝 | `tools/impl.py::save_schedule_draft` | 改硬性时间、block 素材入库、资料缺失入库、**把草稿排进别人的时段** | `test_policy.py::TestPolicyDenial` |
| L3 幂等与原子性 | `store/draft_store.py` | 重复提交产生第二份草稿 | `test_store.py`（含 8 线程并发） |
| L4 声明校验 | `agent/verifier.py` | 谎报已保存/已发布、审核状态放水、建议违背硬性要求 | `test_verifier.py`、`E10_claim_violation` |

一个容易忽略的点：**L2 会重算规则**。模型说「这条素材没问题」不构成事实，`save_schedule_draft` 内部重新跑一遍规则引擎，命中 block 就拒绝。同理，`draft_id` 由代码从 `sha256(material_id|date|time|channel)` 派生，模型不能自定义幂等键（传 `idempotency_key` 会被参数校验拒绝）。

## 6. 幂等设计（防重复提交）

- 键：`D-` + `sha256(material_id|date|time|channel)[:12]`，完全确定，与模型无关。
- 写：`os.open(path, O_CREAT|O_EXCL|O_WRONLY)`。文件系统充当锁，两个并发请求只有一个能建成，另一个收到 `FileExistsError` → 返回 `duplicate` + 原 `draft_id`。
- 语义：**同一素材同一时段 = 同一份草稿实体**。重复提交返回已有草稿，不新建、不修改。
- supersede：同一素材在同一天同一频道换了时段并重新提交时，新草稿生效，旧草稿被标记 `superseded` + `superseded_by`，保留审计痕迹；活跃草稿仍只有一份。
- 为什么不做「物理删除」：排期变更本身是业务事件，删掉就无法回答「昨天那个 20:30 方案去哪了」。

## 7. 冲突调整语义

1. 占用来源 = 静态排期 + 活跃草稿（少了后者，重复提交会算出第二个冲突草稿）。
2. 冲突只看**外部**占用；同一素材自己的草稿不算冲突，仍可作为候选（重新保存 = 幂等命中，正是期望行为）。
3. 候选时段必须与外部排期留出 `min_gap_min=30` 的间隔。
4. 排序偏好：**优先顺延，其次提前**（`is_before` 先排序，再比距离）。业务上一条内容被占位后提前发往往来不及预热，顺延更容易获批。这条规则让 E02 建议 19:00 而不是 17:00，与题面期望一致。
5. `hard` + 冲突 → 不给替代时段、不保存，直接升级为待确认（`decision=conflict_blocked`）。

## 8. 技术选型与取舍

| 选择 | 理由 | 代价 |
| --- | --- | --- |
| 原生实现，不用 LangGraph/ADK | 本题得分点是可验证性：步数上限、幂等、策略拒绝、trace 可复现。框架会把这些藏进内部，评审难判断是我实现的还是框架给的 | 需要自己写循环与预算（约 200 行） |
| 纯标准库（`urllib` 调模型） | 评审 `git clone` 后零安装就能跑；旧环境（Python 3.8）也能跑 | 没有 SDK 的类型与重试糖，需手写（已含 429/5xx 退避、JSON 模式降级） |
| 测试用 `unittest` 而非 pytest | 不装任何包也能 `python -m unittest discover -s tests` | 断言写法比 pytest 略啰嗦；已提供 `tests/conftest.py` 兼容 pytest |
| JSON 文件草稿库，不用 SQLite | 可直接看到草稿文件、diff 友好、O_EXCL 天然原子 | 不支持复杂查询；只在单机场景成立 |
| CLI 而非 HTTP API（题目三选一） | 退出码可被测试断言（0/1/2/3/4），评审复制命令即可复现 | 没有并发服务形态 |
| `rationale` 而非 chain-of-thought | 满足「记录决策依据」又不要求交出模型内部思维 | 依据是概要，不是完整推理 |
| 数据全 JSON，不用 YAML | 免掉 PyYAML 依赖 | 不支持注释（用文档说明字段含义） |

## 9. 失败与异常的统一语义

| 情况 | `save_status` | 运行状态 | 退出码 |
| --- | --- | --- | --- |
| 正常保存 | `saved` | completed | 0 |
| 重复提交 | `duplicate` | completed | 0 |
| 硬性要求冲突/阻断/资料缺失（未尝试保存） | `not_attempted` | completed（业务结论） | 0 |
| 被策略拒绝 | `denied` | completed 或 failed | 0 / 2 |
| 写入失败（重试无效） | `failed` | **failed** | 2 |
| 时段查询失败导致无法核实 | `not_attempted` | **failed** | 2 |
| 触达预算上限 | `not_attempted` | **incomplete_limit_reached** | 3 |
| 用例断言不符（replay 自检） | — | — | 4 |

「completed」只表示 Agent 给出了完整结论，不表示素材一定排上了；`decision` 字段才是业务处置结果。

## 10. 模型接入与降级

- 真实模型：OpenAI 兼容 `/chat/completions`，`temperature=0`，`response_format=json_object`（不支持时自动降级为纯文本 JSON 解析）。
- 失败重试：429/5xx/网络错误指数退避最多 3 次。
- 仍失败且允许降级：切到离线策略模型**就着已有 facts 续跑**（不是从头重跑），trace 记 `model_degraded`，输出 `limitations` 明说，退出码不受影响但结论标注降级。
- 不允许静默：没有 key 时直接报错并提示 `--model offline`。

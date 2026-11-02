# 真实模型的运行记录

这里的两条记录由**真实模型**（`deepseek-flash`，OpenAI 兼容接口，`temperature=0`）跑出来，
用于证明「模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束」的循环确实成立。

| 目录 | 场景 | 结果 |
| --- | --- | --- |
| `E02_slot_conflict/` | 18:00 被 M09 占用，工单是 soft 约束 | 4 步工具调用后改建议 19:00，保存 `pending_confirmation` 草稿，未发布 |
| `E03_missing_info/` | 时长与音乐授权缺失 | 先补查、补查仍缺，判 `missing_info`，**未保存草稿** |

生成命令：

```bash
python -m src.cli run --case examples/cases/E02_slot_conflict/input.json --model real \
  --out examples/traces/real/E02_slot_conflict --store data/store_real --reset-store
python -m src.cli run --case examples/cases/E03_missing_info/input.json --model real \
  --out examples/traces/real/E03_missing_info --store data/store_real
```

阅读方式：

```bash
python -m src.cli inspect --trace examples/traces/real/E02_slot_conflict/trace.jsonl
```

## 必须说明的三点边界

1. **单次采样**：`temperature=0` 不保证服务端完全确定性，这两条是各跑一次的结果，不代表稳定复现率。
2. **不覆盖全部用例**：11 个场景的常规回归走离线策略模型（`--model offline`），成本可控、结果确定；
   真实模型只跑两条最关键的路径（冲突调整、信息缺失补查）。
3. **不证明「离线模型等于真实模型」**：离线策略模型是确定性状态机（人写的策略），只在无网络时兜底；
   两者路径不同恰恰说明决策由「观察到什么」驱动，见 `docs/03-evaluation.md` 第 5 节。

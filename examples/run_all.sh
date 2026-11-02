#!/usr/bin/env bash
# 一键重放（macOS / Linux）
#   bash examples/run_all.sh            # 离线策略模型，无需网络与 key
#   bash examples/run_all.sh real       # 真实模型（需先在 .env 配置 key）
set -euo pipefail

MODEL="${1:-offline}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 1/3 运行单元与场景测试 =="
python3 -m unittest discover -s tests -v

echo "== 2/3 重放全部用例并刷新 examples 产物 =="
python3 -m src.cli replay --model "$MODEL"

echo "== 3/3 完成：查看 examples/cases/E02_slot_conflict/report.md =="

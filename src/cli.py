"""命令行入口。

用法（评审可复制的三条命令）：
    python -m src.cli replay --model offline          # 一键复现 11 个用例（无需网络）
    python -m src.cli run --case examples/cases/E02_slot_conflict/input.json --model offline
    python -m src.cli run --case examples/cases/E02_slot_conflict/input.json --model real

退出码：0 完成（含阻断/待确认为正常业务结论） / 1 参数或环境错误 / 2 保存失败或契约违约 / 3 触达上限未完成
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .config import DEFAULT_CASES_DIR, DEFAULT_STORE_DIR, REPO_ROOT, Settings
from .data import Dataset
from .models.base import ModelError
from .runner import (
    EXIT_CHECK,
    EXIT_OK,
    EXIT_USAGE,
    check_case_expectations,
    load_case,
    resolve_model_name,
    run_case_series,
    summarize_result,
    worst_exit_code,
    write_artifacts,
)
from .store.draft_store import DraftStore

HEADER = "%-32s %-24s %-14s %-32s %-12s %s" % (
    "case",
    "run_status",
    "review",
    "decision",
    "save",
    "steps/tools",
)


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文与 report 里的中文混排容易乱码，这里统一成 UTF-8。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover
            pass


def _print(line: str = "") -> None:
    try:
        print(line)
    except UnicodeEncodeError:  # pragma: no cover - 老终端兜底
        encoding = sys.stdout.encoding or "utf-8"
        print(line.encode(encoding, "replace").decode(encoding, "replace"))


def _dataset(args: argparse.Namespace) -> Dataset:
    return Dataset(getattr(args, "data", None))


def _store(args: argparse.Namespace) -> DraftStore:
    return DraftStore(os.path.abspath(getattr(args, "store", None) or DEFAULT_STORE_DIR))


def cmd_run(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.case):
        _print("找不到用例文件：%s" % args.case)
        return EXIT_USAGE
    case = load_case(args.case)
    settings = Settings.load()
    dataset = _dataset(args)
    store = _store(args)
    if args.reset_store:
        store.reset()
    out_dir = args.out or os.path.dirname(case["_path"])
    try:
        results = run_case_series(case, args.model, settings, dataset, store)
    except (ModelError, ValueError) as exc:
        _print("运行失败：%s" % exc)
        return EXIT_USAGE
    written = write_artifacts(case, results, out_dir)
    summary = summarize_result(case, results)

    if args.json:
        _print(json.dumps(results[-1]["output"], ensure_ascii=False, indent=2))
    else:
        _print("用例：%s  %s" % (case.get("case_id"), case.get("title") or ""))
        _print("模型：%s（%s）" % (summary["model"], resolve_model_name(case, args.model)))
        _print("运行状态：%s   审核状态：%s   处置：%s" % (
            summary["status"], summary["review_status"], summary["decision"]))
        _print("请求时段：%s   建议时段：%s" % (
            ((results[-1]["output"]["materials"][0].get("requested_slot") or {}).get("time")),
            summary["suggested_time"] or "无"))
        _print("草稿保存：%s %s" % (summary["save_status"], summary["draft_id"] or ""))
        if summary["overrides"]:
            _print("校验层拦截：%s" % ", ".join(summary["overrides"]))
        for item in results:
            _print("  第 %s 次运行 -> %s / save=%s" % (
                item["run"]["run_index"], item["run"]["status"],
                ((item["output"]["materials"][0].get("draft") or {}).get("save_status"))))
        _print("产物：%s" % ", ".join(sorted(os.path.basename(p) for p in written.values())))
    return worst_exit_code(results)


def cmd_replay(args: argparse.Namespace) -> int:
    cases_dir = os.path.abspath(args.cases or DEFAULT_CASES_DIR)
    if not os.path.isdir(cases_dir):
        _print("找不到用例目录：%s" % cases_dir)
        return EXIT_USAGE
    settings = Settings.load()
    dataset = _dataset(args)
    store = _store(args)
    if not args.keep_store:
        removed = store.reset()
        _print("已重置草稿库（删除 %s 个文件）：%s" % (removed, store.root))

    case_dirs = sorted(
        name for name in os.listdir(cases_dir) if os.path.isfile(os.path.join(cases_dir, name, "input.json"))
    )
    if not case_dirs:
        _print("用例目录为空：%s" % cases_dir)
        return EXIT_USAGE

    _print("")
    _print(HEADER)
    _print("-" * len(HEADER))
    codes: List[int] = []
    summaries: List[Dict[str, Any]] = []
    all_failures: List[str] = []
    for name in case_dirs:
        case_path = os.path.join(cases_dir, name, "input.json")
        case = load_case(case_path)
        try:
            results = run_case_series(case, args.model, settings, dataset, store)
        except (ModelError, ValueError) as exc:
            _print("%-32s %-24s %s" % (name, "ERROR", exc))
            codes.append(EXIT_USAGE)
            all_failures.append("%s: 运行异常 %s" % (name, exc))
            continue
        write_artifacts(case, results, os.path.dirname(case_path))
        summary = summarize_result(case, results)
        summaries.append(summary)
        codes.append(summary["exit_code"])
        failures = check_case_expectations(case, results, store)
        for item in failures:
            all_failures.append("%s: %s" % (name, item))
        _print("%-32s %-24s %-14s %-32s %-12s %s/%s  %s" % (
            name,
            summary["status"],
            summary["review_status"],
            summary["decision"],
            summary["save_status"],
            summary["steps"],
            summary["tool_calls"],
            "PASS" if not failures else "FAIL",
        ))
    _print("")
    _print("共 %s 个用例，最高退出码 %s" % (len(case_dirs), max(codes) if codes else 0))
    if args.write_summary:
        summary_path = os.path.join(cases_dir, "..", "replay_summary.json")
        with open(os.path.abspath(summary_path), "w", encoding="utf-8") as handle:
            json.dump(
                {"model": args.model, "cases": summaries}, handle, ensure_ascii=False, indent=2
            )
            handle.write("\n")
        _print("汇总：%s" % os.path.abspath(summary_path))
    if all_failures:
        _print("")
        _print("断言失败 %s 项：" % len(all_failures))
        for item in all_failures:
            _print("  - %s" % item)
        return EXIT_CHECK
    _print("全部用例符合预期（含刻意失败/未完成的用例）。")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    path = args.trace
    if not os.path.isfile(path):
        _print("找不到 trace：%s" % path)
        return EXIT_USAGE
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            kind = record.get("type")
            if kind == "model_action":
                action = record.get("action") or {}
                if action.get("type") == "tool":
                    _print("[%s] step %s TOOL %s %s" % (
                        record.get("run_id"), record.get("step"), action.get("name"),
                        json.dumps(action.get("args") or {}, ensure_ascii=False)))
                else:
                    _print("[%s] step %s FINAL %s" % (
                        record.get("run_id"), record.get("step"),
                        json.dumps(action.get("claims") or {}, ensure_ascii=False)))
                if record.get("rationale"):
                    _print("        依据：%s" % record["rationale"])
            elif kind == "tool_result":
                _print("        观察：%s" % record.get("summary"))
            elif kind == "run_end":
                _print("        结论：status=%s steps=%s tools=%s overrides=%s" % (
                    record.get("status"), record.get("steps_used"), record.get("tool_calls_used"),
                    [o.get("code") for o in record.get("overrides") or []]))
            elif kind in ("model_degraded", "tool_rejected", "invalid_action", "limit_reached"):
                _print("[%s] %s step=%s %s" % (
                    record.get("run_id"), kind, record.get("step"),
                    record.get("reason") or record.get("error") or record.get("detail") or ""))
    return EXIT_OK


def cmd_reset_store(args: argparse.Namespace) -> int:
    store = _store(args)
    removed = store.reset()
    _print("草稿库已清空：%s（删除 %s 个文件）" % (store.root, removed))
    return EXIT_OK


def cmd_check_env(args: argparse.Namespace) -> int:
    settings = Settings.load()
    _print("python      : %s" % sys.version.split()[0])
    _print("repo root   : %s" % REPO_ROOT)
    for key, value in settings.masked().items():
        _print("%-12s: %s" % (key, value))
    _print("api_key     : %s" % ("已配置" if settings.api_key else "未配置（可用 --model offline 离线运行）"))
    if settings.api_key and not args.no_probe:
        _print("提示        : 用 `python -m src.cli run --case <用例> --model real` 验证真实模型链路")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="素材审核与发布排期 Agent（只保存草稿，不执行任何发布动作）",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--model", default="offline",
                            help="offline（默认，离线策略模型）| real（OpenAI 兼容真实模型）")
        target.add_argument("--store", default=None, help="草稿库目录，默认 data/store")
        target.add_argument("--data", default=None, help="数据目录，默认 data")

    run = sub.add_parser("run", help="运行单个用例")
    run.add_argument("--case", required=True, help="用例 input.json 路径")
    run.add_argument("--out", default=None, help="产物输出目录，默认同用例目录")
    run.add_argument("--reset-store", action="store_true", help="运行前清空草稿库")
    run.add_argument("--json", action="store_true", help="把输出 JSON 打到 stdout")
    add_common(run)
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="批量重放全部用例并刷新 examples 产物")
    replay.add_argument("--cases", default=None, help="用例根目录，默认 examples/cases")
    replay.add_argument("--keep-store", action="store_true", help="保留现有草稿库（默认重置）")
    replay.add_argument("--write-summary", action="store_true", help="额外输出 examples/replay_summary.json")
    add_common(replay)
    replay.set_defaults(func=cmd_replay)

    inspect = sub.add_parser("inspect", help="以人读方式打印 trace.jsonl")
    inspect.add_argument("--trace", required=True, help="trace.jsonl 路径")
    inspect.set_defaults(func=cmd_inspect)

    reset = sub.add_parser("reset-store", help="清空草稿库")
    reset.add_argument("--store", default=None, help="草稿库目录，默认 data/store")
    reset.set_defaults(func=cmd_reset_store)

    env = sub.add_parser("check-env", help="打印运行环境（不打印密钥明文）")
    env.add_argument("--no-probe", action="store_true")
    env.set_defaults(func=cmd_check_env)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

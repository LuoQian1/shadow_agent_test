"""单次运行的编排层：CLI 与测试共用同一入口，保证「文档里的命令」和「测试断言」跑的是同一条路。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .agent.loop import AgentRunner
from .agent.renderer import build_output, build_report
from .config import Settings
from .data import Dataset
from .domain.slots import SlotPlanner
from .models.adversarial import ADVERSARIAL_MODELS
from .models.base import Model
from .models.offline_policy import OfflinePolicyModel
from .models.openai_compat import OpenAICompatModel
from .store.draft_store import DraftStore
from .tools.impl import ToolBox

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2
EXIT_LIMIT = 3
EXIT_CHECK = 4

STATUS_TO_EXIT = {
    "completed": EXIT_OK,
    "failed": EXIT_FAILED,
    "incomplete_limit_reached": EXIT_LIMIT,
}


def load_case(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        case = json.load(handle)
    if not isinstance(case, dict) or not case.get("material_id"):
        raise ValueError("用例文件缺少 material_id：%s" % path)
    case.setdefault("case_id", os.path.basename(os.path.dirname(os.path.abspath(path))))
    case["_path"] = os.path.abspath(path)
    return case


def build_model(name: str, settings: Settings) -> Model:
    key = (name or "offline").strip().lower()
    if key in ("real", "openai", "openai-compat", "api"):
        return OpenAICompatModel(settings)
    if key in ("offline", "mock", "policy"):
        return OfflinePolicyModel()
    if key in ADVERSARIAL_MODELS:
        return ADVERSARIAL_MODELS[key]()
    raise ValueError("未知模型：%s（可选 real / offline / %s）" % (name, " / ".join(ADVERSARIAL_MODELS)))


def resolve_model_name(case: Dict[str, Any], cli_model: str) -> str:
    """用例可以钉死模型（对抗用例），否则用命令行指定。"""
    return str(case.get("model") or cli_model or "offline")


def make_toolbox(case: Dict[str, Any], dataset: Dataset, store: DraftStore) -> ToolBox:
    planner = SlotPlanner(dataset.rules, dataset.blacklist, dataset.materials)
    brief = dataset.brief(case.get("brief_id")) or dataset.brief_for_material(case.get("material_id"))
    return ToolBox(
        dataset=dataset,
        planner=planner,
        store=store,
        session_material=case.get("material_id"),
        session_brief=brief,
        faults=case.get("faults"),
        overlay_entries=case.get("schedule_overlay"),
    )


def run_case(
    case: Dict[str, Any],
    model_name: str,
    settings: Settings,
    dataset: Dataset,
    store: DraftStore,
    run_index: int = 1,
    trace_name: str = "trace.jsonl",
) -> Dict[str, Any]:
    toolbox = make_toolbox(case, dataset, store)
    model = build_model(model_name, settings)
    fallback = OfflinePolicyModel() if model.kind == "real" else None
    task = {
        "case_id": case.get("case_id"),
        "material_id": case.get("material_id"),
        "brief_id": case.get("brief_id"),
        "run_index": run_index,
    }
    runner = AgentRunner(model, toolbox, settings, task, fallback_model=fallback)
    active_before = store.count_active(material_id=case.get("material_id"))
    result = runner.run()
    active_after = store.count_active(material_id=case.get("material_id"))
    output = build_output(result, case, store, trace_path=trace_name)
    # 记录本次运行对草稿库的净影响：对抗用例要证明「一步都没写进去」
    output["store"]["active_before"] = active_before
    output["store"]["active_after"] = active_after
    output["store"]["active_delta"] = active_after - active_before
    return {"run": result, "output": output, "toolbox": toolbox}


def run_case_series(
    case: Dict[str, Any], cli_model: str, settings: Settings, dataset: Dataset, store: DraftStore
) -> List[Dict[str, Any]]:
    """按 case.repeat 连续跑 N 次（重复提交场景），首次运行前执行 setup.seed_drafts。"""
    model_name = resolve_model_name(case, cli_model)
    repeat = int(case.get("repeat") or 1)
    results: List[Dict[str, Any]] = []
    for index in range(1, repeat + 1):
        if index == 1:
            for seed in (case.get("setup") or {}).get("seed_drafts") or []:
                store.seed(seed)
        results.append(run_case(case, model_name, settings, dataset, store, run_index=index))
    return results


def worst_exit_code(results: List[Dict[str, Any]]) -> int:
    codes = [STATUS_TO_EXIT.get(item["run"]["status"], EXIT_USAGE) for item in results]
    return max(codes) if codes else EXIT_OK


def write_artifacts(
    case: Dict[str, Any], results: List[Dict[str, Any]], out_dir: str
) -> Dict[str, str]:
    """落盘：actual_output.json / report.md / trace.jsonl（多轮时为 runN 后缀）。"""
    os.makedirs(out_dir, exist_ok=True)
    trace_path = os.path.join(out_dir, "trace.jsonl")
    written: Dict[str, str] = {"trace": trace_path}

    with open(trace_path, "w", encoding="utf-8") as handle:
        for item in results:
            for record in item["run"]["trace"]:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")

    multi = len(results) > 1
    for index, item in enumerate(results, start=1):
        output = item["output"]
        output["trace_path"] = os.path.basename(trace_path)
        suffix = ".run%d" % index if multi else ""
        output_path = os.path.join(out_dir, "actual_output%s.json" % suffix)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        report_path = os.path.join(out_dir, "report%s.md" % suffix)
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(build_report(output, item["run"]["trace"], case))
        written["output%s" % suffix] = output_path
        written["report%s" % suffix] = report_path

    # 多轮时另存一份「最后一轮」的通用入口，便于评审直接打开
    if multi:
        last = results[-1]["output"]
        last_path = os.path.join(out_dir, "actual_output.json")
        with open(last_path, "w", encoding="utf-8") as handle:
            json.dump(last, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        written["output"] = last_path
    return written


def summarize_result(case: Dict[str, Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    last = results[-1]
    material = (last["output"].get("materials") or [{}])[0]
    draft = material.get("draft") or {}
    return {
        "case_id": case.get("case_id"),
        "status": last["run"]["status"],
        "exit_code": worst_exit_code(results),
        "review_status": material.get("review_status"),
        "decision": material.get("decision"),
        "suggested_time": (material.get("suggested_slot") or {}).get("time"),
        "save_status": draft.get("save_status"),
        "draft_id": draft.get("draft_id"),
        "steps": last["run"]["usage"]["steps_used"],
        "tool_calls": last["run"]["usage"]["tool_calls_used"],
        "model": last["run"]["model_info"]["final"],
        "runs": [
            {
                "run_index": item["run"]["run_index"],
                "status": item["run"]["status"],
                "save_status": ((item["output"].get("materials") or [{}])[0].get("draft") or {}).get(
                    "save_status"
                ),
            }
            for item in results
        ],
        "overrides": [o.get("code") for o in last["output"]["verifier"]["overrides"]],
    }


def check_case_expectations(
    case: Dict[str, Any], results: List[Dict[str, Any]], store: DraftStore
) -> List[str]:
    """按用例里的 expect 断言语义，返回失败描述列表（空列表 = 全部通过）。

    CLI 的 replay 与 tests/test_scenarios.py 共用这一份校验，避免「文档/样例/测试」三份口径。
    """
    expect = case.get("expect") or {}
    if not expect:
        return []
    failures: List[str] = []
    last = results[-1]
    out = last["output"]
    run = last["run"]
    material = (out.get("materials") or [{}])[0]
    draft = material.get("draft") or {}
    material_id = material.get("material_id") or case.get("material_id")
    date = (material.get("requested_slot") or {}).get("date")

    def eq(key: str, actual: Any) -> None:
        if key in expect and expect[key] != actual:
            failures.append("%s 期望 %r，实际 %r" % (key, expect[key], actual))

    def at_least(key: str, actual: int) -> None:
        if key in expect and actual < int(expect[key]):
            failures.append("%s 期望 >= %r，实际 %r" % (key, expect[key], actual))

    eq("run_status", run.get("status"))
    eq("review_status", material.get("review_status"))
    eq("decision", material.get("decision"))
    eq("save_status", draft.get("save_status"))
    eq("exit_code", worst_exit_code(results))
    eq("tool_calls_used", run.get("usage", {}).get("tool_calls_used"))

    if "suggested_time" in expect:
        actual_time = (material.get("suggested_slot") or {}).get("time")
        if (expect["suggested_time"] or None) != (actual_time or None):
            failures.append("suggested_time 期望 %r，实际 %r" % (expect["suggested_time"], actual_time))

    at_least("pending_confirmations_min", len(material.get("pending_confirmations") or []))
    at_least("overrides_min", len(out.get("verifier", {}).get("overrides") or []))
    at_least("save_attempts_min", int(draft.get("attempts") or 0))
    at_least("slot_query_attempts_min", int((material.get("evidence") or {}).get("slot_query_failures") or 0))

    if "active_drafts" in expect:
        actual_active = len(store.active_drafts(material_id=material_id, date=date))
        if actual_active != int(expect["active_drafts"]):
            failures.append(
                "active_drafts(%s/%s) 期望 %r，实际 %r"
                % (material_id, date, expect["active_drafts"], actual_active)
            )
    if "active_drafts_delta" in expect:
        delta = int((out.get("store") or {}).get("active_delta") or 0)
        if delta != int(expect["active_drafts_delta"]):
            failures.append(
                "active_drafts_delta 期望 %r，实际 %r" % (expect["active_drafts_delta"], delta)
            )
    if "superseded_drafts" in expect:
        actual_superseded = [
            item
            for item in store.superseded_drafts(material_id=material_id)
            if (item.get("slot") or {}).get("date") == date
        ]
        if len(actual_superseded) != int(expect["superseded_drafts"]):
            failures.append(
                "superseded_drafts(%s/%s) 期望 %r，实际 %r"
                % (material_id, date, expect["superseded_drafts"], len(actual_superseded))
            )

    if "published" in expect and bool(material.get("published")) is not bool(expect["published"]):
        failures.append("published 期望 %r，实际 %r" % (expect["published"], material.get("published")))

    if expect.get("did_fetch_assets") is True:
        if not (material.get("evidence") or {}).get("assets_fetched"):
            failures.append("did_fetch_assets 期望 True，实际未补查")

    if "risk_rule_ids_contains" in expect:
        actual_ids = {item.get("rule_id") for item in material.get("risk_reasons") or []}
        for rule_id in expect["risk_rule_ids_contains"]:
            if rule_id not in actual_ids:
                failures.append("risk_reasons 缺少规则 %s（实际 %s）" % (rule_id, sorted(actual_ids)))

    if "conflict_occupant" in expect:
        occupant = (material.get("conflict") or {}).get("occupant")
        if occupant != expect["conflict_occupant"]:
            failures.append("conflict.occupant 期望 %r，实际 %r" % (expect["conflict_occupant"], occupant))

    if "first_run_save_status" in expect and len(results) > 1:
        first = ((results[0]["output"].get("materials") or [{}])[0].get("draft") or {}).get("save_status")
        if first != expect["first_run_save_status"]:
            failures.append("first_run_save_status 期望 %r，实际 %r" % (expect["first_run_save_status"], first))

    if expect.get("same_draft_id") and len(results) > 1:
        ids = [
            (((item["output"].get("materials") or [{}])[0].get("draft")) or {}).get("draft_id")
            for item in results
        ]
        if len(set(ids)) != 1:
            failures.append("same_draft_id 期望同一 draft_id，实际 %r" % (ids,))

    if expect.get("hard_constraint_respected"):
        requested = material.get("requested_slot") or {}
        suggested = material.get("suggested_slot") or {}
        if requested.get("time_constraint") != "hard":
            failures.append("hard_constraint_respected 用例前提不成立（不是 hard）")
        if suggested:
            failures.append("硬性要求场景不应给出替代建议时段，实际 %r" % (suggested,))
        if draft.get("save_status") not in ("not_attempted", "denied", None):
            failures.append("硬性要求被占时不得保存草稿，实际 save_status=%r" % draft.get("save_status"))

    return failures

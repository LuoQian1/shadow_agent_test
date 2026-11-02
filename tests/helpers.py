"""测试公共脚手架（不依赖 pytest，标准库 unittest 直接可用）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import Settings  # noqa: E402
from src.data import Dataset  # noqa: E402
from src.domain.slots import SlotPlanner  # noqa: E402
from src.models.base import Model  # noqa: E402
from src.runner import (  # noqa: E402
    check_case_expectations,
    load_case,
    make_toolbox,
    run_case_series,
    worst_exit_code,
)
from src.store.draft_store import DraftStore  # noqa: E402

CASES_DIR = os.path.join(ROOT, "examples", "cases")
DATASET = Dataset()
SETTINGS = Settings()  # 默认值即可：离线模型不读 .env


def case_names() -> List[str]:
    if not os.path.isdir(CASES_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(CASES_DIR)
        if os.path.isfile(os.path.join(CASES_DIR, name, "input.json"))
    )


def case_path(name: str) -> str:
    return os.path.join(CASES_DIR, name, "input.json")


def load(name: str) -> Dict[str, Any]:
    return load_case(case_path(name))


def temp_store() -> DraftStore:
    base = tempfile.mkdtemp(prefix="agent-test-")
    return DraftStore(os.path.join(base, "store"))


def run(
    case: Dict[str, Any],
    store: Optional[DraftStore] = None,
    model: str = "offline",
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    own_store = store is None
    target = store or temp_store()
    return run_case_series(case, model, settings or SETTINGS, DATASET, target)


def output(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    return results[-1]["output"]


def material(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    return (output(results).get("materials") or [{}])[0]


def planner() -> SlotPlanner:
    return SlotPlanner(DATASET.rules, DATASET.blacklist, DATASET.materials)


def schedule_entries(overlay: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    return planner().build_entries(DATASET.schedule, [], overlay or [])


def toolbox(
    case: Optional[Dict[str, Any]] = None,
    store: Optional[DraftStore] = None,
    faults: Optional[Dict[str, Any]] = None,
):
    case = case or {"case_id": "unit", "material_id": "M01", "brief_id": "B-M01-v1"}
    target = store or temp_store()
    if faults:
        case = dict(case)
        case["faults"] = faults
    return make_toolbox(case, DATASET, target), target


class ScriptedModel(Model):
    """按脚本吐动作的假模型；脚本元素可以是 dict（返回）或 Exception（抛出）。"""

    name = "scripted"
    kind = "test"

    def __init__(self, actions: List[Any]) -> None:
        super().__init__()
        self.actions = list(actions)
        self.index = 0

    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        if not self.actions:
            raise AssertionError("脚本已空")
        action = self.actions[min(self.index, len(self.actions) - 1)]
        self.index += 1
        if isinstance(action, Exception):
            raise action
        return action


def tool_call(name: str, args: Dict[str, Any], rationale: str = "测试动作") -> Dict[str, Any]:
    return {"rationale": rationale, "action": {"type": "tool", "name": name, "args": args}}


def final_call(claims: Dict[str, Any], rationale: str = "测试结论") -> Dict[str, Any]:
    return {"rationale": rationale, "action": {"type": "final", "claims": claims}}

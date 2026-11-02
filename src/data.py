"""合成数据加载。所有业务数据都是本地 JSON，无外部依赖。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .config import DATA_DIR


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class Dataset(object):
    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = data_dir or DATA_DIR
        materials = _read_json(os.path.join(self.data_dir, "materials.json"))
        briefs = _read_json(os.path.join(self.data_dir, "briefs.json"))
        self.materials: Dict[str, Dict[str, Any]] = {m["material_id"]: m for m in materials}
        self.briefs: Dict[str, Dict[str, Any]] = {b["brief_id"]: b for b in briefs}
        self.schedule: List[Dict[str, Any]] = _read_json(os.path.join(self.data_dir, "schedule.json"))
        self.rules: Dict[str, Any] = _read_json(os.path.join(self.data_dir, "rules.json"))
        self.blacklist: Dict[str, Any] = _read_json(os.path.join(self.data_dir, "blacklist.json"))

    def material(self, material_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.materials.get(str(material_id) or "")

    def brief(self, brief_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.briefs.get(str(brief_id) or "")

    def brief_for_material(self, material_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """没有指定 brief_id 时，取该素材版本号最高的需求。"""
        candidates = [b for b in self.briefs.values() if b.get("material_id") == material_id]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.get("brief_version") or 0, item.get("brief_id") or ""))
        return candidates[-1]

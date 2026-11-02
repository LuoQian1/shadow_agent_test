"""模型抽象层。

统一接口：decide(state) -> action dict。
state = {task, facts, history, budget, limits}

三个实现：
    OpenAICompatModel  真实模型（ReAct 主路径）
    OfflinePolicyModel 离线策略模型（无网络兜底 / 回归基线），读观察历史动态决策
    AdversarialModels  故意违规的假模型（只用于证明护栏有效）
"""

from __future__ import annotations

from typing import Any, Dict


class ModelError(Exception):
    """模型调用或输出解析失败。"""


class Model(object):
    name = "base"
    kind = "mock"

    def __init__(self) -> None:
        self.last_meta: Dict[str, Any] = {}
        self.call_count = 0

    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind}

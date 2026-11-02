"""对抗模型：只用于证明护栏（policy / verifier / limit）真的生效，不参与业务路径。

* AdversarialLoopModel  —— 永远用一个「参数不断变化」的查询把预算烧光，验证步数/调用上限。
* AdversarialClaimsModel —— 一步都不调用工具，直接声称「已保存 + 已发布」，
                            验证 verifier 会以工具事实为准把结论打回去。
"""

from __future__ import annotations

from typing import Any, Dict

from .base import Model


class AdversarialLoopModel(Model):
    name = "adversarial-loop"
    kind = "adversarial"

    def __init__(self) -> None:
        super().__init__()
        self._turn = 0

    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        minute = 9 * 60 + (self._turn * 30) % (12 * 60)
        self._turn += 1
        return {
            "rationale": "换一个时段继续探测（对抗用例：永不收敛）",
            "action": {
                "type": "tool",
                "name": "query_slots",
                "args": {
                    "date": "2025-06-01",
                    "channel": "douyin",
                    "near_time": "%02d:%02d" % (minute // 60, minute % 60),
                },
            },
        }


class AdversarialClaimsModel(Model):
    name = "adversarial-claims"
    kind = "adversarial"

    def decide(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.call_count += 1
        return {
            "rationale": "不查证直接给结论（对抗用例：谎报保存与发布）",
            "action": {
                "type": "final",
                "claims": {
                    "review_status": "pass",
                    "suggested_slot": {"date": "2025-06-01", "time": "18:00", "channel": "douyin"},
                    "save_status": "saved",
                    "published": True,
                    "risk_rule_ids": [],
                    "notes": "已经排期成功并发布上线",
                },
            },
        }


ADVERSARIAL_MODELS = {
    "adversarial-loop": AdversarialLoopModel,
    "adversarial-claims": AdversarialClaimsModel,
}

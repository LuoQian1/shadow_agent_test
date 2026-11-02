"""素材审核与发布排期 Agent —— 源码包。

分层：
    domain/   领域模型、规则引擎、时段规划（纯函数，无 IO）
    store/    草稿存储（幂等、原子写、supersede）
    tools/    暴露给模型的工具（唯一能产生副作用的入口）
    models/   模型适配层（真实模型 / 离线策略模型 / 对抗模型）
    agent/    决策循环、动作校验、声明校验（verifier）、渲染
    runner.py 单次运行编排（CLI 与测试共用）
"""

__version__ = "1.0.0"

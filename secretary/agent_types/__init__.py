"""
集中化的 Agent 类型定义

每个 agent 类型在独立的模块中定义，包含：
- 触发规则
- 处理逻辑
- 提示词模板
- 配置构建函数
"""

from secretary.agent_types.worker import WorkerAgent
from secretary.agent_types.secretary import SecretaryAgent
from secretary.agent_types.boss import BossAgent
from secretary.agent_types.recycler import RecyclerAgent

__all__ = [
    "WorkerAgent",
    "SecretaryAgent",
    "BossAgent",
    "RecyclerAgent",
]


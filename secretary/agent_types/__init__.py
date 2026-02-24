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

# 导入注册表系统
from secretary.agent_registry import (
    AgentTypeRegistry,
    get_agent_type,
    register_agent_type,
    list_agent_types,
    has_agent_type,
    initialize_registry,
)

__all__ = [
    "WorkerAgent",
    "SecretaryAgent",
    "BossAgent",
    "RecyclerAgent",
    # 注册表相关
    "AgentTypeRegistry",
    "get_agent_type",
    "register_agent_type",
    "list_agent_types",
    "has_agent_type",
    "initialize_registry",
]

# 自动初始化注册表（延迟初始化，避免循环依赖）
def _auto_initialize_registry():
    """自动初始化注册表（延迟执行）"""
    try:
        import secretary.config as cfg
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        # 如果初始化失败，不影响其他功能
        pass

# 在模块导入时自动初始化（但延迟到实际使用时）
# 这里不立即调用，而是在首次使用时初始化


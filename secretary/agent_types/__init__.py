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

# 延迟导入注册表系统，避免循环依赖
def _lazy_import_registry():
    """延迟导入注册表系统"""
    from secretary.agent_registry import (
        AgentTypeRegistry,
        get_agent_type,
        register_agent_type,
        list_agent_types,
        has_agent_type,
        initialize_registry,
    )
    return {
        "AgentTypeRegistry": AgentTypeRegistry,
        "get_agent_type": get_agent_type,
        "register_agent_type": register_agent_type,
        "list_agent_types": list_agent_types,
        "has_agent_type": has_agent_type,
        "initialize_registry": initialize_registry,
    }

# 提供便捷访问（延迟导入）
def __getattr__(name):
    """延迟导入注册表相关函数"""
    if name in ["AgentTypeRegistry", "get_agent_type", "register_agent_type", 
                "list_agent_types", "has_agent_type", "initialize_registry"]:
        registry_module = _lazy_import_registry()
        return registry_module[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


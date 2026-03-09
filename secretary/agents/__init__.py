"""
Agents 模块 — 统一入口

重新导出 agents.py 中的注册表管理函数和 agent_paths.py 中的路径函数，保持向后兼容。
"""
import sys
import importlib.util
from pathlib import Path

# 动态导入 agents.py 模块（避免与当前目录名称冲突）
_agents_py_path = Path(__file__).parent.parent / "agents.py"
if _agents_py_path.exists():
    spec = importlib.util.spec_from_file_location("secretary_agents_registry", _agents_py_path)
    if spec and spec.loader:
        _agents_registry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_agents_registry)

        # 注册表管理函数
        list_workers = _agents_registry.list_workers
        register_agent = _agents_registry.register_agent
        register_worker = _agents_registry.register_worker
        remove_worker = _agents_registry.remove_worker
        get_worker = _agents_registry.get_worker
        update_worker_status = _agents_registry.update_worker_status
        set_agent_executing = _agents_registry.set_agent_executing
        increment_completed_tasks = _agents_registry.increment_completed_tasks
        record_task_completion = _agents_registry.record_task_completion
        pick_random_name = _agents_registry.pick_random_name
        pick_available_name = _agents_registry.pick_available_name
        build_workers_summary = _agents_registry.build_workers_summary
        build_known_agents_section = _agents_registry.build_known_agents_section
        get_worker_names = _agents_registry.get_worker_names
        get_all_running_pids = _agents_registry.get_all_running_pids
        stop_all_agents = _agents_registry.stop_all_agents
        save_agent_session_id = _agents_registry.save_agent_session_id
        load_agent_session_id = _agents_registry.load_agent_session_id
        # graph / known-agents API
        get_agent_known_agents   = _agents_registry.get_agent_known_agents
        set_agent_known_agents   = _agents_registry.set_agent_known_agents
        add_known_agent_link     = _agents_registry.add_known_agent_link
        remove_known_agent_link  = _agents_registry.remove_known_agent_link
        get_graph_data           = _agents_registry.get_graph_data
        known_agents_changed     = _agents_registry.known_agents_changed
        save_known_agents_snapshot = _agents_registry.save_known_agents_snapshot
        # custom types
        list_custom_types        = _agents_registry.list_custom_types
        get_custom_type          = _agents_registry.get_custom_type
        register_custom_type     = _agents_registry.register_custom_type
        delete_custom_type       = _agents_registry.delete_custom_type
    else:
        raise ImportError(f"Cannot load agents.py from {_agents_py_path}")
else:
    raise ImportError(f"Cannot find agents.py at {_agents_py_path}")

# 路径函数
from secretary.agent_paths import (
    _worker_dir,
    _worker_tasks_dir,
    _worker_assigned_dir,
    _worker_ongoing_dir,
    _worker_logs_dir,
    _worker_stats_dir,
    _worker_reports_dir,
)

__all__ = [
    # Registry
    "list_workers",
    "register_agent",
    "register_worker",
    "remove_worker",
    "get_worker",
    "update_worker_status",
    "set_agent_executing",
    "increment_completed_tasks",
    "record_task_completion",
    "pick_random_name",
    "pick_available_name",
    "build_workers_summary",
    "build_known_agents_section",
    "get_worker_names",
    "get_all_running_pids",
    "stop_all_agents",
    "save_agent_session_id",
    "load_agent_session_id",
    "get_agent_known_agents",
    "set_agent_known_agents",
    "add_known_agent_link",
    "remove_known_agent_link",
    "get_graph_data",
    "known_agents_changed",
    "save_known_agents_snapshot",
    "list_custom_types",
    "get_custom_type",
    "register_custom_type",
    "delete_custom_type",
    # Paths
    "_worker_dir",
    "_worker_tasks_dir",
    "_worker_assigned_dir",
    "_worker_ongoing_dir",
    "_worker_logs_dir",
    "_worker_stats_dir",
    "_worker_reports_dir",
]

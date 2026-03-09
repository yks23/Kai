"""
Agent 命令系统 — 允许 agent 通过命令文件操作主进程

命令文件格式：
- 位置：{BASE_DIR}/commands/{timestamp}-{agent_name}-{command_id}.json
- 内容：JSON 格式，包含命令类型、参数、来源 agent 等信息

支持的命令类型：
- create_agent: 创建新 agent
- start_agent: 启动 agent scanner
- send_task: 向其他 agent 发送任务
- link_agents: 链接两个 agents
- create_custom_type: 创建自定义 agent 类型
"""
import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

import secretary.config as cfg


# 命令目录
COMMANDS_DIR = cfg.BASE_DIR / "commands"
COMMANDS_PROCESSED_DIR = COMMANDS_DIR / "processed"
COMMANDS_FAILED_DIR = COMMANDS_DIR / "failed"


def _ensure_dirs():
    """确保命令目录存在"""
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDS_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    COMMANDS_FAILED_DIR.mkdir(parents=True, exist_ok=True)


def create_command(
    command_type: str,
    agent_name: str,
    params: Dict[str, Any],
    command_id: Optional[str] = None
) -> Path:
    """
    创建一个命令文件，供 agent 调用
    
    Args:
        command_type: 命令类型 (create_agent, start_agent, send_task, link_agents)
        agent_name: 发起命令的 agent 名称
        params: 命令参数（根据命令类型不同）
        command_id: 可选的命令 ID（用于去重），如果不提供则使用时间戳
    
    Returns:
        创建的命令文件路径
    """
    _ensure_dirs()
    
    if command_id is None:
        command_id = f"{int(time.time() * 1000)}"
    
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{agent_name}-{command_id}.json"
    command_file = COMMANDS_DIR / filename
    
    command_data = {
        "command_type": command_type,
        "agent_name": agent_name,
        "params": params,
        "command_id": command_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    
    command_file.write_text(
        json.dumps(command_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    return command_file


def list_pending_commands() -> List[Path]:
    """列出所有待处理的命令文件"""
    _ensure_dirs()
    if not COMMANDS_DIR.exists():
        return []
    
    return sorted([
        f for f in COMMANDS_DIR.iterdir()
        if f.is_file() and f.suffix == ".json"
    ], key=lambda p: p.stat().st_mtime)


def process_command(command_file: Path) -> Dict[str, Any]:
    """
    处理单个命令文件
    
    Returns:
        {"success": bool, "result": Any, "error": str}
    """
    try:
        command_data = json.loads(command_file.read_text(encoding="utf-8"))
        command_type = command_data.get("command_type")
        agent_name = command_data.get("agent_name")
        params = command_data.get("params", {})
        
        if not command_type or not agent_name:
            raise ValueError("Invalid command format: missing command_type or agent_name")
        
        # 执行命令
        result = _execute_command(command_type, agent_name, params)
        
        # 标记为已处理
        command_data["status"] = "processed"
        command_data["processed_at"] = datetime.now().isoformat()
        command_data["result"] = result
        
        # 移动到 processed 目录
        processed_file = COMMANDS_PROCESSED_DIR / command_file.name
        command_file.rename(processed_file)
        
        # 保存处理结果
        processed_file.write_text(
            json.dumps(command_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        return {"success": True, "result": result, "error": None}
        
    except Exception as e:
        error_msg = str(e)
        error_tb = traceback.format_exc()
        
        # 标记为失败
        try:
            command_data = json.loads(command_file.read_text(encoding="utf-8"))
            command_data["status"] = "failed"
            command_data["failed_at"] = datetime.now().isoformat()
            command_data["error"] = error_msg
            command_data["traceback"] = error_tb
        except:
            command_data = {
                "status": "failed",
                "failed_at": datetime.now().isoformat(),
                "error": error_msg,
                "traceback": error_tb,
            }
        
        # 移动到 failed 目录
        failed_file = COMMANDS_FAILED_DIR / command_file.name
        try:
            command_file.rename(failed_file)
        except:
            # 如果重命名失败，尝试复制后删除
            failed_file.write_text(command_file.read_text(encoding="utf-8"), encoding="utf-8")
            command_file.unlink()
        
        # 保存错误信息
        failed_file.write_text(
            json.dumps(command_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        return {"success": False, "result": None, "error": error_msg}


def _execute_command(command_type: str, agent_name: str, params: Dict[str, Any]) -> Any:
    """
    执行具体的命令
    
    Args:
        command_type: 命令类型
        agent_name: 发起命令的 agent
        params: 命令参数
    
    Returns:
        命令执行结果
    """
    if command_type == "create_agent":
        return _cmd_create_agent(agent_name, params)
    elif command_type == "start_agent":
        return _cmd_start_agent(agent_name, params)
    elif command_type == "send_task":
        return _cmd_send_task(agent_name, params)
    elif command_type == "link_agents":
        return _cmd_link_agents(agent_name, params)
    elif command_type == "create_custom_type":
        return _cmd_create_custom_type(agent_name, params)
    else:
        raise ValueError(f"Unknown command type: {command_type}")


def _cmd_create_agent(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行 create_agent 命令"""
    from secretary.agents import register_agent
    
    new_agent_name = params.get("name", "").strip().lower()
    if not new_agent_name:
        raise ValueError("Missing required parameter: name")
    
    agent_type = params.get("type", "worker").strip()
    description = params.get("description", "").strip()
    known_agents = params.get("known_agents", [])
    if isinstance(known_agents, str):
        known_agents = [ka.strip() for ka in known_agents.split(",") if ka.strip()]
    
    # 验证类型
    from secretary.agents import get_custom_type
    valid_builtins = {"worker", "secretary", "boss", "recycler"}
    if agent_type not in valid_builtins and not get_custom_type(agent_type):
        raise ValueError(f"Invalid agent type: {agent_type}")
    
    info = register_agent(
        new_agent_name,
        agent_type=agent_type,
        description=description,
        known_agents=known_agents
    )
    
    return {
        "agent": info,
        "message": f"Agent '{new_agent_name}' created successfully"
    }


def _cmd_start_agent(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行 start_agent 命令"""
    from secretary.cli import _start_agent_scanner
    from secretary.agents import get_worker
    
    target_name = params.get("name", "").strip().lower()
    if not target_name:
        raise ValueError("Missing required parameter: name")
    
    # 检查 agent 是否存在
    worker = get_worker(target_name)
    if not worker:
        raise ValueError(f"Agent '{target_name}' not found")
    
    # 检查是否已在运行
    if worker.get("pid"):
        return {
            "started": False,
            "message": f"Agent '{target_name}' is already running (PID: {worker.get('pid')})"
        }
    
    # 获取 agent 类型
    agent_type = worker.get("type", "worker")
    
    # 启动 scanner
    started = _start_agent_scanner(target_name, agent_type, silent=True)
    
    if started:
        return {
            "started": True,
            "message": f"Agent '{target_name}' scanner started successfully"
        }
    else:
        raise RuntimeError(f"Failed to start agent '{target_name}' scanner")


def _cmd_send_task(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行 send_task 命令"""
    from secretary.agent_paths import _worker_tasks_dir
    
    target_name = params.get("target", "").strip().lower()
    if not target_name:
        raise ValueError("Missing required parameter: target")
    
    task_content = params.get("content", "").strip()
    if not task_content:
        raise ValueError("Missing required parameter: content")
    
    # 检查目标 agent 是否存在
    from secretary.agents import get_worker
    target_worker = get_worker(target_name)
    if not target_worker:
        raise ValueError(f"Target agent '{target_name}' not found")
    
    # 写入任务文件
    tasks_dir = _worker_tasks_dir(target_name)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_file = tasks_dir / f"task-{timestamp}.md"
    task_file.write_text(task_content, encoding="utf-8")
    
    return {
        "target": target_name,
        "task_file": str(task_file),
        "message": f"Task sent to '{target_name}' successfully"
    }


def _cmd_link_agents(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行 link_agents 命令"""
    from secretary.agents import add_known_agent_link, get_worker
    
    target_name = params.get("target", "").strip().lower()
    if not target_name:
        raise ValueError("Missing required parameter: target")
    
    # 检查目标 agent 是否存在
    target_worker = get_worker(target_name)
    if not target_worker:
        raise ValueError(f"Target agent '{target_name}' not found")
    
    # 添加链接（agent_name 认识 target_name）
    add_known_agent_link(agent_name, target_name)
    
    return {
        "source": agent_name,
        "target": target_name,
        "message": f"Link created: '{agent_name}' now knows '{target_name}'"
    }


def _cmd_create_custom_type(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行 create_custom_type 命令"""
    from secretary.agents import register_custom_type
    
    type_name = params.get("name", "").strip().lower()
    if not type_name:
        raise ValueError("Missing required parameter: name")
    
    base_type = params.get("base_type", "worker").strip().lower()
    valid_bases = {"worker", "secretary", "boss", "recycler"}
    if base_type not in valid_bases:
        raise ValueError(f"Invalid base_type: {base_type}. Must be one of {valid_bases}")
    
    description = params.get("description", "").strip()
    first_prompt = params.get("first_prompt", "").strip()
    continue_prompt = params.get("continue_prompt", "").strip()
    
    if not first_prompt:
        raise ValueError("Missing required parameter: first_prompt")
    if not continue_prompt:
        raise ValueError("Missing required parameter: continue_prompt")
    
    # 注册自定义类型
    register_custom_type(
        type_name=type_name,
        base_type=base_type,
        description=description,
        first_prompt=first_prompt,
        continue_prompt=continue_prompt
    )
    
    return {
        "type_name": type_name,
        "base_type": base_type,
        "message": f"Custom agent type '{type_name}' created successfully"
    }


def process_all_pending_commands() -> List[Dict[str, Any]]:
    """
    处理所有待处理的命令
    
    Returns:
        处理结果列表
    """
    pending = list_pending_commands()
    results = []
    
    for command_file in pending:
        result = process_command(command_file)
        results.append({
            "file": command_file.name,
            **result
        })
    
    return results


# ============================================================
#  辅助函数：供 agent 在代码中调用
# ============================================================

def write_command_file(agent_name: str, command_type: str, params: Dict[str, Any]) -> Path:
    """
    供 agent 调用的便捷函数：写入命令文件
    
    示例：
        from secretary.command_system import write_command_file
        
        # 创建新 agent
        write_command_file("alice", "create_agent", {
            "name": "bob",
            "type": "worker",
            "description": "负责后端开发",
            "known_agents": ["alice"]
        })
        
        # 启动 agent
        write_command_file("alice", "start_agent", {"name": "bob"})
        
        # 发送任务
        write_command_file("alice", "send_task", {
            "target": "bob",
            "content": "请实现用户登录功能"
        })
        
        # 链接 agents
        write_command_file("alice", "link_agents", {"target": "bob"})
        
        # 创建自定义 agent 类型
        write_command_file("alice", "create_custom_type", {
            "name": "my-custom-type",
            "base_type": "worker",
            "description": "自定义类型描述",
            "first_prompt": "# 首轮提示词内容...",
            "continue_prompt": "# 续轮提示词内容..."
        })
    """
    return create_command(command_type, agent_name, params)


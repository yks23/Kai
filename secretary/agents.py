"""
Agent 注册表管理

每个 agent 有自己的名字、专属文件夹 ({BASE_DIR}/agents/{name}/tasks 和 agents/{name}/ongoing)，
但报告统一提交到 {BASE_DIR}/report/。

注册表存储在 {BASE_DIR}/agents/agents.json，记录:
  - Agent 名字
  - 招募时间
  - 擅长方向 (由秘书历史分配推断)
  - 已完成任务数
  - 最近完成的任务列表

秘书 Agent 在分配任务时会读取 agent 信息，决定分配给谁。

名字池:
  `kai hire` 不带名字时，自动从预设名字池中随机抽取一个可用名字。
"""
import json
import random
import shutil
from datetime import datetime
from pathlib import Path

import secretary.config as cfg


# ============================================================
#  预设名字池 — hire 不带名字时随机抽一个
# ============================================================

PRESET_NAMES: list[str] = [
    # 中文拼音风
    "kaisen", "kaicheng", "mingyu", "zhenwei", "haoran",
    "tianyu", "junhao", "yifan", "ruoxi", "lingling",
    "xiaoming", "dazhuang", "xiaohu", "afei", "aniu",
    "yichen", "zixuan", "yutong", "ruohan", "chenxi",
    "yuxuan", "zihan", "yiran", "ruoyi", "chenhan",
    # 英文名
    "alice", "bob", "charlie", "diana", "eve",
    "frank", "grace", "henry", "iris", "jack",
    "kate", "leo", "mia", "noah", "olive",
    "paul", "quinn", "ruby", "sam", "tina",
    "victor", "willa", "xander", "yara", "zoe",
    "adam", "bella", "carlos", "daisy", "ethan",
    "fiona", "george", "hannah", "ivan", "julia",
    # 有趣的代号
    "panda", "phoenix", "ninja", "rocket", "spark",
    "pixel", "byte", "nova", "echo", "flux",
    "zen", "arc", "nex", "ion", "ray",
    "max", "ace", "fox", "jet", "sky",
    # 简短代号
    "yks", "ykc", "ykx", "yky", "ykz",
    "aks", "akc", "akx", "aky", "akz",
]


def _agents_file() -> Path:
    return cfg.AGENTS_FILE


def _load_registry() -> dict:
    """加载 agent 注册表"""
    af = _agents_file()
    if af.exists():
        try:
            return json.loads(af.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"workers": {}}  # 保持向后兼容的键名
    return {"workers": {}}  # 保持向后兼容的键名


def _save_registry(registry: dict):
    """保存 agent 注册表"""
    af = _agents_file()
    af.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


# 路径辅助函数已移至 agent_paths.py，保持向后兼容
from secretary.agent_paths import (
    _worker_dir,
    _worker_tasks_dir,
    _worker_assigned_dir,
    _worker_ongoing_dir,
    _worker_logs_dir,
    _worker_stats_dir,
    _worker_reports_dir,
    _worker_memory_file,
)




# ============================================================
#  CRUD
# ============================================================

def register_agent(
    agent_name: str,
    agent_type: str = "worker",
    description: str = "",
    known_agents: list[str] | None = None,
) -> dict:
    """
    注册一个新 agent（统一接口）。
    known_agents: hire 时传入的依赖 agent 名称列表，持久化到 agents.json。
    """
    reg = _load_registry()

    if agent_name in reg["workers"]:
        updated = False
        entry = reg["workers"][agent_name]
        if description and entry.get("description") != description:
            entry["description"] = description
            updated = True
        if agent_type and entry.get("type") != agent_type:
            entry["type"] = agent_type
            updated = True
        if known_agents is not None and entry.get("known_agents") != known_agents:
            entry["known_agents"] = known_agents
            updated = True
        if updated:
            _save_registry(reg)
        return entry

    info = {
        "name": agent_name,
        "type": agent_type,
        "description": description,
        "hired_at": datetime.now().isoformat(),
        "completed_tasks": 0,
        "recent_tasks": [],
        "specialties": [],
        "known_agents": known_agents or [],
        "status": "idle",
        "pid": None,
        "executing": False,
    }
    reg["workers"][agent_name] = info
    _save_registry(reg)

    # 按 agent 类型只创建该类型需要的目录
    _worker_tasks_dir(agent_name).mkdir(parents=True, exist_ok=True)
    _worker_logs_dir(agent_name).mkdir(parents=True, exist_ok=True)
    if agent_type == "secretary":
        _worker_assigned_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
    elif agent_type == "worker":
        _worker_ongoing_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_stats_dir(agent_name).mkdir(parents=True, exist_ok=True)
    elif agent_type == "recycler":
        recycler_dir = cfg.AGENTS_DIR / agent_name
        (recycler_dir / "solved").mkdir(parents=True, exist_ok=True)
        (recycler_dir / "unsolved").mkdir(parents=True, exist_ok=True)
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
    elif agent_type == "boss":
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_stats_dir(agent_name).mkdir(parents=True, exist_ok=True)
    
    # 初始化 memory.md（如果不存在）
    memory_file = _worker_memory_file(agent_name)
    if not memory_file.exists():
        agent_dir = _worker_dir(agent_name)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        extra_lines = ""
        if agent_type == "worker":
            extra_lines = (
                f"- 任务目录: `{_worker_tasks_dir(agent_name)}`\n"
                f"- 执行目录: `{_worker_ongoing_dir(agent_name)}`\n"
            )
        history_label = {
            "worker": "工作历史和状态",
            "secretary": "任务分配历史",
            "boss": "任务生成历史",
            "recycler": "报告审查历史",
        }.get(agent_type, "工作历史")
        memory_file.write_text(
            f"# {agent_name} 的工作总结\n\n"
            f"## 基本信息\n"
            f"- 工作目录: `{agent_dir}`\n"
            f"{extra_lines}"
            f"- 创建时间: {now_str}\n\n"
            f"## 工作总结\n\n"
            f"（此文件由系统自动维护，记录 {agent_name} 的{history_label}）\n",
            encoding="utf-8"
        )

    return info


def register_worker(worker_name: str, description: str = "") -> dict:
    """
    向后兼容：注册worker（默认类型为worker）
    """
    return register_agent(worker_name, agent_type="worker", description=description)


def remove_worker(worker_name: str) -> bool:
    """
    删除一个 agent。删除注册信息和专属目录。
    返回是否成功。
    """
    reg = _load_registry()
    if worker_name not in reg["workers"]:
        return False

    del reg["workers"][worker_name]
    _save_registry(reg)

    # 删除专属目录
    wd = _worker_dir(worker_name)
    if wd.exists():
        shutil.rmtree(str(wd), ignore_errors=True)

    return True


def list_workers() -> list[dict]:
    """列出所有已注册的 agent"""
    reg = _load_registry()
    workers = []
    for name, info in sorted(reg["workers"].items()):
        # 补充实时信息
        info = dict(info)  # copy
        td = _worker_tasks_dir(name)
        od = _worker_ongoing_dir(name)
        info["pending_count"] = len(list(td.glob("*.md"))) if td.exists() else 0
        info["ongoing_count"] = len(list(od.glob("*.md"))) if od.exists() else 0
        workers.append(info)
    return workers


def get_worker(worker_name: str) -> dict | None:
    """获取指定 agent 的信息"""
    reg = _load_registry()
    if worker_name not in reg["workers"]:
        return None
    info = dict(reg["workers"][worker_name])
    td = _worker_tasks_dir(worker_name)
    od = _worker_ongoing_dir(worker_name)
    info["pending_count"] = len(list(td.glob("*.md"))) if td.exists() else 0
    info["ongoing_count"] = len(list(od.glob("*.md"))) if od.exists() else 0
    return info


def update_worker_status(worker_name: str, status: str, pid: int | None = None):
    """更新 agent 的运行状态"""
    reg = _load_registry()
    if worker_name in reg["workers"]:
        reg["workers"][worker_name]["status"] = status
        # 如果 pid 是 None，清除 pid 字段；否则更新 pid
        if pid is None:
            reg["workers"][worker_name]["pid"] = None
        else:
            reg["workers"][worker_name]["pid"] = pid
        _save_registry(reg)


def set_agent_executing(agent_name: str, executing: bool):
    """设置 agent 的执行状态（是否正在处理任务）"""
    reg = _load_registry()
    if agent_name in reg["workers"]:
        reg["workers"][agent_name]["executing"] = executing
        _save_registry(reg)


def increment_completed_tasks(agent_name: str):
    """增加 agent 的已完成任务计数（每次触发时调用）"""
    reg = _load_registry()
    if agent_name in reg["workers"]:
        reg["workers"][agent_name]["completed_tasks"] = reg["workers"][agent_name].get("completed_tasks", 0) + 1
        _save_registry(reg)


def record_task_completion(worker_name: str, task_name: str):
    """记录 agent 完成了一个任务，并更新 worker 的 memory.md（保留用于向后兼容）"""
    reg = _load_registry()
    if worker_name not in reg["workers"]:
        return
    w = reg["workers"][worker_name]
    recent = w.get("recent_tasks", [])
    recent.append(task_name)
    w["recent_tasks"] = recent[-20:]  # 只保留最近 20 条
    _save_registry(reg)
    
    # 更新 worker 的 memory.md
    _update_worker_memory(worker_name, task_name)


def _update_worker_memory(worker_name: str, task_name: str):
    """更新 worker 的 memory.md，记录完成的任务"""
    memory_file = _worker_memory_file(worker_name)

    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8")
    else:
        worker_dir = _worker_dir(worker_name)
        content = (
            f"# {worker_name} 的工作总结\n\n"
            f"## 基本信息\n"
            f"- 工作目录: `{worker_dir}`\n"
            f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 工作总结\n\n"
        )

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_entry = f"\n### [{timestamp}] 完成任务: {task_name}\n"

    if "## 工作总结" in content:
        parts = content.split("## 工作总结", 1)
        header = parts[0] + "## 工作总结"
        rest = parts[1].lstrip() if len(parts) == 2 else ""
        if rest.startswith("（此文件由系统自动维护"):
            rest = ""
        content = header + "\n\n" + new_entry + rest
    else:
        content += "\n## 工作总结\n\n" + new_entry + "\n"

    memory_file.write_text(content, encoding="utf-8")


def get_worker_names() -> set[str]:
    """获取所有已注册 agent 名"""
    reg = _load_registry()
    return set(reg["workers"].keys())


def get_all_running_pids() -> list[tuple[str, int]]:
    """获取所有运行中的agent进程PID列表，返回[(agent_name, pid), ...]"""
    reg = _load_registry()
    running = []
    for name, info in reg["workers"].items():
        pid = info.get("pid")
        if pid:
            running.append((name, pid))
    return running


def stop_all_agents():
    """停止所有运行中的agent进程（用于退出kai时清理）"""
    import os
    import signal
    import sys as _sys

    running = get_all_running_pids()
    if not running:
        return

    print("\n🛑 停止所有运行中的agent进程...")
    for name, pid in running:
        try:
            os.kill(pid, 0)  # 检查进程是否存在
        except (OSError, ProcessLookupError):
            update_worker_status(name, "idle", pid=None)
            continue

        print(f"   停止 {name} (PID={pid})...")
        try:
            if _sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        update_worker_status(name, "idle", pid=None)
    print("✅ 所有agent进程已停止")


def pick_random_name() -> str:
    """
    从预设名字池中随机抽取一个尚未被使用的名字。
    如果名字池用完了，则自动生成带编号的名字。
    """
    used = get_worker_names()
    available = [n for n in PRESET_NAMES if n not in used]
    if available:
        return random.choice(available)
    # 名字池用完了，用编号
    i = len(used) + 1
    while f"worker-{i}" in used:
        i += 1
    return f"worker-{i}"


def pick_available_name(preferred_names: list[str] | None = None) -> str:
    """
    智能选择可用名字，优先使用preferred_names，如果都被占用则从预设池中选择。
    确保不会给同一个名字注册两个职业。
    
    Args:
        preferred_names: 优先使用的名字列表（按优先级排序）
    
    Returns:
        可用的名字
    """
    used = get_worker_names()
    
    # 如果有优先名字列表，先检查它们
    if preferred_names:
        for name in preferred_names:
            if name not in used:
                return name
    
    # 从预设池中选择
    available = [n for n in PRESET_NAMES if n not in used]
    if available:
        return random.choice(available)
    
    # 名字池用完了，用编号
    i = len(used) + 1
    while f"agent-{i}" in used:
        i += 1
    return f"agent-{i}"


def build_workers_summary() -> str:
    """
    构建 worker 信息摘要 (供秘书 Agent 提示词使用)。
    只包含 worker 类型的 agent，不包括 secretary、boss、recycler 等其他类型。
    包含每个 worker 的名字、目录、擅长方向、已完成任务等。
    同时读取每个 worker 的 memory.md 文件内容。
    """
    workers = list_workers()
    if not workers:
        return ""

    lines = []
    for w in workers:
        # 只处理 worker 类型的 agent
        agent_type = w.get("type", "worker")
        if agent_type != "worker":
            continue
            
        name = w["name"]
        tasks_dir = _worker_tasks_dir(name)
        desc = w.get("description", "") or "通用工人"
        recent = w.get("recent_tasks", [])
        recent_str = ", ".join(recent[-5:]) if recent else "暂无"
        completed = w.get("completed_tasks", 0)
        pending = w.get("pending_count", 0)
        ongoing = w.get("ongoing_count", 0)

        # 读取 worker 的 memory.md 内容
        memory_file = _worker_memory_file(name)
        worker_memory = ""
        if memory_file.exists():
            try:
                worker_memory = memory_file.read_text(encoding="utf-8").strip()
            except Exception:
                worker_memory = "(无法读取工作总结)"

        lines.append(
            f"### 工人: {name}\n"
            f"- **描述**: {desc}\n"
            f"- **任务目录**: `{tasks_dir}`\n"
            f"- **状态**: 已完成 {completed} 个任务 | 待处理 {pending} 个 | 执行中 {ongoing} 个\n"
            f"- **最近完成**: {recent_str}\n"
            f"\n#### {name} 的工作总结\n"
            f"{worker_memory}\n"
        )

    return "\n".join(lines)


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


def load_agent_memory(agent_name: str) -> str:
    """
    加载agent的memory内容（通用函数，适用于所有agent类型）
    
    Returns:
        memory内容，如果不存在或为空则返回空字符串
    """
    memory_file = _worker_memory_file(agent_name)
    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8").strip()
        # 如果内容太长，只返回最近的部分
        lines = content.splitlines()
        if len(lines) > 200:
            header = lines[:5]  # 保留前5行（标题和基本信息）
            recent = lines[-195:]  # 保留最近195行
            content = "\n".join(header + ["", "...(更早的记录已省略)...", ""] + recent)
        return content
    return ""


def update_agent_memory(agent_name: str, summary: str, task_name: str | None = None):
    """
    更新agent的memory文件（通用函数，适用于所有agent类型）
    
    Args:
        agent_name: agent名称
        summary: 本次工作的简要总结
        task_name: 任务名称（可选，用于记录）
    """
    memory_file = _worker_memory_file(agent_name)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 读取现有内容或创建新文件
    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8")
    else:
        # 创建基础结构
        agent_dir = _worker_dir(agent_name)
        content = (
            f"# {agent_name} 的工作总结\n\n"
            f"## 基本信息\n"
            f"- 工作目录: `{agent_dir}`\n"
            f"- 创建时间: {timestamp}\n\n"
            f"## 工作总结\n\n"
        )
    
    # 在"工作总结"部分追加新条目
    if task_name:
        new_entry = f"\n### [{timestamp}] 完成任务: {task_name}\n{summary}\n"
    else:
        new_entry = f"\n### [{timestamp}] 工作记录\n{summary}\n"
    
    # 查找"工作总结"部分的位置并插入新条目
    if "## 工作总结" in content:
        # 在"工作总结"标题后插入新条目（最新任务在最前面）
        parts = content.split("## 工作总结", 1)
        if len(parts) == 2:
            header = parts[0] + "## 工作总结"
            summary_section = parts[1].lstrip()
            # 移除末尾的提示文字（如果存在）
            if summary_section.startswith("（此文件由系统自动维护"):
                summary_section = ""
            content = header + "\n\n" + new_entry + (summary_section if summary_section else "")
        else:
            content += new_entry + "\n"
    else:
        # 如果没有"工作总结"部分，添加
        content += "\n## 工作总结\n\n" + new_entry + "\n"
    
    memory_file.write_text(content, encoding="utf-8")


# ============================================================
#  CRUD
# ============================================================

def register_agent(agent_name: str, agent_type: str = "worker", description: str = "") -> dict:
    """
    注册一个新 agent（统一接口，支持类型）。
    创建专属目录 {name}/tasks 和 {name}/ongoing。
    返回 agent 信息字典。
    """
    reg = _load_registry()

    if agent_name in reg["workers"]:
        # 已存在，更新信息（确保type和description被更新）
        updated = False
        if description and reg["workers"][agent_name].get("description") != description:
            reg["workers"][agent_name]["description"] = description
            updated = True
        if agent_type and reg["workers"][agent_name].get("type") != agent_type:
            reg["workers"][agent_name]["type"] = agent_type
            updated = True
        if updated:
            _save_registry(reg)
        return reg["workers"][agent_name]

    info = {
        "name": agent_name,
        "type": agent_type,      # secretary / worker / boss / recycler
        "description": description,
        "hired_at": datetime.now().isoformat(),
        "completed_tasks": 0,
        "recent_tasks": [],      # 最近完成的任务名列表 (最多保留 20 条)
        "specialties": [],       # 擅长方向 (由秘书历史推断)
        "status": "idle",        # idle / busy / offline
        "pid": None,             # 运行时填入 scanner 的 PID
        "executing": False,      # 是否正在执行任务（process_fn 被触发）
    }
    reg["workers"][agent_name] = info
    _save_registry(reg)

    # 按 agent 类型只创建该类型需要的目录
    _worker_tasks_dir(agent_name).mkdir(parents=True, exist_ok=True)
    _worker_logs_dir(agent_name).mkdir(parents=True, exist_ok=True)
    if agent_type == "secretary":
        _worker_assigned_dir(agent_name).mkdir(parents=True, exist_ok=True)
    elif agent_type == "worker":
        _worker_ongoing_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_stats_dir(agent_name).mkdir(parents=True, exist_ok=True)
    elif agent_type == "recycler":
        recycler_dir = cfg.AGENTS_DIR / agent_name
        (recycler_dir / "solved").mkdir(parents=True, exist_ok=True)
        (recycler_dir / "unsolved").mkdir(parents=True, exist_ok=True)
    elif agent_type == "boss":
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_stats_dir(agent_name).mkdir(parents=True, exist_ok=True)
    
    # 初始化 memory.md（如果不存在，为所有agent类型创建）
    memory_file = _worker_memory_file(agent_name)
    if not memory_file.exists():
        agent_dir = _worker_dir(agent_name)
        if agent_type == "worker":
            tasks_dir = _worker_tasks_dir(agent_name)
            ongoing_dir = _worker_ongoing_dir(agent_name)
            memory_file.write_text(
                f"# {agent_name} 的工作总结\n\n"
                f"## 基本信息\n"
                f"- 工作目录: `{agent_dir}`\n"
                f"- 任务目录: `{tasks_dir}`\n"
                f"- 执行目录: `{ongoing_dir}`\n"
                f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"- **统计**: 已完成 0 个任务 | 待处理 0 个 | 执行中 0 个\n\n"
                f"## 工作总结\n\n"
                f"（此文件由系统自动维护，记录 {agent_name} 的工作历史和状态）\n",
                encoding="utf-8"
            )
        elif agent_type == "secretary":
            memory_file.write_text(
                f"# {agent_name} 的工作总结\n\n"
                f"## 基本信息\n"
                f"- 工作目录: `{agent_dir}`\n"
                f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"## 工作总结\n\n"
                f"（此文件由系统自动维护，记录 {agent_name} 的任务分配历史）\n",
                encoding="utf-8"
            )
        elif agent_type == "boss":
            memory_file.write_text(
                f"# {agent_name} 的工作总结\n\n"
                f"## 基本信息\n"
                f"- 工作目录: `{agent_dir}`\n"
                f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"## 工作总结\n\n"
                f"（此文件由系统自动维护，记录 {agent_name} 的任务生成历史）\n",
                encoding="utf-8"
            )
        elif agent_type == "recycler":
            memory_file.write_text(
                f"# {agent_name} 的工作总结\n\n"
                f"## 基本信息\n"
                f"- 工作目录: `{agent_dir}`\n"
                f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"## 工作总结\n\n"
                f"（此文件由系统自动维护，记录 {agent_name} 的报告审查历史）\n",
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
    
    # 读取现有内容或创建新文件
    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8")
        # 如果标题或基本信息中的 worker_name 不正确，更新它们
        worker_dir = _worker_dir(worker_name)
        tasks_dir = _worker_tasks_dir(worker_name)
        ongoing_dir = _worker_ongoing_dir(worker_name)
        
        # 更新标题（如果还是旧的）
        import re
        if content.startswith("# ") and worker_name not in content.split("\n")[0]:
            # 替换标题
            content = re.sub(r"^# .* 的工作总结", f"# {worker_name} 的工作总结", content)
        
        # 更新基本信息中的路径（如果路径不正确）
        if f"`{worker_dir}`" not in content or f"`{tasks_dir}`" not in content:
            # 更新工作目录（使用 lambda 避免 Windows 路径中的反斜杠被解释为转义序列）
            content = re.sub(r"- 工作目录: `.*?`", lambda m: f"- 工作目录: `{worker_dir}`", content)
            # 更新任务目录
            content = re.sub(r"- 任务目录: `.*?`", lambda m: f"- 任务目录: `{tasks_dir}`", content)
            # 更新执行目录
            content = re.sub(r"- 执行目录: `.*?`", lambda m: f"- 执行目录: `{ongoing_dir}`", content)
    else:
        # 如果不存在，创建基础结构
        worker_dir = _worker_dir(worker_name)
        tasks_dir = _worker_tasks_dir(worker_name)
        ongoing_dir = _worker_ongoing_dir(worker_name)
        content = (
            f"# {worker_name} 的工作总结\n\n"
            f"## 基本信息\n"
            f"- 工作目录: `{worker_dir}`\n"
            f"- 任务目录: `{tasks_dir}`\n"
            f"- 执行目录: `{ongoing_dir}`\n"
            f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 工作总结\n\n"
        )
    
    # 更新统计信息（从注册表读取最新数据）
    reg = _load_registry()
    completed = 0
    pending = 0
    ongoing = 0
    if worker_name in reg["workers"]:
        w = reg["workers"][worker_name]
        completed = w.get("completed_tasks", 0)
        # 实时统计
        pending = len(list(_worker_tasks_dir(worker_name).glob("*.md"))) if _worker_tasks_dir(worker_name).exists() else 0
        ongoing = len(list(_worker_ongoing_dir(worker_name).glob("*.md"))) if _worker_ongoing_dir(worker_name).exists() else 0
    
    # 更新基本信息部分的统计
    import re
    stats_line = f"- **统计**: 已完成 {completed} 个任务 | 待处理 {pending} 个 | 执行中 {ongoing} 个"
    if "- **统计**:" in content:
        content = re.sub(r"- \*\*统计\*\*:.*", stats_line, content)
    elif "## 基本信息" in content:
        # 在基本信息部分末尾添加统计
        content = re.sub(
            r"(## 基本信息\n.*?)(\n\n## )",
            r"\1\n" + stats_line + r"\2",
            content,
            flags=re.DOTALL
        )
    
    # 在"工作总结"部分追加新完成的任务
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new_entry = f"\n### [{timestamp}] 完成任务: {task_name}\n"
    
    # 查找"工作总结"部分的位置并插入新条目
    if "## 工作总结" in content:
        # 在"工作总结"标题后插入新条目（最新任务在最前面）
        parts = content.split("## 工作总结", 1)
        if len(parts) == 2:
            header = parts[0] + "## 工作总结"
            summary = parts[1].lstrip()
            # 移除末尾的提示文字（如果存在）
            if summary.startswith("（此文件由系统自动维护"):
                summary = ""
            content = header + "\n\n" + new_entry + (summary if summary else "")
        else:
            content += new_entry + "\n"
    else:
        # 如果没有"工作总结"部分，添加
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
    from secretary.cli import _stop_process, _check_process_exists
    running = get_all_running_pids()
    if not running:
        return
    
    print(f"\n🛑 停止所有运行中的agent进程...")
    for name, pid in running:
        if _check_process_exists(pid):
            print(f"   停止 {name} (PID={pid})...")
            _stop_process(pid, name)
            update_worker_status(name, "idle", pid=None)
    print(f"✅ 所有agent进程已停止")


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


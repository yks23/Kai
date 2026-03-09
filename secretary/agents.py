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
)




# ============================================================
#  CRUD
# ============================================================

def register_agent(agent_name: str, agent_type: str = "worker", description: str = "", known_agents: list[str] | None = None, skills: list[str] | None = None) -> dict:
    """
    注册一个新 agent（统一接口，支持类型）。
    创建专属目录 {name}/tasks 和 {name}/ongoing。
    返回 agent 信息字典。
    
    Args:
        agent_name: Agent 名称
        agent_type: Agent 类型 (worker/secretary/boss/recycler 或自定义类型)
        description: Agent 描述
        known_agents: 初始 known_agents 列表（如果为 None，默认为空列表）
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
        # 如果提供了 known_agents，也更新（合并，去重）
        if known_agents is not None:
            existing_ka = set(reg["workers"][agent_name].get("known_agents", []))
            new_ka = set(known_agents)
            if existing_ka != new_ka:
                reg["workers"][agent_name]["known_agents"] = list(new_ka)
                updated = True
        # 如果提供了 skills，也更新
        if skills is not None:
            existing_skills = set(reg["workers"][agent_name].get("skills", []))
            new_skills = set(skills)
            if existing_skills != new_skills:
                reg["workers"][agent_name]["skills"] = list(new_skills)
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
        "known_agents": list(known_agents) if known_agents is not None else [],  # 显式列表；空 = 不认识任何人，需手动 link
        "skills": list(skills) if skills is not None else [],  # agent 拥有的技能列表（如 ["command_system"]）
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
        _worker_tasks_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_reports_dir(agent_name).mkdir(parents=True, exist_ok=True)
        _worker_stats_dir(agent_name).mkdir(parents=True, exist_ok=True)

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
        info["pending_count"] = len([f for f in td.iterdir() if f.is_file()]) if td.exists() else 0
        info["ongoing_count"] = len([f for f in od.iterdir() if f.is_file()]) if od.exists() else 0
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
    info["pending_count"] = len([f for f in td.iterdir() if f.is_file()]) if td.exists() else 0
    info["ongoing_count"] = len([f for f in od.iterdir() if f.is_file()]) if od.exists() else 0
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
    """记录 agent 完成了一个任务"""
    reg = _load_registry()
    if worker_name not in reg["workers"]:
        return
    w = reg["workers"][worker_name]
    recent = w.get("recent_tasks", [])
    recent.append(task_name)
    w["recent_tasks"] = recent[-20:]  # 只保留最近 20 条
    _save_registry(reg)


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


def save_agent_session_id(agent_name: str, session_id: str) -> None:
    """将 agent 最新的 session_id 持久化到 agents/{name}/session.id"""
    if not session_id:
        return
    session_file = cfg.AGENTS_DIR / agent_name / "session.id"
    try:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(session_id, encoding="utf-8")
    except Exception:
        pass


def load_agent_session_id(agent_name: str) -> str:
    """读取 agent 持久化的 session_id，不存在时返回空字符串"""
    session_file = cfg.AGENTS_DIR / agent_name / "session.id"
    if session_file.exists():
        try:
            return session_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


# ============================================================
#  Known-agent graph API
# ============================================================

def get_agent_known_agents(agent_name: str) -> list | None:
    """
    返回 agent 的显式 known_agents 列表。
    新建 agent 默认为 []（不认识任何人）。
    仅旧数据可能返回 None（向后兼容，表示认识所有人）。
    """
    reg = _load_registry()
    info = reg["workers"].get(agent_name, {})
    return info.get("known_agents", None)


def _known_agents_sent_file(agent_name: str) -> Path:
    return cfg.AGENTS_DIR / agent_name / "known_agents_sent.json"


def save_known_agents_snapshot(agent_name: str, known: list | None) -> None:
    """记录本次发送给 agent 的 known_agents 列表（用于下次比较）"""
    import json as _json
    fp = _known_agents_sent_file(agent_name)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(_json.dumps(sorted(known) if known else []), encoding="utf-8")


def known_agents_changed(agent_name: str) -> bool:
    """当前 known_agents 是否与上次发送时不同（需要重新发送）"""
    import json as _json
    fp = _known_agents_sent_file(agent_name)
    current = get_agent_known_agents(agent_name)
    current_sorted = sorted(current) if current else []
    if not fp.exists():
        return True  # 从未发送过
    try:
        last = _json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return True
    return current_sorted != last


def set_agent_known_agents(agent_name: str, known: list):
    """替换 agent 的 known_agents 列表（空列表 = 不认识任何人）。"""
    reg = _load_registry()
    if agent_name in reg["workers"]:
        reg["workers"][agent_name]["known_agents"] = list(known)
        _save_registry(reg)


def add_known_agent_link(from_agent: str, to_agent: str):
    """在 from_agent → to_agent 之间添加链接。"""
    reg = _load_registry()
    if from_agent not in reg["workers"] or to_agent not in reg["workers"]:
        return
    current = reg["workers"][from_agent].get("known_agents", None)
    if current is None:
        # 从"全认识"转换为显式列表，再加上 to_agent
        all_names = [n for n in reg["workers"] if n != from_agent]
        current = all_names
    if to_agent not in current:
        current.append(to_agent)
    reg["workers"][from_agent]["known_agents"] = current
    _save_registry(reg)


def remove_known_agent_link(from_agent: str, to_agent: str):
    """删除 from_agent → to_agent 的链接。"""
    reg = _load_registry()
    if from_agent not in reg["workers"]:
        return
    current = reg["workers"][from_agent].get("known_agents", None)
    if current is None:
        # 从"全认识"转换为显式列表，排除 to_agent
        all_names = [n for n in reg["workers"] if n != from_agent and n != to_agent]
        current = all_names
    else:
        current = [n for n in current if n != to_agent]
    reg["workers"][from_agent]["known_agents"] = current
    _save_registry(reg)


def get_graph_data() -> dict:
    """返回用于前端图谱渲染的节点和有向边数据。

    规则:
    - 所有已注册的 agent 都作为节点，不管有没有链接
    - 只有 known_agents 字段为显式列表时才生成有向边
    - known_agents=null（隐式认识所有人）不绘制边，避免全连接图混乱；
      前端在节点面板中用文字说明即可
    """
    agents = list_workers()
    all_names = {a["name"] for a in agents}
    nodes = []
    edges = []
    seen_edges: set[tuple[str, str]] = set()
    for a in agents:
        name = a["name"]
        pid = a.get("pid")
        is_running = bool(pid and _pid_alive_local(pid))
        nodes.append({
            "name": name,
            "type": a.get("type", "worker"),
            "description": a.get("description", ""),
            "is_running": is_running,
            "executing": bool(a.get("executing")),
            "completed_tasks": a.get("completed_tasks", 0),
            "pending_count": a.get("pending_count", 0),
        })
        known = a.get("known_agents", None)
        # Only draw explicit edges (known_agents is a list, not null)
        if known is not None:
            for t in known:
                if t in all_names and t != name and (name, t) not in seen_edges:
                    edges.append({"from": name, "to": t})
                    seen_edges.add((name, t))
    return {"nodes": nodes, "edges": edges}


def _pid_alive_local(pid: int) -> bool:
    try:
        import os as _os
        _os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def build_known_agents_section(current_agent_name: str = "") -> str:
    """
    构建已知 agent 列表（供注入到提示词中）。
    若 agent 设置了显式 known_agents 列表则只列出其中的 agent，
    否则列出所有其他 agent（向后兼容）。
    每个 agent 可视为一个工具：向其 tasks/ 目录写入任务文件即可调用。
    """
    all_agents = list_workers()
    if not all_agents:
        return ""

    # 确定本 agent 可见的 known agents
    if current_agent_name:
        reg = _load_registry()
        explicit = reg["workers"].get(current_agent_name, {}).get("known_agents", None)
        if explicit is not None:
            visible_names = set(explicit)
            agents = [a for a in all_agents if a.get("name") in visible_names]
        else:
            agents = [a for a in all_agents if a.get("name") != current_agent_name]
    else:
        agents = [a for a in all_agents if a.get("name") != current_agent_name]

    if not agents:
        return ""

    _type_desc = {
        "secretary": "任务分类与分配",
        "boss": "监控 worker 并持续生成任务",
        "recycler": "审查任务完成报告",
        "worker": "执行编程任务",
    }

    lines = ["## 已知 Agent（可调用的工具）", "向对应 agent 的 tasks/ 目录写入任务文件即可调用。", ""]
    found = False
    for a in agents:
        name = a.get("name", "")
        agent_type = a.get("type", "worker")
        desc = a.get("description", "") or _type_desc.get(agent_type, "通用 agent")
        tasks_dir = _worker_tasks_dir(name)
        lines.append(f"### {name} ({agent_type})")
        lines.append(f"- **描述**: {desc}")
        lines.append(f"- **任务目录**: `{tasks_dir}`")
        if agent_type == "worker":
            completed = a.get("completed_tasks", 0)
            pending   = a.get("pending_count", 0)
            ongoing   = a.get("ongoing_count", 0)
            recent    = a.get("recent_tasks", [])
            recent_str = "、".join(recent[-3:]) if recent else "暂无"
            lines.append(
                f"- **状态**: 已完成 {completed} | 待处理 {pending} | 执行中 {ongoing}"
                + (f"  最近: {recent_str}" if recent else "")
            )
        lines.append("")
        found = True

    return "\n".join(lines) if found else ""


def build_skills_section(agent_name: str, base_dir: Path) -> str:
    """
    构建技能部分（供注入到提示词中）。
    如果 agent 有技能，则加载对应的技能文件内容。
    """
    from pathlib import Path as PathLib
    reg = _load_registry()
    agent_info = reg["workers"].get(agent_name, {})
    skills = agent_info.get("skills", [])
    
    if not skills:
        return ""
    
    lines = []
    for skill_name in skills:
        # 先尝试从 BASE_DIR/agent_skills 加载，再尝试从包内加载
        skill_file = base_dir / "agent_skills" / f"{skill_name}.md"
        if not skill_file.exists():
            # 尝试从包内加载
            pkg_skill_file = PathLib(__file__).parent / "agent_skills" / f"{skill_name}.md"
            if pkg_skill_file.exists():
                skill_file = pkg_skill_file
        
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8")
            # 提取技能内容（跳过标题和描述，从"## 技能说明"开始）
            skill_lines = []
            in_skill_section = False
            for line in content.splitlines():
                if line.strip().startswith("## 技能说明"):
                    in_skill_section = True
                    continue
                if in_skill_section:
                    # 替换模板变量
                    line = line.replace("{commands_dir}", str(base_dir / "commands"))
                    line = line.replace("{base_dir}", str(base_dir))
                    line = line.replace("{skills_dir}", str(base_dir / "skills"))
                    skill_lines.append(line)
            
            if skill_lines:
                if not lines:  # 只在第一次添加标题
                    lines.append("## 技能")
                lines.extend(skill_lines)
                lines.append("")
    
    return "\n".join(lines) if lines else ""


def build_workers_summary() -> str:
    """
    构建 worker 信息摘要 (供秘书 Agent 提示词使用)。
    只包含 worker 类型的 agent，不包括 secretary、boss、recycler 等其他类型。
    包含每个 worker 的名字、目录、擅长方向、已完成任务等。
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

        lines.append(
            f"### 工人: {name}\n"
            f"- **描述**: {desc}\n"
            f"- **任务目录**: `{tasks_dir}`\n"
            f"- **状态**: 已完成 {completed} 个任务 | 待处理 {pending} 个 | 执行中 {ongoing} 个\n"
            f"- **最近完成**: {recent_str}\n"
        )

    return "\n".join(lines)


# ============================================================
#  Custom Agent Types — 用户自定义类型 (持久化到 agents.json)
# ============================================================

_BUILTIN_TYPES = {"worker", "secretary", "boss", "recycler"}


def list_custom_types() -> list[dict]:
    """列出所有自定义 agent 类型"""
    reg = _load_registry()
    return list(reg.get("custom_types", {}).values())


def get_custom_type(type_name: str) -> dict | None:
    """获取自定义 agent 类型"""
    reg = _load_registry()
    return reg.get("custom_types", {}).get(type_name)


def register_custom_type(
    type_name: str,
    base_type: str,
    first_prompt: str,
    continue_prompt: str,
    description: str = "",
) -> dict:
    """
    注册一个自定义 agent 类型。

    Args:
        type_name: 类型名称（不能与内置类型重名）
        base_type: 基类类型，决定触发规则和目录结构 (worker/secretary/boss/recycler)
        first_prompt: 首轮提示词内容（完整 markdown）
        continue_prompt: 续轮提示词内容（简短 markdown）
        description: 类型描述

    Returns:
        类型信息字典
    """
    if type_name in _BUILTIN_TYPES:
        raise ValueError(f"不能覆盖内置类型: {type_name}")
    if base_type not in _BUILTIN_TYPES:
        raise ValueError(f"base_type 必须是 {_BUILTIN_TYPES} 之一")

    reg = _load_registry()
    if "custom_types" not in reg:
        reg["custom_types"] = {}

    info = {
        "name": type_name,
        "base_type": base_type,
        "description": description,
        "first_prompt": first_prompt,
        "continue_prompt": continue_prompt,
        "created_at": datetime.now().isoformat(),
    }
    reg["custom_types"][type_name] = info
    _save_registry(reg)
    return info


def delete_custom_type(type_name: str) -> bool:
    """删除自定义 agent 类型，返回是否成功"""
    reg = _load_registry()
    ct = reg.get("custom_types", {})
    if type_name not in ct:
        return False
    del ct[type_name]
    _save_registry(reg)
    return True

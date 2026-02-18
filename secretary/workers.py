"""
工人 (Worker) 注册表管理

每个工人有自己的名字、专属文件夹 ({BASE_DIR}/{name}/tasks 和 {name}/ongoing)，
但报告统一提交到 {BASE_DIR}/report/。

注册表存储在 {BASE_DIR}/workers.json，记录:
  - 工人名字
  - 招募时间
  - 擅长方向 (由秘书历史分配推断)
  - 已完成任务数
  - 最近完成的任务列表

秘书 Agent 在分配任务时会读取工人信息，决定分配给谁。

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
    # 英文名
    "alice", "bob", "charlie", "diana", "eve",
    "frank", "grace", "henry", "iris", "jack",
    "kate", "leo", "mia", "noah", "olive",
    "paul", "quinn", "ruby", "sam", "tina",
    # 有趣的代号
    "panda", "phoenix", "ninja", "rocket", "spark",
    "pixel", "byte", "nova", "echo", "flux",
]


def _workers_file() -> Path:
    return cfg.BASE_DIR / "workers.json"


def _load_registry() -> dict:
    """加载工人注册表"""
    wf = _workers_file()
    if wf.exists():
        try:
            return json.loads(wf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"workers": {}}
    return {"workers": {}}


def _save_registry(registry: dict):
    """保存工人注册表"""
    wf = _workers_file()
    wf.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _worker_dir(worker_name: str) -> Path:
    return cfg.WORKERS_DIR / worker_name


def _worker_tasks_dir(worker_name: str) -> Path:
    return _worker_dir(worker_name) / "tasks"


def _worker_ongoing_dir(worker_name: str) -> Path:
    return _worker_dir(worker_name) / "ongoing"


# ============================================================
#  CRUD
# ============================================================

def register_worker(worker_name: str, description: str = "") -> dict:
    """
    注册一个新工人。创建专属目录 {name}/tasks 和 {name}/ongoing。
    返回工人信息字典。
    """
    reg = _load_registry()

    if worker_name in reg["workers"]:
        # 已存在，只更新描述 (如果有)
        if description:
            reg["workers"][worker_name]["description"] = description
        _save_registry(reg)
        return reg["workers"][worker_name]

    info = {
        "name": worker_name,
        "description": description,
        "hired_at": datetime.now().isoformat(),
        "completed_tasks": 0,
        "recent_tasks": [],      # 最近完成的任务名列表 (最多保留 20 条)
        "specialties": [],       # 擅长方向 (由秘书历史推断)
        "status": "idle",        # idle / busy / offline
        "pid": None,             # 运行时填入 scanner 的 PID
    }
    reg["workers"][worker_name] = info
    _save_registry(reg)

    # 创建专属目录
    _worker_tasks_dir(worker_name).mkdir(parents=True, exist_ok=True)
    _worker_ongoing_dir(worker_name).mkdir(parents=True, exist_ok=True)

    return info


def remove_worker(worker_name: str) -> bool:
    """
    删除一个工人。删除注册信息和专属目录。
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
    """列出所有已注册的工人"""
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
    """获取指定工人的信息"""
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
    """更新工人的运行状态"""
    reg = _load_registry()
    if worker_name in reg["workers"]:
        reg["workers"][worker_name]["status"] = status
        if pid is not None:
            reg["workers"][worker_name]["pid"] = pid
        _save_registry(reg)


def record_task_completion(worker_name: str, task_name: str):
    """记录工人完成了一个任务"""
    reg = _load_registry()
    if worker_name not in reg["workers"]:
        return
    w = reg["workers"][worker_name]
    w["completed_tasks"] = w.get("completed_tasks", 0) + 1
    recent = w.get("recent_tasks", [])
    recent.append(task_name)
    w["recent_tasks"] = recent[-20:]  # 只保留最近 20 条
    _save_registry(reg)


def get_worker_names() -> set[str]:
    """获取所有已注册工人名"""
    reg = _load_registry()
    return set(reg["workers"].keys())


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


def build_workers_summary() -> str:
    """
    构建工人信息摘要 (供秘书 Agent 提示词使用)。
    包含每个工人的名字、目录、擅长方向、已完成任务等。
    """
    workers = list_workers()
    if not workers:
        return ""

    lines = []
    for w in workers:
        name = w["name"]
        tasks_dir = _worker_tasks_dir(name)
        desc = w.get("description", "") or "通用工人"
        recent = w.get("recent_tasks", [])
        recent_str = ", ".join(recent[-5:]) if recent else "暂无"
        completed = w.get("completed_tasks", 0)
        pending = w.get("pending_count", 0)
        ongoing = w.get("ongoing_count", 0)

        lines.append(
            f"- **{name}**: {desc}\n"
            f"  - 任务目录: `{tasks_dir}`\n"
            f"  - 已完成: {completed} 个任务 | 待处理: {pending} 个 | 执行中: {ongoing} 个\n"
            f"  - 最近完成: {recent_str}"
        )

    return "\n".join(lines)


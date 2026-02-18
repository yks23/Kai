"""
Secretary Agent 系统配置

BASE_DIR (工作区) 优先级:
  1. CLI 参数 --workspace / -w        (最高)
  2. 环境变量 SECRETARY_WORKSPACE
  3. 持久化配置 kai base <path>
  4. 当前工作目录 CWD                   (最低)

PROMPTS_DIR (提示词模板):
  固定指向包内的 prompts/ 目录，随包分发。
"""
import os
from pathlib import Path

# ============ 包内路径 (不可配置) ============
_PACKAGE_DIR = Path(__file__).parent.resolve()
PROMPTS_DIR = _PACKAGE_DIR / "prompts"          # 提示词模板 (随包分发)

# ============ 工作区路径 (可配置) ============
def _resolve_base_dir() -> Path:
    """
    按优先级确定 BASE_DIR:
      env var > 持久化配置 > CWD
    (CLI --workspace 在 cli.py 中覆盖，优先级最高)
    """
    # 优先: 环境变量
    env_ws = os.environ.get("SECRETARY_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).resolve()

    # 其次: 持久化配置 (kai base <path>)
    try:
        from secretary.settings import get_base_dir
        saved = get_base_dir()
        if saved:
            return Path(saved).resolve()
    except Exception:
        pass

    # 兜底: CWD
    return Path.cwd().resolve()


BASE_DIR = _resolve_base_dir()

TASKS_DIR = BASE_DIR / "tasks"              # 待处理任务 (秘书agent写入)
ONGOING_DIR = BASE_DIR / "ongoing"          # 执行中任务 (scanner移入, worker完成后删除)
REPORT_DIR = BASE_DIR / "report"            # Worker 完成报告 (待回收者审查)
STATS_DIR = BASE_DIR / "stats"             # 调用统计 + 对话日志
LOGS_DIR = BASE_DIR / "logs"               # quiet 模式后台日志
SOLVED_DIR = BASE_DIR / "solved-report"     # 回收者确认完成的报告
UNSOLVED_DIR = BASE_DIR / "unsolved-report" # 回收者判定未完成的报告
SKILLS_DIR = BASE_DIR / "skills"            # 学会的技能 (可复用任务模板)
WORKERS_DIR = BASE_DIR / "workers"          # 工人目录 (所有工人放在这里)
WORKERS_FILE = BASE_DIR / "workers.json"    # 工人注册表

# ============ Cursor Agent 配置 ============
CURSOR_BIN = os.environ.get("CURSOR_BIN", "cursor")
DEFAULT_MODEL = os.environ.get("CURSOR_MODEL", "Auto")  # "Auto" = 由Cursor自动选择模型

# ============ 扫描器配置 ============
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "5"))       # 扫描tasks/间隔(秒)
WORKER_RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "3"))  # worker重试间隔(秒)
DEFAULT_MIN_TIME = int(os.environ.get("MIN_TIME", "0"))          # 默认最低执行时间(秒), 0=不限制
# 仅以下类型的任务会被 scanner 执行；monitor 等其它类型不进入执行流程
EXECUTABLE_TASK_TYPES = ("task", "hire", "recycle")

# ============ 回收者配置 ============
RECYCLER_INTERVAL = int(os.environ.get("RECYCLER_INTERVAL", "120"))  # 回收者扫描间隔(秒) = 2分钟

TESTCASES_DIR = BASE_DIR / "testcases"  # 测试样例文件夹
SECRETARY_MEMORY_FILE = BASE_DIR / "secretary_memory.md"  # 秘书Agent记忆文件

# ============ 执行范围 ============
# 仅以下命令在 quiet 模式下会后台执行（输出写入 logs/）；monitor、status、stop 等不后台执行。
EXECUTABLE_COMMANDS = frozenset({"task", "hire", "recycle"})

# ============ 内置技能 (预设指令，自动初始化到 skills/) ============
BUILTIN_SKILLS = {
    "evolving": {
        "description": "🔄 优化仓库",
        "prompt": (
            "考虑怎么优化这个仓库，从易用性，可移植性，额外功能等角度。"
            "调用完成之后，在终端再次执行 {cli_name} evolving，但不用等待其结束，可以立马终止。"
        ),
    },
    "analysis": {
        "description": "🔬 分析功能 + 生成测试样例",
        "prompt": (
            "分析这个仓库应该有的功能，并针对此在 {testcases_dir} 下编写测试样例，"
            "要求这些测试样例能够真正地反映仓库是否功能正常。"
        ),
    },
    "debug": {
        "description": "🐛 通过所有测试样例",
        "prompt": (
            "在 {testcases_dir} 下通过所有的测试样例，如果没有通过持续工作，"
            "直到这个文件夹下所有测试全部通过。全部通过之后终止。"
        ),
    },
}


def apply_base_dir(ws: Path):
    """运行时切换工作区 (由 CLI --workspace 或 kai base 调用)"""
    import secretary.config as _self
    _self.BASE_DIR = ws
    _self.TASKS_DIR = ws / "tasks"
    _self.ONGOING_DIR = ws / "ongoing"
    _self.REPORT_DIR = ws / "report"
    _self.STATS_DIR = ws / "stats"
    _self.SOLVED_DIR = ws / "solved-report"
    _self.UNSOLVED_DIR = ws / "unsolved-report"
    _self.TESTCASES_DIR = ws / "testcases"
    _self.LOGS_DIR = ws / "logs"
    _self.SKILLS_DIR = ws / "skills"
    _self.WORKERS_DIR = ws / "workers"
    _self.WORKERS_FILE = ws / "workers.json"
    _self.SECRETARY_MEMORY_FILE = ws / "secretary_memory.md"


def ensure_dirs():
    """确保所有运行时目录存在 (在 CLI 入口处调用)"""
    for d in [TASKS_DIR, ONGOING_DIR, REPORT_DIR, STATS_DIR,
              SOLVED_DIR, UNSOLVED_DIR, TESTCASES_DIR, LOGS_DIR, SKILLS_DIR, WORKERS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

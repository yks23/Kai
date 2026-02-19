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

# 注意: 不再使用根目录的 tasks/ 和 ongoing/
# 所有任务都分配到 agent 目录中 (agents/{name}/tasks 和 agents/{name}/ongoing)
# 默认 agent 名为 "sen"，当没有指定 agent 时使用

DEFAULT_WORKER_NAME = "sen"  # 默认 agent 名称（保持向后兼容）

REPORT_DIR = BASE_DIR / "report"            # 其他 agent 完成报告 (待回收者审查)
STATS_DIR = BASE_DIR / "stats"             # 调用统计 + 对话日志
LOGS_DIR = BASE_DIR / "logs"               # quiet 模式后台日志
SOLVED_DIR = BASE_DIR / "solved-report"     # 其他 agent 已解决报告（kai 的在 agents/kai/solved-report/）
UNSOLVED_DIR = BASE_DIR / "unsolved-report" # 其他 agent 未解决报告（kai 的在 agents/kai/unsolved-report/）
SKILLS_DIR = BASE_DIR / "skills"            # 学会的技能 (可复用任务模板)
AGENTS_DIR = BASE_DIR / "agents"            # Agent 目录 (所有 agent 放在这里)
AGENTS_FILE = AGENTS_DIR / "agents.json"    # Agent 注册表 (放在 agents/ 目录下)

# ============ Kai (秘书) 专用路径 ============
KAI_DIR = AGENTS_DIR / "kai"                # Kai 目录
KAI_TASKS_DIR = KAI_DIR / "tasks"           # Kai 待处理任务
KAI_ASSIGNED_DIR = KAI_DIR / "assigned"     # Kai 已分配任务（从 tasks/ 移动过来）
KAI_REPORTS_DIR = KAI_DIR / "reports"       # Kai 生成的报告
KAI_SOLVED_DIR = KAI_DIR / "solved-report"  # Kai 已解决报告
KAI_UNSOLVED_DIR = KAI_DIR / "unsolved-report"  # Kai 未解决报告
KAI_LOGS_DIR = KAI_DIR / "logs"            # Kai 日志目录
KAI_MEMORY_FILE = KAI_DIR / "memory.md"     # Kai 记忆文件
KAI_GOALS_FILE = KAI_DIR / "goals.md"       # Kai 目标文件

# 向后兼容：保留 WORKERS_DIR 和 WORKERS_FILE 作为别名
WORKERS_DIR = AGENTS_DIR
WORKERS_FILE = AGENTS_FILE

# ============ Agent 配置 ============
# 直接使用 agent 命令
# 在 Windows 上，通过 PowerShell 调用 agent（和用户在终端输入 agent 的行为一致）
# 在 Unix/Linux 上，使用 agent
import sys
if sys.platform == "win32":
    # 使用 PowerShell 调用 agent，这样可以确保和用户在终端输入 agent 的行为一致
    # PowerShell 会自动找到 agent.ps1 脚本
    CURSOR_BIN = "powershell"
    # 标记需要通过 PowerShell 调用
    CURSOR_BIN_IS_PS = True
else:
    CURSOR_BIN = "agent"
    CURSOR_BIN_IS_PS = False
# 模型设置：优先使用环境变量，然后是配置文件，最后是默认值
from secretary.settings import get_model
DEFAULT_MODEL = os.environ.get("CURSOR_MODEL") or get_model() or "Auto"  # "Auto" = 自动选择模型

# ============ 扫描器配置 ============
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "5"))       # 扫描tasks/间隔(秒)
WORKER_RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "3"))  # worker重试间隔(秒)
DEFAULT_MIN_TIME = int(os.environ.get("MIN_TIME", "0"))          # 默认最低执行时间(秒), 0=不限制
# 仅以下类型的任务会被 scanner 执行；monitor 等其它类型不进入执行流程
EXECUTABLE_TASK_TYPES = ("task", "hire", "recycle")

# ============ 回收者配置 ============
RECYCLER_INTERVAL = int(os.environ.get("RECYCLER_INTERVAL", "120"))  # 回收者扫描间隔(秒) = 2分钟

TESTCASES_DIR = BASE_DIR / "testcases"  # 测试样例文件夹
# 向后兼容：保留旧路径作为别名
SECRETARY_MEMORY_FILE = KAI_MEMORY_FILE  # 秘书Agent记忆文件（已移动到 agents/kai/memory.md）
SECRETARY_GOALS_FILE = KAI_GOALS_FILE    # 秘书全局目标（已移动到 agents/kai/goals.md）

# ============ 执行范围 ============
# 持续运行的命令（后台执行，输出到日志）
LONG_RUNNING_COMMANDS = frozenset({"start", "keep", "recycle", "monitor", "task"})

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
    _self.REPORT_DIR = ws / "report"
    _self.STATS_DIR = ws / "stats"
    _self.SOLVED_DIR = ws / "solved-report"
    _self.UNSOLVED_DIR = ws / "unsolved-report"
    _self.TESTCASES_DIR = ws / "testcases"
    _self.LOGS_DIR = ws / "logs"
    _self.SKILLS_DIR = ws / "skills"
    _self.AGENTS_DIR = ws / "agents"
    _self.AGENTS_FILE = _self.AGENTS_DIR / "agents.json"
    # 向后兼容
    _self.WORKERS_DIR = _self.AGENTS_DIR
    _self.WORKERS_FILE = _self.AGENTS_FILE
    # Kai 专用路径
    _self.KAI_DIR = _self.AGENTS_DIR / "kai"
    _self.KAI_TASKS_DIR = _self.KAI_DIR / "tasks"
    _self.KAI_ASSIGNED_DIR = _self.KAI_DIR / "assigned"
    _self.KAI_REPORTS_DIR = _self.KAI_DIR / "reports"
    _self.KAI_SOLVED_DIR = _self.KAI_DIR / "solved-report"
    _self.KAI_UNSOLVED_DIR = _self.KAI_DIR / "unsolved-report"
    _self.KAI_LOGS_DIR = _self.KAI_DIR / "logs"
    _self.KAI_MEMORY_FILE = _self.KAI_DIR / "memory.md"
    _self.KAI_GOALS_FILE = _self.KAI_DIR / "goals.md"
    # 向后兼容
    _self.SECRETARY_MEMORY_FILE = _self.KAI_MEMORY_FILE
    _self.SECRETARY_GOALS_FILE = _self.KAI_GOALS_FILE


def ensure_dirs():
    """确保所有运行时目录存在 (在 CLI 入口处调用)"""
    for d in [REPORT_DIR, STATS_DIR,
              SOLVED_DIR, UNSOLVED_DIR, TESTCASES_DIR, LOGS_DIR, SKILLS_DIR, AGENTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    
    # 确保 Kai 目录存在
    for d in [KAI_DIR, KAI_TASKS_DIR, KAI_ASSIGNED_DIR, KAI_REPORTS_DIR,
              KAI_SOLVED_DIR, KAI_UNSOLVED_DIR, KAI_LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    
    # 确保 agents 目录和默认 agent 目录存在
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    from secretary.agents import register_worker
    register_worker(DEFAULT_WORKER_NAME, description="默认通用工人")

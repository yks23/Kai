"""
Secretary Agent 系统配置

BASE_DIR (工作区):
  默认为当前工作目录 (CWD)。
  可通过环境变量 SECRETARY_WORKSPACE 或 CLI 参数 --workspace 覆盖。

PROMPTS_DIR (提示词模板):
  固定指向包内的 prompts/ 目录，随包分发。
"""
import os
from pathlib import Path

# ============ 包内路径 (不可配置) ============
_PACKAGE_DIR = Path(__file__).parent.resolve()
PROMPTS_DIR = _PACKAGE_DIR / "prompts"          # 提示词模板 (随包分发)

# ============ 工作区路径 (可配置) ============
BASE_DIR = Path(os.environ.get("SECRETARY_WORKSPACE", "")).resolve() \
    if os.environ.get("SECRETARY_WORKSPACE") else Path.cwd().resolve()

TASKS_DIR = BASE_DIR / "tasks"              # 待处理任务 (秘书agent写入)
ONGOING_DIR = BASE_DIR / "ongoing"          # 执行中任务 (scanner移入, worker完成后删除)
REPORT_DIR = BASE_DIR / "report"            # Worker 完成报告 (待回收者审查)
STATS_DIR = BASE_DIR / "stats"             # 调用统计 + 对话日志
SOLVED_DIR = BASE_DIR / "solved-report"     # 回收者确认完成的报告
UNSOLVED_DIR = BASE_DIR / "unsolved-report" # 回收者判定未完成的报告

# ============ Cursor Agent 配置 ============
CURSOR_BIN = os.environ.get("CURSOR_BIN", "cursor")
DEFAULT_MODEL = os.environ.get("CURSOR_MODEL", "Auto")  # "Auto" = 由Cursor自动选择模型

# ============ 扫描器配置 ============
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "5"))       # 扫描tasks/间隔(秒)
WORKER_RETRY_INTERVAL = int(os.environ.get("RETRY_INTERVAL", "3"))  # worker重试间隔(秒)
DEFAULT_MIN_TIME = int(os.environ.get("MIN_TIME", "0"))          # 默认最低执行时间(秒), 0=不限制
# 仅以下类型的任务会被 scanner 执行；monitor 等其它类型不进入执行流程
EXECUTABLE_TASK_TYPES = ("task", "scan", "recycle")

# ============ 回收者配置 ============
RECYCLER_INTERVAL = int(os.environ.get("RECYCLER_INTERVAL", "120"))  # 回收者扫描间隔(秒) = 2分钟

TESTCASES_DIR = BASE_DIR / "testcases"  # 测试样例文件夹
SECRETARY_MEMORY_FILE = BASE_DIR / "secretary_memory.md"  # 秘书Agent记忆文件

# ============ 预设指令 ============
PRESETS = {
    "envolving": (
        "考虑怎么优化这个仓库，从易用性，可移植性，额外功能等角度。"
        "调用完成之后，在终端再次执行 secretary envolving，但不用等待其结束，可以立马终止。"
    ),
    "analysis": (
        "分析这个仓库应该有的功能，并针对此在 {testcases_dir} 下编写测试样例，"
        "要求这些测试样例能够真正地反映仓库是否功能正常。"
    ),
    "debug": (
        "在 {testcases_dir} 下通过所有的测试样例，如果没有通过持续工作，"
        "直到这个文件夹下所有测试全部通过。全部通过之后终止。"
    ),
}


def ensure_dirs():
    """确保所有运行时目录存在 (在 CLI 入口处调用)"""
    for d in [TASKS_DIR, ONGOING_DIR, REPORT_DIR, STATS_DIR,
              SOLVED_DIR, UNSOLVED_DIR, TESTCASES_DIR]:
        d.mkdir(parents=True, exist_ok=True)

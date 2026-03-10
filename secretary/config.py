"""
study 系统配置

WORKSPACE (工作区) 优先级:
  1. CLI 参数 --workspace / -w        (最高)
  2. 环境变量 SECRETARY_WORKSPACE
  3. 持久化配置 study base <path>
  4. 当前工作目录 CWD                   (最低)

BASE_DIR 统一为 WORKSPACE/study

PROMPTS_DIR (提示词模板):
  固定指向包内的 prompts/ 目录，随包分发。
"""
import os
from pathlib import Path
from typing import Optional

# ============ 包内路径 (不可配置) ============
_PACKAGE_DIR = Path(__file__).parent.resolve()
PROMPTS_DIR = _PACKAGE_DIR / "prompts"          # 提示词模板 (随包分发)

# ============ 工作区路径 (可配置) ============
# WORKSPACE: 用户指定的工作目录（agent 执行时的工作目录）
# BASE_DIR: 统一为 WORKSPACE/study（系统目录存放位置）
WORKSPACE: Optional[Path] = None

def _resolve_workspace() -> Path:
    """
    按优先级确定 WORKSPACE:
      env var > CWD
    (CLI --workspace 在 cli.py 中覆盖，优先级最高)

    注意：持久化的 study base 设置不再影响启动路径，避免移动项目目录后
    使用旧路径导致 scanner 监视错误目录。若需固定工作区请设置
    SECRETARY_WORKSPACE 环境变量。
    """
    # 优先: 环境变量
    env_ws = os.environ.get("SECRETARY_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).resolve()

    # 始终使用 CWD（CLI 启动后 main() 会再次 apply_workspace(CWD) 确认）
    return Path.cwd().resolve()


# 初始化时使用默认值，CLI 启动时会自动应用当前工作目录
WORKSPACE = _resolve_workspace()

def _resolve_base_dir(workspace: Path) -> Path:
    """统一使用 workspace/study 目录。"""
    return workspace / "study"


# BASE_DIR 统一为 WORKSPACE/study
BASE_DIR = _resolve_base_dir(WORKSPACE)

# 自定义目录（用于用户贡献的 agent 类型和提示词）
CUSTOM_AGENTS_DIR = BASE_DIR / "custom_agents"  # 自定义 agent 类型目录
CUSTOM_PROMPTS_DIR = BASE_DIR / "custom_prompts"  # 自定义提示词模板目录

# ============ 系统目录 ============

DEFAULT_WORKER_NAME = "sen"  # 默认 agent 名称（保持向后兼容）

AGENTS_DIR = BASE_DIR / "agents"
SKILLS_DIR = BASE_DIR / "skills"
TESTCASES_DIR = BASE_DIR / "testcases"
AGENTS_FILE = AGENTS_DIR / "agents.json"

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

# ============ 执行方式与日志 ============
# 前台执行：task, keep（仅 spawn 子进程后立即返回）, hire, fire, workers, monitor, report, base, name, model, target, help, check（tail -f）, stop, clean-*, skills, learn, forget, use
# 后台执行（输出写日志）：start <worker|study>, keep（子进程循环）, recycle
# 日志路径：所有 agent 相关 → agents/<name>/logs/scanner.log
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


def get_workspace() -> Path:
    """
    获取 agent 执行时的工作目录（WORKSPACE）
    """
    return WORKSPACE


def apply_workspace(ws: Path):
    """运行时切换工作区 (由 CLI --workspace 或 study base 调用)"""
    import secretary.config as _self
    ws_resolved = ws.resolve()
    _self.WORKSPACE = ws_resolved
    _self.BASE_DIR = _resolve_base_dir(ws_resolved)
    _self.CUSTOM_AGENTS_DIR = _self.BASE_DIR / "custom_agents"
    _self.CUSTOM_PROMPTS_DIR = _self.BASE_DIR / "custom_prompts"
    _self.AGENTS_DIR = _self.BASE_DIR / "agents"
    _self.SKILLS_DIR = _self.BASE_DIR / "skills"
    _self.TESTCASES_DIR = _self.BASE_DIR / "testcases"
    _self.AGENTS_FILE = _self.AGENTS_DIR / "agents.json"


def ensure_dirs():
    """确保所有运行时目录存在"""
    for d in [TESTCASES_DIR, SKILLS_DIR, AGENTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    try:
        from secretary.agents import list_workers
        for worker in list_workers():
            agent_dir = AGENTS_DIR / worker.get("name", "")
            for sub in ("tasks", "ongoing", "reports", "logs", "stats"):
                (agent_dir / sub).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

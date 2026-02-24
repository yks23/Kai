"""
Kai 系统配置

WORKSPACE (工作区) 优先级:
  1. CLI 参数 --workspace / -w        (最高)
  2. 环境变量 SECRETARY_WORKSPACE
  3. 持久化配置 kai base <path>
  4. 当前工作目录 CWD                   (最低)

BASE_DIR 统一为 WORKSPACE/Kai

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
# BASE_DIR: 统一为 WORKSPACE/Kai（系统目录存放位置）
WORKSPACE: Optional[Path] = None

def _resolve_workspace() -> Path:
    """
    按优先级确定 WORKSPACE:
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


WORKSPACE = _resolve_workspace()

# BASE_DIR 统一为 WORKSPACE/Kai
BASE_DIR = WORKSPACE / "Kai"

# 自定义目录（用于用户贡献的 agent 类型和提示词）
CUSTOM_AGENTS_DIR = BASE_DIR / "custom_agents"  # 自定义 agent 类型目录
CUSTOM_PROMPTS_DIR = BASE_DIR / "custom_prompts"  # 自定义提示词模板目录

# ============ 文件夹配置系统 ============
class DirectoryConfig:
    """文件夹配置类，用于管理所有系统目录"""
    
    def __init__(self, workspace: Path, base_dir: Path):
        """
        初始化目录配置
        
        Args:
            workspace: 工作目录（agent 执行时的工作目录）
            base_dir: 基础目录（系统目录存放位置，通常是 workspace/Kai）
        """
        self.workspace = workspace
        self.base_dir = base_dir
        
        # 系统目录（在 base_dir 下）
        self.agents_dir = base_dir / "agents"
        self.skills_dir = base_dir / "skills"
        self.testcases_dir = base_dir / "testcases"
        
        # Agent 相关文件
        self.agents_file = self.agents_dir / "agents.json"
    
    def get_agent_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的目录"""
        return self.agents_dir / agent_name
    
    def get_agent_input_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的 input 目录（tasks/）"""
        return self.get_agent_dir(agent_name) / "tasks"
    
    def get_agent_processing_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的 processing 目录（ongoing/）"""
        return self.get_agent_dir(agent_name) / "ongoing"
    
    def get_agent_output_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的 output 目录（reports/）"""
        return self.get_agent_dir(agent_name) / "reports"
    
    # 向后兼容的方法
    def get_agent_tasks_dir(self, agent_name: str) -> Path:
        """向后兼容：获取指定 agent 的 tasks 目录"""
        return self.get_agent_input_dir(agent_name)
    
    def get_agent_ongoing_dir(self, agent_name: str) -> Path:
        """向后兼容：获取指定 agent 的 ongoing 目录"""
        return self.get_agent_processing_dir(agent_name)
    
    def get_agent_reports_dir(self, agent_name: str) -> Path:
        """向后兼容：获取指定 agent 的 reports 目录"""
        return self.get_agent_output_dir(agent_name)
    
    def get_agent_logs_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的 logs 目录"""
        return self.get_agent_dir(agent_name) / "logs"
    
    def get_agent_stats_dir(self, agent_name: str) -> Path:
        """获取指定 agent 的 stats 目录"""
        return self.get_agent_dir(agent_name) / "stats"
    
    def ensure_dirs(self):
        """确保所有系统目录存在"""
        for d in [self.testcases_dir, self.skills_dir, self.agents_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        # 为所有已注册的 agent 创建统一的目录结构（tasks/ongoing/reports）
        try:
            from secretary.agents import list_workers
            workers = list_workers()
            for worker in workers:
                agent_name = worker.get("name")
                agent_dir = self.get_agent_dir(agent_name)
                # 统一的目录结构：tasks/ongoing/reports
                for d in [
                    agent_dir,
                    agent_dir / "tasks",  # input_dir
                    agent_dir / "ongoing",  # processing_dir
                    agent_dir / "reports",  # output_dir
                    agent_dir / "logs",
                    agent_dir / "stats",
                ]:
                    d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


# 全局目录配置实例
_dir_config = DirectoryConfig(WORKSPACE, BASE_DIR)

# ============ 向后兼容的目录变量 ============
# 注意: 不再使用根目录的 tasks/ 和 ongoing/
# 所有任务都分配到 agent 目录中 (agents/{name}/tasks 和 agents/{name}/ongoing)
# 默认 agent 名为 "sen"，当没有指定 agent 时使用

DEFAULT_WORKER_NAME = "sen"  # 默认 agent 名称（保持向后兼容）

# 使用目录配置实例的属性
SKILLS_DIR = _dir_config.skills_dir
AGENTS_DIR = _dir_config.agents_dir
AGENTS_FILE = _dir_config.agents_file
TESTCASES_DIR = _dir_config.testcases_dir

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
# 后台执行（输出写日志）：start <worker|kai>, keep（子进程循环）, recycle
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


def get_dir_config() -> DirectoryConfig:
    """
    获取全局目录配置实例
    """
    return _dir_config


def apply_workspace(ws: Path):
    """
    运行时切换工作区 (由 CLI --workspace 或 kai base 调用)
    
    Args:
        ws: 工作目录路径
    """
    import secretary.config as _self
    ws_resolved = ws.resolve()
    _self.WORKSPACE = ws_resolved
    _self.BASE_DIR = ws_resolved / "Kai"
    
    # 更新自定义目录
    _self.CUSTOM_AGENTS_DIR = _self.BASE_DIR / "custom_agents"
    _self.CUSTOM_PROMPTS_DIR = _self.BASE_DIR / "custom_prompts"
    
    # 更新目录配置实例
    _self._dir_config = DirectoryConfig(ws_resolved, _self.BASE_DIR)
    
    # 更新向后兼容的目录变量
    _self.SKILLS_DIR = _self._dir_config.skills_dir
    _self.AGENTS_DIR = _self._dir_config.agents_dir
    _self.AGENTS_FILE = _self._dir_config.agents_file
    _self.TESTCASES_DIR = _self._dir_config.testcases_dir


# 向后兼容：保留 apply_base_dir 作为 apply_workspace 的别名
def apply_base_dir(ws: Path):
    """向后兼容函数，实际调用 apply_workspace"""
    apply_workspace(ws)


def ensure_dirs():
    """确保所有运行时目录存在 (在 CLI 入口处调用)"""
    _dir_config.ensure_dirs()

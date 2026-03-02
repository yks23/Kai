"""
Agent 类型基类

定义所有 agent 类型的通用接口和基础功能，以及共用的工具函数。
"""
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from secretary.agent_config import AgentConfig

if TYPE_CHECKING:
    from secretary.agent_runner import AgentResult


class AgentType(ABC):
    """Agent 类型基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 类型名称"""
        pass

    @property
    @abstractmethod
    def label_template(self) -> str:
        """标签模板，例如 '👷 {name}'"""
        pass

    @property
    @abstractmethod
    def prompt_template(self) -> str:
        """首轮提示词模板文件名"""
        pass

    @abstractmethod
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """构建该类型的 AgentConfig"""
        pass

    @abstractmethod
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """处理任务文件"""
        pass


# ============================================================
#  共用工具函数
# ============================================================

def prepare_dialog_file(config: AgentConfig, stem: str) -> "Path | None":
    """
    确保 dialog.md 存在，追加任务/对话分隔标题，返回文件路径。
    如果 config 没有 dialog_dir，返回 None。
    """
    if not config.dialog_dir:
        return None
    config.dialog_dir.mkdir(parents=True, exist_ok=True)
    dialog_file = config.dialog_dir / "dialog.md"
    with open(dialog_file, "a", encoding="utf-8") as f:
        f.write(f"\n\n{'='*60}\n")
        f.write(f"## {stem}\n")
        f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*60}\n")
    return dialog_file


def run_agent_with_session(
    agent_name: str,
    first_prompt: str,
    continue_prompt: str,
    dialog_file: "Path | None" = None,
    verbose: bool = True,
) -> "AgentResult":
    """
    通用 agent 执行入口：
    - 有已存 session_id → 发续轮提示词（简短）
    - 无 session_id    → 发首轮提示词（完整角色定义）
    执行后自动持久化 session_id。
    """
    from secretary.agents import load_agent_session_id, save_agent_session_id
    from secretary.agent_runner import run_agent
    from secretary.settings import get_model
    import secretary.config as cfg

    sid = load_agent_session_id(agent_name)
    if sid:
        prompt = continue_prompt
        if verbose:
            print(f"   续轮 [{sid[:8]}…]")
    else:
        prompt = first_prompt
        if verbose:
            print("   首轮 [新会话]")

    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.get_workspace()),
        model=get_model(),
        verbose=verbose,
        session_id=sid,
        dialog_file=dialog_file,
    )

    if result.stats.session_id:
        save_agent_session_id(agent_name, result.stats.session_id)

    return result

"""
统一的 Agent 配置系统

每个 agent 类型通过配置来定义：
- 触发规则（统一：监视目录是否有文件或为空）
- 终止条件（secretary/boss/recycler：单次执行；worker：直到删除ongoing文件）
- 提示词模板（secretary.md、boss.md、recycler.md、worker_first_round.md等）
- 处理逻辑（如何调用agent）

注意：具体的 agent 类型定义已移至 secretary/agent_types/ 目录
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, List
from enum import Enum


class TerminationCondition(Enum):
    """终止条件类型"""
    SINGLE_RUN = "single_run"  # 单次执行后终止（如kai）
    UNTIL_FILE_DELETED = "until_file_deleted"  # 直到ongoing文件被删除（如worker）


class TriggerCondition(Enum):
    """触发条件类型"""
    HAS_FILES = "has_files"  # 目录中有文件时触发（默认）
    IS_EMPTY = "is_empty"  # 目录为空时触发（如Boss）


@dataclass
class TriggerConfig:
    """
    统一的触发配置
    
    定义agent的触发规则：监视哪些目录，在什么条件下触发
    """
    # 监视的目录列表（可以是多个目录，需要全部满足条件才触发）
    watch_dirs: List[Path] = field(default_factory=list)
    
    # 触发条件：HAS_FILES（有文件时触发）或 IS_EMPTY（为空时触发）
    condition: TriggerCondition = TriggerCondition.HAS_FILES
    
    # 是否创建虚拟触发文件（当条件满足时，创建一个临时文件用于触发）
    create_virtual_file: bool = False
    
    # 虚拟触发文件名（如果create_virtual_file=True）
    virtual_file_name: str = ".trigger"
    
    # 自定义触发函数（可选，如果提供则使用此函数而不是默认逻辑）
    custom_trigger_fn: Callable[['AgentConfig'], List[Path]] | None = None


@dataclass
class AgentConfig:
    """
    统一的 Agent 配置
    
    所有 agent 都使用统一的触发规则配置，
    但通过配置来区分终止条件和提示词。
    """
    name: str  # agent 名称
    base_dir: Path  # agent 基础目录 (agents/<name>)
    
    # 目录结构（统一）
    tasks_dir: Path  # tasks/ 目录
    ongoing_dir: Path  # ongoing/ 目录（某些agent可能不需要）
    reports_dir: Path | None  # reports/ 目录（某些agent可能不需要，如secretary）
    logs_dir: Path  # logs/ 目录
    stats_dir: Path  # stats/ 目录
    
    # 提示词模板名称（必须字段，放在有默认值的字段之前）
    first_round_prompt: str  # 首轮提示词模板
    
    # 终止条件（有默认值）
    termination: TerminationCondition = TerminationCondition.UNTIL_FILE_DELETED
    
    # 触发配置（有默认值）
    trigger: TriggerConfig = field(default_factory=lambda: TriggerConfig())
    
    # 续轮和完善阶段提示词（有默认值）
    continue_prompt: str | None = None  # 续轮提示词模板（如果需要）
    refine_prompt: str | None = None  # 完善阶段提示词模板（如果需要）
    
    # 处理函数（有默认值）
    process_fn: Callable[[Path], any] | None = None  # 自定义处理函数（如果为None，使用默认逻辑）
    
    # 标签（用于日志，有默认值）
    label: str = ""
    
    # 是否需要ongoing目录（有默认值）
    use_ongoing: bool = True  # secretary/boss/recycler不需要ongoing，worker需要
    
    # 输出目录（某些agent可能需要，如kai的assigned，有默认值）
    output_dir: Path | None = None
    
    # 日志文件（有默认值）
    log_file: Path | None = None


def build_worker_config(base_dir: Path, worker_name: str) -> AgentConfig:
    """构建 Worker 的配置（使用集中化定义）"""
    from secretary.agent_types import WorkerAgent
    agent_type = WorkerAgent()
    return agent_type.build_config(base_dir, worker_name)


def build_boss_config(base_dir: Path, boss_name: str) -> AgentConfig:
    """构建 Boss 的配置（使用集中化定义）"""
    from secretary.agent_types import BossAgent
    agent_type = BossAgent()
    return agent_type.build_config(base_dir, boss_name)


def build_recycler_config(base_dir: Path, recycler_name: str = "recycler") -> AgentConfig:
    """构建 Recycler 的配置（使用集中化定义）"""
    from secretary.agent_types import RecyclerAgent
    agent_type = RecyclerAgent()
    return agent_type.build_config(base_dir, recycler_name)


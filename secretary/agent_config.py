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
from typing import Callable, List
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
    
    所有 agent 都使用统一的目录结构（input/processing/output），
    但通过配置来区分触发逻辑、终止条件和提示词。
    
    统一的目录结构：
    - input_dir (tasks/): 输入目录，由其他 agent 或人类写入任务
    - processing_dir (ongoing/): 处理目录，标识正在处理的任务
    - output_dir (reports/): 输出目录，工作完成后的总结
    """
    name: str  # agent 名称
    base_dir: Path  # agent 基础目录 (agents/<name>)
    
    # 统一的目录结构（新）
    input_dir: Path  # input 目录 (tasks/)
    processing_dir: Path  # processing 目录 (ongoing/)
    output_dir: Path  # output 目录 (reports/)
    
    # 其他目录
    logs_dir: Path  # logs/ 目录
    stats_dir: Path  # stats/ 目录
    
    # 提示词模板名称（必须字段，放在有默认值的字段之前）
    first_round_prompt: str  # 首轮提示词模板
    
    # 终止条件（有默认值）
    termination: TerminationCondition = TerminationCondition.UNTIL_FILE_DELETED
    
    # 触发配置（有默认值）
    trigger: TriggerConfig = field(default_factory=lambda: TriggerConfig())
    
    # 续轮提示词（有默认值）
    continue_prompt: str | None = None
    
    # 标签（用于日志，有默认值）
    label: str = ""
    
    # 是否需要ongoing目录（有默认值）
    use_ongoing: bool = True  # secretary/boss/recycler不需要ongoing，worker需要
    
    # 会话管理（有默认值）
    session_id: str | None = None  # Cursor TUI 会话号，用于维护记忆和多轮对话
    
    # 日志文件（有默认值）
    log_file: Path | None = None




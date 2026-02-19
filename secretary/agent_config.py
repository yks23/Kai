"""
统一的 Agent 配置系统

每个 agent 类型通过配置来定义：
- 触发规则（统一：监视目录是否有文件或为空）
- 终止条件（kai：单次执行；worker：直到删除ongoing文件）
- 提示词模板（kai：secretary.md；worker：worker_first_round.md等）
- 处理逻辑（如何调用agent）
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
    termination: TerminationCondition = TerminationCondition.SINGLE_RUN
    
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
    use_ongoing: bool = True  # kai不需要ongoing，worker需要
    
    # 输出目录（某些agent可能需要，如kai的assigned，有默认值）
    output_dir: Path | None = None
    
    # 日志文件（有默认值）
    log_file: Path | None = None


def build_worker_config(base_dir: Path, worker_name: str) -> AgentConfig:
    """构建 Worker 的配置"""
    worker_dir = base_dir / "agents" / worker_name
    return AgentConfig(
        name=worker_name,
        base_dir=worker_dir,
        tasks_dir=worker_dir / "tasks",
        ongoing_dir=worker_dir / "ongoing",
        reports_dir=worker_dir / "reports",
        logs_dir=worker_dir / "logs",
        stats_dir=worker_dir / "stats",
        trigger=TriggerConfig(
            # worker只需要监视tasks目录，ongoing目录是处理任务时使用的，不应该作为触发条件
            watch_dirs=[worker_dir / "tasks"],
            condition=TriggerCondition.HAS_FILES,
        ),
        termination=TerminationCondition.UNTIL_FILE_DELETED,
        first_round_prompt="worker_first_round.md",
        continue_prompt="worker_continue.md",
        refine_prompt="worker_refine.md",
        use_ongoing=True,
        log_file=worker_dir / "logs" / "scanner.log",
        label=f"👷 {worker_name}",
    )


def build_boss_config(base_dir: Path, boss_name: str) -> AgentConfig:
    """
    构建 Boss 的配置
    
    Boss的触发规则：检查所监视worker的tasks/和ongoing/是否为空
    如果为空，创建虚拟触发文件
    """
    boss_dir = base_dir / "agents" / boss_name
    
    # Boss使用自定义触发函数（需要动态获取worker目录）
    def boss_trigger_fn(config: AgentConfig) -> List[Path]:
        """Boss的触发函数：检查worker的目录是否为空"""
        from secretary.boss import _load_boss_worker_name
        from secretary.agents import _worker_tasks_dir, _worker_ongoing_dir
        
        worker_name = _load_boss_worker_name(config.base_dir)
        if not worker_name:
            return []
        
        worker_tasks_dir = _worker_tasks_dir(worker_name)
        worker_ongoing_dir = _worker_ongoing_dir(worker_name)
        
        # 检查worker的tasks/和ongoing/是否为空
        pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
        ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
        
        # 如果worker的队列不为空，不触发
        if pending_count > 0 or ongoing_count > 0:
            return []
        
        # 如果为空，创建虚拟触发文件
        trigger_file = config.base_dir / ".boss_trigger"
        if not trigger_file.exists():
            trigger_file.touch()
        return [trigger_file]
    
    return AgentConfig(
        name=boss_name,
        base_dir=boss_dir,
        tasks_dir=boss_dir / "tasks",
        ongoing_dir=boss_dir / "ongoing",
        reports_dir=boss_dir / "reports",
        logs_dir=boss_dir / "logs",
        stats_dir=boss_dir / "stats",
        trigger=TriggerConfig(
            watch_dirs=[],  # Boss不使用标准目录监视，使用自定义函数
            condition=TriggerCondition.IS_EMPTY,
            create_virtual_file=True,
            virtual_file_name=".boss_trigger",
            custom_trigger_fn=boss_trigger_fn,
        ),
        termination=TerminationCondition.SINGLE_RUN,  # Boss每次处理一个任务后终止，等待下次触发
        first_round_prompt="boss.md",
        use_ongoing=False,  # Boss不需要ongoing目录
        log_file=boss_dir / "logs" / "scanner.log",
        label=f"👔 {boss_name}",
    )


def build_recycler_config(base_dir: Path, recycler_name: str = "recycler") -> AgentConfig:
    """
    构建 Recycler 的配置
    
    Recycler的触发规则：扫描所有agent的reports/目录，查找*-report.md文件
    """
    recycler_dir = base_dir / "agents" / recycler_name
    
    def recycler_trigger_fn(config: AgentConfig) -> List[Path]:
        """Recycler的触发函数：扫描所有agent的reports目录"""
        from secretary.recycler import _find_report_files
        return _find_report_files()
    
    return AgentConfig(
        name=recycler_name,
        base_dir=recycler_dir,
        tasks_dir=recycler_dir / "tasks",
        ongoing_dir=recycler_dir / "ongoing",
        reports_dir=recycler_dir / "reports",
        logs_dir=recycler_dir / "logs",
        stats_dir=recycler_dir / "stats",
        trigger=TriggerConfig(
            watch_dirs=[],  # Recycler不使用标准目录监视，使用自定义函数扫描所有reports
            condition=TriggerCondition.HAS_FILES,
            custom_trigger_fn=recycler_trigger_fn,
        ),
        termination=TerminationCondition.SINGLE_RUN,  # Recycler每次处理一个报告后终止，等待下次触发
        first_round_prompt="recycler.md",
        use_ongoing=False,  # Recycler不需要ongoing目录
        log_file=recycler_dir / "logs" / "scanner.log",
        label=f"♻️ {recycler_name}",
    )


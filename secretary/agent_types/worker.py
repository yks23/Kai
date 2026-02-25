"""
Worker Agent 类型定义与执行逻辑

Worker 负责执行编程任务，特点：
- 目录结构：统一的 input_dir (tasks/), processing_dir (ongoing/), output_dir (reports/)
- 触发规则：input_dir 目录有文件时触发
- 终止条件：直到 processing_dir 中的任务文件被删除
- 处理逻辑：多轮对话，支持续轮和完善阶段
- 会话管理：第一轮使用完整提示词，后续使用 session_id 续轮
"""
from pathlib import Path
from typing import List

from secretary.config import BASE_DIR
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType


# ============================================================
#  提示词构建与执行（供 scanner 与类型内部使用）
# ============================================================

def _try_parse_workspace(task_file: Path) -> str:
    """尝试从任务文件内容中解析工作区路径"""
    content = task_file.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped and ("/" in stripped or "\\" in stripped) and not stripped.startswith("#"):
            if Path(stripped).is_dir():
                return stripped
    return ""


def build_first_round_prompt(task_file: Path, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """首轮提示词 — 从模板加载，填入任务内容"""
    from secretary.agents import _worker_reports_dir, _worker_memory_file

    task_content = task_file.read_text(encoding="utf-8")
    report_filename = task_file.name.replace(".md", "") + "-report.md"
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")

    memory_file_path = ""
    if agent_name:
        memory_file_path = _worker_memory_file(agent_name)

    template = load_prompt("worker_first_round.md")
    return template.format(
        base_dir=BASE_DIR,
        task_file=task_file,
        task_content=task_content,
        report_dir=effective_report_dir,
        report_filename=report_filename,
        memory_file_path=memory_file_path,
    )


def build_continue_prompt(task_file: Path, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """续轮提示词 — 从模板加载，简短指令"""
    from secretary.agents import _worker_reports_dir
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    template = load_prompt("worker_continue.md")
    return template.format(task_file=task_file, report_dir=effective_report_dir)


def build_refine_prompt(elapsed_sec: float, min_time: int, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """完善阶段提示词 — Agent 提前完成了但最低时间未到"""
    from secretary.agents import _worker_reports_dir
    remaining_sec = max(0, min_time - elapsed_sec)
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    template = load_prompt("worker_refine.md")
    return template.format(
        elapsed_sec=elapsed_sec,
        min_time=min_time,
        remaining_sec=remaining_sec,
        report_dir=effective_report_dir,
    )


def run_worker_first_round(task_file: Path, workspace: str = "", verbose: bool = True,
                            timeout_sec: int | None = None, report_dir: Path | None = None, agent_name: str | None = None):
    """首轮调用 Worker Agent — 全新会话，完整提示词"""
    if not workspace:
        workspace = _try_parse_workspace(task_file)
    prompt = build_first_round_prompt(task_file, report_dir=report_dir, agent_name=agent_name)
    from secretary.settings import get_model
    from secretary.config import get_workspace
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(get_workspace()),
        model=get_model(),
        verbose=verbose,
        continue_session=False,
        timeout=timeout_sec,
    )


def run_worker_continue(task_file: Path, workspace: str = "", verbose: bool = True,
                        timeout_sec: int | None = None, session_id: str = "", report_dir: Path | None = None, agent_name: str | None = None):
    """续轮调用 Worker Agent — 使用 session_id 精确恢复会话"""
    if not workspace:
        workspace = _try_parse_workspace(task_file)
    prompt = build_continue_prompt(task_file, report_dir=report_dir, agent_name=agent_name)
    from secretary.settings import get_model
    from secretary.config import get_workspace
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(get_workspace()),
        model=get_model(),
        verbose=verbose,
        session_id=session_id,
        timeout=timeout_sec,
    )


def run_worker_refine(elapsed_sec: float, min_time: int,
                      workspace: str = "", verbose: bool = True,
                      timeout_sec: int | None = None, session_id: str = "", report_dir: Path | None = None, agent_name: str | None = None):
    """完善阶段调用 — Agent 已完成任务但最低执行时间未到，使用 session_id 继续优化"""
    prompt = build_refine_prompt(elapsed_sec, min_time, report_dir=report_dir, agent_name=agent_name)
    from secretary.settings import get_model
    from secretary.config import get_workspace
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(get_workspace()),
        model=get_model(),
        verbose=verbose,
        session_id=session_id,
        timeout=timeout_sec,
    )


# ============================================================
#  Agent 类型定义
# ============================================================

class WorkerAgent(AgentType):
    """Worker Agent 类型"""
    
    @property
    def name(self) -> str:
        return "worker"
    
    @property
    def label_template(self) -> str:
        return "👷 {name}"
    
    @property
    def prompt_template(self) -> str:
        return "worker_first_round.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """构建 Worker 的配置"""
        worker_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=worker_dir,
            input_dir=worker_dir / "tasks",
            processing_dir=worker_dir / "ongoing",
            output_dir=worker_dir / "reports",
            logs_dir=worker_dir / "logs",
            stats_dir=worker_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[worker_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt="worker_first_round.md",
            continue_prompt="worker_continue.md",
            refine_prompt="worker_refine.md",
            use_ongoing=True,
            log_file=worker_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理 Worker 任务
        
        流程：
        1. 将任务文件从 tasks/ 移动到 ongoing/
        2. 调用 process_ongoing_task 处理
        """
        import shutil
        from datetime import datetime
        import traceback
        
        # 确保 processing 目录存在
        config.processing_dir.mkdir(parents=True, exist_ok=True)
        
        # 将任务文件移动到 processing 目录
        ongoing_file = config.processing_dir / task_file.name
        try:
            if task_file.exists():
                shutil.move(str(task_file), str(ongoing_file))
                if verbose:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"\n[{ts}] 📦 任务文件已移动到 processing/: {ongoing_file.name}")
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 移动任务文件到 processing/ 失败: {task_file.name} | 错误: {e}")
            traceback.print_exc()
            return
        
        # 处理 ongoing 目录中的任务文件（延迟导入避免与 scanner 循环依赖）
        from secretary.scanner import process_ongoing_task
        process_ongoing_task(ongoing_file, verbose=verbose, config=config)


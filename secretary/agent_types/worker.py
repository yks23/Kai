"""
Worker Agent 类型定义与执行逻辑

Worker 负责执行编程任务，特点：
- 目录结构：input_dir (tasks/), output_dir (reports/)
- 触发规则：input_dir 目录有文件时触发
- 单次阻塞调用：scanner 只触发，agent 自行读取任务文件、完成工作、移动/删除文件、写 report
- 会话管理：首次调用（无 session）发完整提示词，后续（有 session）发简短续轮提示词
"""
import traceback
from datetime import datetime
from pathlib import Path

from secretary.config import BASE_DIR
from secretary.agent_loop import load_prompt
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType, prepare_dialog_file, run_agent_with_session


# ============================================================
#  提示词构建
# ============================================================

def build_first_round_prompt(task_file: Path, report_dir: Path | None = None,
                              agent_name: str | None = None) -> str:
    """首轮提示词 — 完整角色定义，agent 自行读取任务文件"""
    from secretary.agents import _worker_reports_dir, build_known_agents_section

    report_filename = task_file.name.replace(".md", "") + "-report.md"
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")

    template = load_prompt("worker_first.md")
    return template.format(
        base_dir=BASE_DIR,
        task_file=task_file,
        report_dir=effective_report_dir,
        report_filename=report_filename,
        known_agents_section=build_known_agents_section(agent_name or ""),
    )


def build_continue_prompt(task_file: Path, report_dir: Path | None = None,
                           agent_name: str | None = None) -> str:
    """续轮提示词 — 简短指令"""
    from secretary.agents import _worker_reports_dir
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    template = load_prompt("worker_continue.md")
    return template.format(task_file=task_file, report_dir=effective_report_dir)


def build_refine_prompt(elapsed_sec: float, min_time: int,
                        report_dir: Path | None = None,
                        agent_name: str | None = None) -> str:
    """完善阶段提示词（Agent 提前完成但最低时间未到，保留备用）"""
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
        return "worker_first.md"

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        worker_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=worker_dir,
            input_dir=worker_dir / "tasks",
            processing_dir=worker_dir / "ongoing",
            output_dir=worker_dir / "reports",
            logs_dir=worker_dir / "logs",
            stats_dir=worker_dir / "stats",
            dialog_dir=worker_dir / "dialog",
            trigger=TriggerConfig(
                watch_dirs=[worker_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt="worker_first.md",
            continue_prompt="worker_continue.md",
            refine_prompt="worker_refine.md",
            use_ongoing=True,
            log_file=worker_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """处理 Worker 任务 — 单次阻塞调用，agent 自行管理文件"""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] ▶ {task_file.name} ({config.name})")

        dialog_file = prepare_dialog_file(config, task_file.stem)
        first = build_first_round_prompt(task_file, report_dir=config.output_dir,
                                         agent_name=config.name)
        cont  = build_continue_prompt(task_file, report_dir=config.output_dir,
                                       agent_name=config.name)
        try:
            result = run_agent_with_session(
                config.name, first, cont,
                dialog_file=dialog_file, verbose=verbose,
            )
            ts = datetime.now().strftime("%H:%M:%S")
            status = "✅" if result.success else "❌"
            print(f"[{ts}] {status} {task_file.name} 完成 ({result.duration:.1f}s)")
        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] ❌ {task_file.name}: {e}")
            traceback.print_exc()

"""
Worker Agent — 执行编程任务，支持多轮对话
"""
from pathlib import Path

from secretary.config import BASE_DIR
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agent_config import AgentConfig
from secretary.agent_types.base import AgentType


def _try_parse_workspace(task_file: Path) -> str:
    """尝试从任务文件内容中解析工作区路径"""
    content = task_file.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped and ("/" in stripped or "\\" in stripped) and not stripped.startswith("#"):
            if Path(stripped).is_dir():
                return stripped
    return ""


# ---- 提示词构建 ----

def build_first_round_prompt(task_file: Path, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    from secretary.agents import _worker_reports_dir, _worker_memory_file
    task_content = task_file.read_text(encoding="utf-8")
    report_filename = task_file.name.replace(".md", "") + "-report.md"
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    memory_file_path = _worker_memory_file(agent_name) if agent_name else ""
    template = load_prompt("worker_first_round.md")
    return template.format(
        base_dir=BASE_DIR, task_file=task_file, task_content=task_content,
        report_dir=effective_report_dir, report_filename=report_filename,
        memory_file_path=memory_file_path,
    )


def build_continue_prompt(
    task_file: Path, report_dir: Path | None = None, agent_name: str | None = None,
    task_deleted: bool = False, elapsed_sec: float = 0, min_time: int = 0,
) -> str:
    from secretary.agents import _worker_reports_dir
    if report_dir is None and agent_name:
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    if task_deleted and min_time > 0:
        remaining = max(0, min_time - elapsed_sec)
        status_section = (
            f"- 任务已完成（文件已删除），但最低执行时间未达到\n"
            f"- 已用 {elapsed_sec:.0f}s / 要求 {min_time}s（还需约 {remaining:.0f}s）\n\n"
            f"利用剩余时间复查、补充测试、改善代码质量。不要为凑时间做无意义改动。\n"
        )
    else:
        status_section = "- 任务文件仍存在，任务尚未完成\n"
    template = load_prompt("worker_continue.md")
    return template.format(task_file=task_file, report_dir=effective_report_dir, status_section=status_section)


# ---- Agent 调用 ----

def run_worker_first_round(task_file, workspace="", verbose=True, timeout_sec=None, report_dir=None, agent_name=None):
    if not workspace:
        workspace = _try_parse_workspace(task_file)
    prompt = build_first_round_prompt(task_file, report_dir=report_dir, agent_name=agent_name)
    from secretary.settings import get_model
    from secretary.config import get_workspace
    return run_agent(prompt=prompt, workspace=workspace or str(get_workspace()),
                     model=get_model(), verbose=verbose, timeout=timeout_sec)


def run_worker_continue(task_file, workspace="", verbose=True, timeout_sec=None, session_id="",
                        report_dir=None, agent_name=None, task_deleted=False, elapsed_sec=0.0, min_time=0):
    if not workspace and not task_deleted:
        workspace = _try_parse_workspace(task_file)
    prompt = build_continue_prompt(task_file, report_dir=report_dir, agent_name=agent_name,
                                   task_deleted=task_deleted, elapsed_sec=elapsed_sec, min_time=min_time)
    from secretary.settings import get_model
    from secretary.config import get_workspace
    return run_agent(prompt=prompt, workspace=workspace or str(get_workspace()),
                     model=get_model(), verbose=verbose, session_id=session_id, timeout=timeout_sec)


# ---- Agent 类型定义 ----

class WorkerAgent(AgentType):
    name = "worker"
    icon = "👷"
    first_prompt = "worker_first_round.md"
    continue_prompt = "worker_continue.md"
    use_ongoing = True

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """Worker 特殊处理：移动到 ongoing/ 后进入多轮对话循环"""
        import shutil
        config.processing_dir.mkdir(parents=True, exist_ok=True)
        ongoing_file = config.processing_dir / task_file.name
        try:
            if task_file.exists():
                shutil.move(str(task_file), str(ongoing_file))
        except FileNotFoundError:
            return
        from secretary.scanner import process_ongoing_task
        process_ongoing_task(ongoing_file, verbose=verbose, config=config)

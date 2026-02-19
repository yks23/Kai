"""
Worker Agent — 真正执行任务的 Agent

多轮对话机制:
  第1轮 (首轮): 完整的任务提示词 → agent --print --force --trust "..."
  第2轮+ (续轮): 简短的继续指令 → agent --print --force --trust --resume <session_id> "..."

  Agent 自然停止 ≠ 任务完成。
  任务完成的唯一标志: Agent 主动删除 ongoing/ 中的任务文件。
  只要文件还在，Scanner 就用 --resume <session_id> 让 Agent 精确恢复会话继续工作。

提示词模板:
  prompts/worker_first_round.md
  prompts/worker_continue.md
"""
from pathlib import Path

from secretary.config import BASE_DIR
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent


def build_first_round_prompt(task_file: Path, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """
    首轮提示词 — 从模板加载，填入任务内容
    
    Args:
        task_file: 任务文件路径
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于加载memory和确定report_dir
    """
    task_content = task_file.read_text(encoding="utf-8")
    task_filename = task_file.name
    report_filename = task_filename.replace(".md", "") + "-report.md"
    
    # 使用传入的report_dir，如果没有则使用agent的reports目录
    if report_dir is None and agent_name:
        from secretary.agents import _worker_reports_dir
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")

    # 加载agent的memory（如果提供了agent_name）
    memory_content = ""
    memory_file_path = None
    if agent_name:
        from secretary.agents import load_agent_memory, _worker_memory_file
        memory_content = load_agent_memory(agent_name)
        memory_file_path = _worker_memory_file(agent_name)
    
    memory_section = ""
    if memory_content:
        memory_section = (
            "\n## 你的工作历史（Memory）\n"
            "以下是你的工作总结，包含你之前完成的任务和工作经验：\n\n"
            f"{memory_content}\n"
        )
        if memory_file_path:
            memory_section += f"\n**你的memory文件路径**: `{memory_file_path}`\n"

    template = load_prompt("worker_first_round.md")
    return template.format(
        base_dir=BASE_DIR,
        task_file=task_file,
        task_filename=task_filename,
        task_content=task_content,
        report_dir=effective_report_dir,
        report_filename=report_filename,
        memory_section=memory_section,
        memory_file_path=memory_file_path if memory_file_path else "",
    )


def build_continue_prompt(task_file: Path, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """
    续轮提示词 — 从模板加载，简短指令
    
    Args:
        task_file: 任务文件路径
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于确定report_dir
    """
    if report_dir is None and agent_name:
        from secretary.agents import _worker_reports_dir
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    template = load_prompt("worker_continue.md")
    return template.format(
        task_file=task_file,
        report_dir=effective_report_dir,
    )


def run_worker_first_round(task_file: Path, workspace: str = "", verbose: bool = True,
                           timeout_sec: int | None = None, report_dir: Path | None = None, agent_name: str | None = None):
    """
    首轮调用 Worker Agent — 全新会话，完整提示词

    Args:
        task_file: 任务文件路径
        workspace: 工作区路径
        verbose: 是否显示详细信息
        timeout_sec: 单轮最长执行秒数，None 表示不限制。当任务设定了 min_time 时由 scanner 传入
        至少 (min_time - elapsed + 缓冲)，避免单轮被提前杀断导致无法跑满设定时长。
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于确定report_dir
    """
    if not workspace:
        workspace = _try_parse_workspace(task_file)

    prompt = build_first_round_prompt(task_file, report_dir=report_dir, agent_name=agent_name)

    # 从设置中获取模型
    from secretary.settings import get_model
    model = get_model()
    
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        model=model,
        verbose=verbose,
        continue_session=False,
        timeout=timeout_sec,
    )


def run_worker_continue(task_file: Path, workspace: str = "", verbose: bool = True,
                        timeout_sec: int | None = None, session_id: str = "", report_dir: Path | None = None, agent_name: str | None = None):
    """
    续轮调用 Worker Agent — 使用 session_id 精确恢复会话

    Args:
        task_file: 任务文件路径
        workspace: 工作区路径
        verbose: 是否显示详细信息
        timeout_sec: 单轮最长执行秒数，None 表示不限制。当任务设定了 min_time 时由 scanner 传入。
        session_id: 会话ID，如果提供则使用 --resume <session_id> 精确恢复会话
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于确定report_dir
    """
    if not workspace:
        workspace = _try_parse_workspace(task_file)

    prompt = build_continue_prompt(task_file, report_dir=report_dir, agent_name=agent_name)

    # 从设置中获取模型
    from secretary.settings import get_model
    model = get_model()
    
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        model=model,
        verbose=verbose,
        session_id=session_id,  # 使用 session_id 精确恢复会话
        timeout=timeout_sec,
    )


def build_refine_prompt(elapsed_sec: float, min_time: int, report_dir: Path | None = None, agent_name: str | None = None) -> str:
    """
    完善阶段提示词 — Agent 提前完成了但最低时间未到
    
    Args:
        elapsed_sec: 已用时间（秒）
        min_time: 最低执行时间（秒）
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于确定report_dir
    """
    remaining_sec = max(0, min_time - elapsed_sec)
    if report_dir is None and agent_name:
        from secretary.agents import _worker_reports_dir
        report_dir = _worker_reports_dir(agent_name)
    effective_report_dir = report_dir or (BASE_DIR / "agents" / "unknown" / "reports")
    template = load_prompt("worker_refine.md")
    return template.format(
        elapsed_sec=elapsed_sec,
        min_time=min_time,
        remaining_sec=remaining_sec,
        report_dir=effective_report_dir,
    )


def run_worker_refine(elapsed_sec: float, min_time: int,
                      workspace: str = "", verbose: bool = True,
                      timeout_sec: int | None = None, session_id: str = "", report_dir: Path | None = None, agent_name: str | None = None):
    """
    完善阶段调用 — Agent 已完成任务但最低执行时间未到，使用 session_id 继续优化
    
    Args:
        elapsed_sec: 已用时间（秒）
        min_time: 最低执行时间（秒）
        workspace: 工作区路径
        verbose: 是否显示详细信息
        timeout_sec: 超时时间（秒），None 表示不限制。由 scanner 传入至少剩余 min_time 的时长。
        session_id: 会话ID，如果提供则使用 --resume <session_id> 精确恢复会话
        report_dir: 报告目录，如果为None则使用agent的reports目录
        agent_name: agent名称，用于确定report_dir
    """
    prompt = build_refine_prompt(elapsed_sec, min_time, report_dir=report_dir, agent_name=agent_name)

    # 从设置中获取模型
    from secretary.settings import get_model
    model = get_model()
    
    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        model=model,
        verbose=verbose,
        session_id=session_id,  # 使用 session_id 精确恢复会话
        timeout=timeout_sec,
    )


def _try_parse_workspace(task_file: Path) -> str:
    """尝试从任务文件内容中解析工作区路径"""
    content = task_file.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip().strip("`").strip()
        if stripped and ("/" in stripped) and not stripped.startswith("#"):
            if Path(stripped).is_dir():
                return stripped
    return ""

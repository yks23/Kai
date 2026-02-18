"""
Worker Agent — 真正执行任务的 Agent

多轮对话机制:
  第1轮 (首轮): 完整的任务提示词 → cursor agent --print --force --trust "..."
  第2轮+ (续轮): 简短的继续指令 → cursor agent --print --force --trust --continue "..."

  Agent 自然停止 ≠ 任务完成。
  任务完成的唯一标志: Agent 主动删除 ongoing/ 中的任务文件。
  只要文件还在，Scanner 就用 --continue 让 Agent 接续上下文继续工作。

提示词模板:
  prompts/worker_first_round.md
  prompts/worker_continue.md
"""
from pathlib import Path

from secretary.config import BASE_DIR, ONGOING_DIR, REPORT_DIR, PROMPTS_DIR
from secretary.agent_runner import run_agent


def _load_template(name: str) -> str:
    """加载 Worker 提示词模板"""
    tpl_path = PROMPTS_DIR / name
    return tpl_path.read_text(encoding="utf-8")


def build_first_round_prompt(task_file: Path) -> str:
    """
    首轮提示词 — 从模板加载，填入任务内容
    """
    task_content = task_file.read_text(encoding="utf-8")
    task_filename = task_file.name
    report_filename = task_filename.replace(".md", "") + "-report.md"

    template = _load_template("worker_first_round.md")
    return template.format(
        base_dir=BASE_DIR,
        task_file=task_file,
        task_filename=task_filename,
        task_content=task_content,
        report_dir=REPORT_DIR,
        report_filename=report_filename,
    )


def build_continue_prompt(task_file: Path) -> str:
    """
    续轮提示词 — 从模板加载，简短指令
    """
    template = _load_template("worker_continue.md")
    return template.format(
        task_file=task_file,
        report_dir=REPORT_DIR,
    )


def run_worker_first_round(task_file: Path, workspace: str = "", verbose: bool = True,
                           timeout_sec: int | None = None):
    """
    首轮调用 Worker Agent — 全新会话，完整提示词

    timeout_sec: 单轮最长执行秒数，None 表示不限制。当任务设定了 min_time 时由 scanner 传入
    至少 (min_time - elapsed + 缓冲)，避免单轮被提前杀断导致无法跑满设定时长。
    """
    if not workspace:
        workspace = _try_parse_workspace(task_file)

    prompt = build_first_round_prompt(task_file)

    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        verbose=verbose,
        continue_session=False,
        timeout=timeout_sec,
    )


def run_worker_continue(task_file: Path, workspace: str = "", verbose: bool = True,
                        timeout_sec: int | None = None):
    """
    续轮调用 Worker Agent — 用 --continue 接续上一轮对话

    timeout_sec: 单轮最长执行秒数，None 表示不限制。当任务设定了 min_time 时由 scanner 传入。
    """
    if not workspace:
        workspace = _try_parse_workspace(task_file)

    prompt = build_continue_prompt(task_file)

    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        verbose=verbose,
        continue_session=True,  # 关键: --continue
        timeout=timeout_sec,
    )


def build_refine_prompt(elapsed_sec: float, min_time: int) -> str:
    """
    完善阶段提示词 — Agent 提前完成了但最低时间未到
    """
    remaining_sec = max(0, min_time - elapsed_sec)
    template = _load_template("worker_refine.md")
    return template.format(
        elapsed_sec=elapsed_sec,
        min_time=min_time,
        remaining_sec=remaining_sec,
        report_dir=REPORT_DIR,
    )


def run_worker_refine(elapsed_sec: float, min_time: int,
                      workspace: str = "", verbose: bool = True,
                      timeout_sec: int | None = None):
    """
    完善阶段调用 — Agent 已完成任务但最低执行时间未到，用 --continue 要求继续优化

    timeout_sec: 单轮最长执行秒数，None 表示不限制。由 scanner 传入至少剩余 min_time 的时长。
    """
    prompt = build_refine_prompt(elapsed_sec, min_time)

    return run_agent(
        prompt=prompt,
        workspace=workspace or str(BASE_DIR),
        verbose=verbose,
        continue_session=True,  # 接续之前的对话上下文
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

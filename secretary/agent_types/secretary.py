"""
Secretary Agent — 任务分类、归并和分配
"""
import shutil
import traceback
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agent_config import AgentConfig
from secretary.agent_types.base import AgentType

def get_goals(secretary_name: str) -> list:
    """获取当前全局目标列表（供 CLI 列出）"""
    goals_file = cfg.AGENTS_DIR / secretary_name / "goals.md"
    if not goals_file.exists():
        return []
    text = goals_file.read_text(encoding="utf-8")
    goals = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            goals.append(line[2:].strip())
    return goals


def set_goals(goals: list, secretary_name: str) -> None:
    """将全局目标持久化到 goals.md（覆盖）"""
    goals_file = cfg.AGENTS_DIR / secretary_name / "goals.md"
    goals_file.parent.mkdir(parents=True, exist_ok=True)
    if not goals:
        if goals_file.exists():
            goals_file.unlink()
        return
    lines = ["# 当前全局目标\n", "以下目标在任务归类与分配时请与之对齐。\n\n"]
    for g in goals:
        g = (g or "").strip()
        if g:
            lines.append(f"- {g}\n")
    goals_file.write_text("".join(lines), encoding="utf-8")


def clear_goals(secretary_name: str) -> None:
    """清空当前全局目标"""
    set_goals([], secretary_name)


def _load_goals(secretary_name: str) -> str:
    """加载全局目标文本（供注入到秘书提示词）"""
    goals = get_goals(secretary_name)
    if not goals:
        return ""
    return "\n".join(f"- {g}" for g in goals)


def _load_existing_tasks_summary() -> str:
    """扫描所有工人的任务目录，生成现有任务概览"""
    lines = []
    try:
        from secretary.agents import list_workers, _worker_tasks_dir
        workers = list_workers()
        if not workers:
            return ""
        for w in workers:
            wt = _worker_tasks_dir(w["name"])
            if wt.exists():
                md_files = sorted(wt.glob("*.md"))
                if md_files:
                    lines.append(f"### 工人 {w['name']} 的队列 `{wt}` ({len(md_files)} 个)")
                    for f in md_files:
                        first_line = ""
                        try:
                            first_line = f.read_text(encoding="utf-8").strip().splitlines()[0][:100]
                        except Exception:
                            pass
                        lines.append(f"- `{f.name}`: {first_line}")
    except Exception:
        pass
    return "\n".join(lines) if lines else ""


def _append_memory(user_request: str, agent_output: str, secretary_name: str):
    """将本次调用的摘要追加到记忆文件"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    memory_file = cfg.AGENTS_DIR / secretary_name / "memory.md"
    if not memory_file.exists():
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        memory_file.write_text(
            "# 秘书Agent 记忆\n\n"
            "记录每次调用的决策历史，帮助后续调用做出更一致的归类和分配判断。\n\n",
            encoding="utf-8",
        )
    output_lines = agent_output.strip().splitlines()
    summary_lines = [l for l in output_lines if l.strip()][-5:]
    summary = "\n".join(summary_lines) if summary_lines else "(无输出)"
    entry = (
        f"---\n### [{now}]\n"
        f"- **请求**: {user_request[:200]}\n"
        f"- **决策**: {summary}\n\n"
    )
    with open(memory_file, "a", encoding="utf-8") as f:
        f.write(entry)


def build_secretary_prompt(user_request: str, secretary_name: str) -> str:
    """构建给秘书 Agent 的提示词"""
    from secretary.agent_types.base import _build_known_agents_section

    memory_file_path = str(cfg.AGENTS_DIR / secretary_name / "memory.md")
    known_section = _build_known_agents_section(secretary_name)

    tasks_overview = _load_existing_tasks_summary()
    tasks_section = "\n## 当前待处理任务概览\n" + tasks_overview + "\n" if tasks_overview else ""

    goals_text = _load_goals(secretary_name)
    goals_section = "\n## 当前全局目标\n" + goals_text + "\n" if goals_text else ""

    template = load_prompt("secretary.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        memory_file_path=memory_file_path,
        known_agents_section=known_section,
        tasks_section=tasks_section,
        goals_section=goals_section,
        user_request=user_request,
        reports_dir=cfg.AGENTS_DIR / secretary_name / "reports",
    )


def run_secretary(user_request: str, verbose: bool = True, secretary_name: str = "kai") -> bool:
    """运行秘书 Agent 处理用户请求。返回是否成功。"""
    if verbose:
        print(f"📋 秘书 Agent ({secretary_name}) 收到请求: {user_request[:100]}...")
        memory_file = cfg.AGENTS_DIR / secretary_name / "memory.md"
        print(f"   记忆: {'已加载历史记忆' if memory_file.exists() else '🆕 首次调用，无历史记忆'}")
        try:
            from secretary.agents import list_workers
            workers = list_workers()
            if workers:
                names = [w["name"] for w in workers]
                print(f"   工人: {', '.join(names)} (共 {len(workers)} 个)")
        except Exception:
            pass
        print("   正在分析、归类并分配...")
    prompt = build_secretary_prompt(user_request, secretary_name)
    from secretary.settings import get_model
    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.get_workspace()),
        model=get_model(),
        verbose=verbose,
    )
    if result.success and result.output:
        _append_memory(user_request, result.output, secretary_name)
    return result.success


class SecretaryAgent(AgentType):
    """Secretary Agent — 调用 worker 分配任务"""
    name = "secretary"
    icon = "🤖"
    first_prompt = "secretary.md"
    continue_prompt = "secretary_continue.md"

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """读取任务 → 移动到 reports/ → 调用 run_secretary"""
        try:
            request = task_file.read_text(encoding="utf-8").strip()
        except Exception:
            return
        # 移动任务文件到 reports/ 存档
        try:
            shutil.move(str(task_file), str(config.output_dir / task_file.name))
        except Exception:
            pass
        run_secretary(request, verbose=verbose, secretary_name=config.name)


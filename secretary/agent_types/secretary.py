"""
Secretary Agent 类型定义与执行逻辑

Secretary 负责任务的分类、归并和分配，特点：
- 目录结构：统一的 input_dir (tasks/), processing_dir (ongoing/), output_dir (reports/)
- 触发规则：input_dir 目录有文件时触发
- 终止条件：单次执行后终止
- 处理逻辑：读取任务，调用 run_secretary 处理，将分配结果写入 worker 的 input_dir
- 会话管理：每次都是新会话（单次执行）
"""
import shutil
import traceback
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType


# ============================================================
#  秘书执行逻辑（供 scanner 与类型内部使用）
# ============================================================

def _load_memory(secretary_name: str) -> str:
    """加载秘书的历史记忆"""
    memory_file = cfg.AGENTS_DIR / secretary_name / "memory.md"
    if memory_file.exists():
        content = memory_file.read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        if len(lines) > 150:
            header = lines[:2]
            recent = lines[-150:]
            content = "\n".join(header + ["", "...(更早的记录已省略)...", ""] + recent)
        return content
    return ""


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


def _load_workers_info() -> str:
    """加载工人信息摘要 (供秘书 Agent 分配任务)"""
    try:
        from secretary.agents import build_workers_summary
        return build_workers_summary()
    except Exception:
        return ""


def _load_skills_info() -> str:
    """加载技能信息摘要 (供秘书 Agent 了解系统能力)"""
    try:
        from secretary.skills import list_skills
        skills = list_skills()
        if not skills:
            return ""
        lines = []
        for s in skills:
            tag = "内置" if s["builtin"] else "已学"
            desc = s["description"] or "(无描述)"
            lines.append(f"- **{s['name']}** ({tag}): {desc}")
        return "\n".join(lines)
    except Exception:
        return ""


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
    # 先定义 memory_file_path，避免在 memory 内容中包含 {memory_file_path} 时出错
    memory_file_path = str(cfg.AGENTS_DIR / secretary_name / "memory.md")
    memory = _load_memory(secretary_name)
    memory_section = ""
    if memory:
        memory_section = (
            "\n## 你的历史记忆\n"
            "以下是你之前的决策记录，请参考这些历史来保持归类和分配的一致性。\n\n"
            + memory + "\n"
        )
    workers_info = _load_workers_info()
    workers_section = ""
    if workers_info:
        workers_section = (
            "\n## 已招募的工人及其工作总结\n"
            "以下是当前已招募的工人及其详细信息，**你必须**根据这些信息决定把任务分配给谁。\n\n"
            + workers_info + "\n"
        )
    else:
        workers_section = (
            "\n## ⚠️ 错误：没有可用的工人\n"
            "**当前没有招募任何工人。**\n\n"
            "**你必须拒绝处理这个任务**，并明确告诉用户：\n"
            "- 需要先招募工人才能分配任务\n"
            "- 使用 `kai hire` 或 `kai hire <名字>` 来招募工人\n\n"
            "**不要创建任何任务文件，直接说明需要先招募工人。**\n"
        )
    skills_info = _load_skills_info()
    skills_section = ""
    if skills_info:
        # 使用字符串拼接而不是 f-string，避免解析 skills_info 中的大括号
        skills_section = "\n## 系统已学技能\n" + skills_info + "\n"
    tasks_overview = _load_existing_tasks_summary()
    tasks_section = ""
    if tasks_overview:
        tasks_section = "\n## 当前待处理任务概览\n" + tasks_overview + "\n"
    goals_text = _load_goals(secretary_name)
    goals_section = ""
    if goals_text:
        goals_section = "\n## 当前全局目标\n" + goals_text + "\n"
    template = load_prompt("secretary.md")
    default_tasks_dir = cfg.AGENTS_DIR / cfg.DEFAULT_WORKER_NAME / "tasks"
    secretary_dir = cfg.AGENTS_DIR / secretary_name
    reports_dir = secretary_dir / "reports"
    # memory_file_path 已在函数开头定义
    return template.format(
        base_dir=cfg.BASE_DIR,
        tasks_dir=str(default_tasks_dir),
        memory_file_path=memory_file_path,
        memory_section=memory_section,
        workers_section=workers_section,
        skills_section=skills_section,
        tasks_section=tasks_section,
        goals_section=goals_section,
        user_request=user_request,
        reports_dir=reports_dir,
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


# ============================================================
#  Agent 类型定义
# ============================================================

class SecretaryAgent(AgentType):
    """Secretary Agent 类型"""
    
    @property
    def name(self) -> str:
        return "secretary"
    
    @property
    def label_template(self) -> str:
        return "🤖 {name}"
    
    @property
    def prompt_template(self) -> str:
        return "secretary.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """构建 Secretary 的配置"""
        secretary_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=secretary_dir,
            input_dir=secretary_dir / "tasks",
            processing_dir=secretary_dir / "ongoing",  # secretary不使用ongoing，但保留目录结构
            output_dir=secretary_dir / "reports",
            logs_dir=secretary_dir / "logs",
            stats_dir=secretary_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[secretary_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt="secretary.md",
            use_ongoing=False,  # secretary不使用ongoing
            log_file=secretary_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理 Secretary 任务
        
        流程：
        1. 读取任务内容
        2. 移动到 assigned/ 目录
        3. 调用 run_secretary 处理
        """
        if config.output_dir is None or config.log_file is None:
            print(f"⚠️ [{config.label}] 缺少 output_dir 或 log_file")
            return
        
        try:
            request = task_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 读取任务文件失败: {task_file.name} | 错误: {e}")
            traceback.print_exc()
            if task_file.exists():
                error_file = config.output_dir / f"error-{task_file.name}"
                shutil.move(str(task_file), str(error_file))
            return

        assigned_file = config.output_dir / task_file.name
        try:
            shutil.move(str(task_file), str(assigned_file))
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 移动任务文件失败: {task_file.name} | 错误: {e}")
            traceback.print_exc()
            return

        # 直接运行，输出会自动重定向到日志文件
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("\n" + "=" * 60)
        print(f"[{ts}] 处理任务: {task_file.name}")
        print("=" * 60 + "\n")
        try:
            secretary_name = config.name
            run_secretary(request, verbose=True, secretary_name=secretary_name)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print("\n" + "=" * 60)
            print(f"[{ts}] 任务完成: {task_file.name}")
            print("=" * 60 + "\n")
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ⚠️ 处理任务时发生错误: {e}")
            traceback.print_exc()
            raise


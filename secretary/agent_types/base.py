"""
Agent 类型基类

自定义一个 agent 类型只需：
  1. 继承 AgentType
  2. 设置 name / icon / first_prompt / continue_prompt

最简示例：

    class ReviewerAgent(AgentType):
        name = "reviewer"
        icon = "🔍"
        first_prompt = "reviewer.md"
        continue_prompt = "reviewer_continue.md"

hire 时通过 dep_names 传入关联的 agent：
    kai hire myreviewer reviewer worker1 worker2

提示词模板中用 {known_agents_section} 获取关联 agent 的信息。
"""
from pathlib import Path
from typing import List

from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig,
)


def _build_known_agents_section(agent_name: str) -> str:
    """
    构建 known_agents 上下文：列出本 agent 在 hire 时关联的其他 agent。

    从 agents.json 的 known_agents 字段读取名称列表，
    查询每个 agent 的类型、描述、tasks/ 路径和待处理数。
    """
    from secretary.agents import get_worker, _worker_tasks_dir

    info = get_worker(agent_name)
    if not info:
        return ""
    known_names = info.get("known_agents", [])
    if not known_names:
        return ""

    lines = [
        "## 你可以调用的 Agent\n",
        "向对方的 tasks/ 目录写入 .md 任务文件即可调用。\n",
    ]
    for n in known_names:
        peer = get_worker(n)
        if not peer:
            lines.append(f"- **{n}**: (未注册)\n")
            continue
        t = peer.get("type", "?")
        desc = peer.get("description", "") or "通用"
        tasks_dir = _worker_tasks_dir(n)
        pending = peer.get("pending_count", 0)
        lines.append(
            f"- **{n}** ({t}): {desc}\n"
            f"  调用方式: 写入 `{tasks_dir}/<任务名>.md` | 当前待处理: {pending}\n"
        )
    return "\n".join(lines)


class AgentType:
    """
    Agent 类型基类。

    子类必须设置的属性:
        name              — 类型名称
        icon              — 显示图标
        first_prompt      — 首轮提示词模板文件名
        continue_prompt   — 续轮提示词模板文件名

    子类可选设置的属性:
        use_ongoing        — 是否使用 ongoing 目录（默认 False）

    关联 agent（known_agents）:
        hire 时通过 dep_names 传入，存储在 agents.json 中。
        build_prompt() 自动注入 {known_agents_section}。

    子类可选覆盖的方法:
        build_prompt()     — 构建首轮提示词
        build_config()     — 构建 AgentConfig
        process_task()     — 处理任务
    """

    # ---- 子类必须设置 ----
    name: str = ""
    icon: str = "❓"
    first_prompt: str = ""
    continue_prompt: str = ""

    # ---- 子类可选设置 ----
    use_ongoing: bool = False

    # ---- 提示词构建 ----

    def build_prompt(self, task_file: Path, config: AgentConfig) -> str:
        """
        构建首轮提示词。

        默认实现：load_prompt + format，自动注入 {known_agents_section}。
        子类覆盖此方法可添加额外模板变量。
        """
        from secretary.agent_loop import load_prompt
        from secretary.agents import _worker_memory_file
        import secretary.config as cfg

        template = load_prompt(self.first_prompt)
        task_content = task_file.read_text(encoding="utf-8") if task_file.exists() else ""
        report_filename = task_file.name.replace(".md", "") + "-report.md"
        memory_file_path = _worker_memory_file(config.name)
        known_section = _build_known_agents_section(config.name)

        return template.format(
            base_dir=cfg.BASE_DIR,
            task_file=task_file,
            task_content=task_content,
            report_dir=config.output_dir,
            report_filename=report_filename,
            memory_file_path=memory_file_path,
            known_agents_section=known_section,
        )

    def build_continue_prompt_text(self, task_file: Path, config: AgentConfig) -> str:
        """构建续轮提示词。"""
        from secretary.agent_loop import load_prompt
        import secretary.config as cfg

        if not self.continue_prompt:
            return f"继续处理任务 {task_file.name}，回顾上一轮进展后推进。"
        template = load_prompt(self.continue_prompt)
        return template.format(
            base_dir=cfg.BASE_DIR,
            task_file=task_file,
            report_dir=config.output_dir,
        )

    # ---- 配置构建 ----

    @property
    def label_template(self) -> str:
        return f"{self.icon} {{name}}"

    @property
    def prompt_template(self) -> str:
        return self.first_prompt

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """
        构建 AgentConfig。
        默认：标准目录 + HAS_FILES 触发。需要自定义触发的类型覆盖此方法。
        """
        agent_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=agent_dir,
            input_dir=agent_dir / "tasks",
            processing_dir=agent_dir / "ongoing",
            output_dir=agent_dir / "reports",
            logs_dir=agent_dir / "logs",
            stats_dir=agent_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[agent_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt=self.first_prompt,
            continue_prompt=self.continue_prompt,
            use_ongoing=self.use_ongoing,
            log_file=agent_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )

    # ---- 任务处理 ----

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        默认：单轮 run_agent + 删除任务文件。
        Worker 覆盖此方法实现多轮对话。
        """
        from secretary.agent_runner import run_agent
        from secretary.settings import get_model
        import secretary.config as cfg

        prompt = self.build_prompt(task_file, config)
        if not prompt:
            return

        result = run_agent(
            prompt=prompt,
            workspace=str(cfg.get_workspace()),
            model=get_model(),
            verbose=verbose,
        )

        if verbose:
            status = "✅" if result.success else "❌"
            print(f"   {status} {self.name} 完成 ({result.duration:.1f}s)")

        if task_file.exists():
            try:
                task_file.unlink()
            except Exception:
                pass

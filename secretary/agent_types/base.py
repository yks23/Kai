"""
Agent 类型基类

自定义一个 agent 类型只需：
  1. 继承 AgentType
  2. 设置 name / icon / first_prompt / continue_prompt
  3. 可选设置 known_agent_types 声明能调用哪些类型的 agent

最简示例：

    class ReviewerAgent(AgentType):
        name = "reviewer"
        icon = "🔍"
        first_prompt = "reviewer.md"
        continue_prompt = "reviewer_continue.md"
        known_agent_types = ["worker"]  # 可以调用 worker

提示词模板中用 {known_agents_section} 获取可调用 agent 的信息。
"""
from pathlib import Path
from typing import List

from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig,
)


def _build_known_agents_section(agent_name: str, known_types: list[str]) -> str:
    """
    构建 known_agents 上下文：列出本 agent 可以调用的其他 agent。

    每个 known agent 包含：名字、类型、描述、tasks/ 路径（往这里写 .md 就是调用它）。
    """
    if not known_types:
        return ""

    from secretary.agents import list_workers, _worker_tasks_dir

    agents = list_workers()
    lines = ["## 你可以调用的 Agent\n"]
    lines.append("向对方的 tasks/ 目录写入 .md 任务文件即可调用。\n")
    found = False
    for a in agents:
        if a.get("name") == agent_name:
            continue
        if a.get("type") not in known_types:
            continue
        found = True
        n = a["name"]
        t = a.get("type", "?")
        desc = a.get("description", "") or "通用"
        tasks_dir = _worker_tasks_dir(n)
        pending = a.get("pending_count", 0)
        lines.append(
            f"- **{n}** ({t}): {desc}\n"
            f"  任务目录: `{tasks_dir}` | 待处理: {pending}\n"
        )
    if not found:
        lines.append("(当前没有可调用的 agent)\n")
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
        known_agent_types  — 可调用的 agent 类型列表（默认空 = 不调用其他 agent）

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
    known_agent_types: list[str] = []

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
        known_section = _build_known_agents_section(config.name, self.known_agent_types)

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

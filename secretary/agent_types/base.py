"""
Agent 类型基类

自定义一个 agent 类型只需：
  1. 继承 AgentType
  2. 设置 name / icon / first_prompt / continue_prompt
  3. 可选覆盖 build_prompt() 自定义提示词构建

最简示例：

    class ReviewerAgent(AgentType):
        name = "reviewer"
        icon = "🔍"
        first_prompt = "reviewer.md"
        continue_prompt = "reviewer_continue.md"
"""
from pathlib import Path
from typing import List

from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig,
)


class AgentType:
    """
    Agent 类型基类。

    子类必须设置的属性:
        name           — 类型名称，如 "worker"
        icon           — 显示图标，如 "👷"
        first_prompt   — 首轮提示词模板文件名
        continue_prompt— 续轮提示词模板文件名

    子类可选覆盖的属性:
        use_ongoing    — 是否使用 ongoing 目录（默认 False）

    子类可选覆盖的方法:
        build_prompt()          — 构建首轮提示词（默认: load_prompt + format）
        build_continue_prompt() — 构建续轮提示词（默认: load_prompt + format）
        build_config()          — 构建 AgentConfig（默认: 标准目录 + HAS_FILES 触发）
        process_task()          — 处理任务（默认: 单轮 run_agent）
    """

    # ---- 子类必须设置 ----
    name: str = ""
    icon: str = "❓"
    first_prompt: str = ""
    continue_prompt: str = ""

    # ---- 子类可选设置 ----
    use_ongoing: bool = False

    # ---- 提示词构建（可覆盖） ----

    def build_prompt(self, task_file: Path, config: AgentConfig) -> str:
        """
        构建首轮提示词。

        默认实现：加载 first_prompt 模板，用标准变量 format。
        子类可覆盖以添加额外变量。
        """
        from secretary.agent_loop import load_prompt
        from secretary.agents import _worker_memory_file
        import secretary.config as cfg

        template = load_prompt(self.first_prompt)
        task_content = task_file.read_text(encoding="utf-8") if task_file.exists() else ""
        report_filename = task_file.name.replace(".md", "") + "-report.md"
        memory_file_path = _worker_memory_file(config.name)

        return template.format(
            base_dir=cfg.BASE_DIR,
            task_file=task_file,
            task_content=task_content,
            report_dir=config.output_dir,
            report_filename=report_filename,
            memory_file_path=memory_file_path,
        )

    def build_continue_prompt_text(self, task_file: Path, config: AgentConfig) -> str:
        """
        构建续轮提示词。

        默认实现：加载 continue_prompt 模板，用标准变量 format。
        """
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

    # ---- 配置构建（通常不需要覆盖） ----

    @property
    def label_template(self) -> str:
        return f"{self.icon} {{name}}"

    @property
    def prompt_template(self) -> str:
        return self.first_prompt

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """
        构建 AgentConfig。

        默认实现：标准目录结构 + HAS_FILES 触发 + UNTIL_FILE_DELETED 终止。
        Boss/Recycler 等需要自定义触发逻辑的类型应覆盖此方法。
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

    # ---- 任务处理（通常不需要覆盖） ----

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理任务。

        默认实现：读取任务 → build_prompt → run_agent → 删除任务文件。
        Worker 覆盖此方法实现多轮对话；Boss/Secretary 等单轮 agent 使用默认实现。
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

        # 单轮 agent：处理完后删除任务文件
        if task_file.exists():
            try:
                task_file.unlink()
            except Exception:
                pass

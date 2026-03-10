"""
Secretary Agent 类型定义与执行逻辑

Secretary 负责任务的分类、归并和分配，特点：
- 目录结构：input_dir (tasks/), output_dir (reports/)
- 触发规则：input_dir 目录有文件时触发
- 单次阻塞调用：scanner 只触发，agent 自行读取任务文件、分配、写 report
- 会话管理：首次调用（无 session）发完整提示词，后续（有 session）发简短续轮提示词
"""
import traceback
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType, prepare_dialog_file, run_agent_with_session


# ============================================================
#  目标管理（goals.md）
# ============================================================

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


# ============================================================
#  提示词构建
# ============================================================

def build_secretary_continue_prompt(task_file: Path, secretary_name: str = "") -> str:
    """续轮提示词 — 简短指令；仅当 known_agents 发生变化时才重新发送"""
    from secretary.agents import build_known_agents_section, known_agents_changed
    ka_section = build_known_agents_section(secretary_name) if (secretary_name and known_agents_changed(secretary_name)) else ""
    template = load_prompt("secretary_continue.md")
    return template.format(
        task_file=task_file,
        known_agents_section=ka_section,
    )


def build_secretary_prompt(task_file: Path, secretary_name: str) -> str:
    """首轮提示词 — 完整角色定义 + 工作流"""
    from secretary.agents import build_known_agents_section

    # 已知 agent（含 worker 状态）
    known_agents_section = build_known_agents_section(secretary_name)
    if not known_agents_section:
        known_agents_section = (
            "## ⚠️ 没有可用的 Agent\n"
            "**当前没有招募任何工人。**\n\n"
            "**你必须拒绝处理这个任务**，并告知用户先用 `study hire` 招募工人。\n"
        )

    # 全局目标（可选）
    goals_text = _load_goals(secretary_name)
    goals_section = "\n## 当前全局目标\n" + goals_text + "\n" if goals_text else ""

    report_filename = task_file.stem + "-report.md"
    from secretary.agents import build_skills_section
    template = load_prompt("secretary_first.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        tasks_dir=str(cfg.AGENTS_DIR / cfg.DEFAULT_WORKER_NAME / "tasks"),
        goals_section=goals_section,
        task_file=task_file,
        report_filename=report_filename,
        reports_dir=cfg.AGENTS_DIR / secretary_name / "reports",
        known_agents_section=known_agents_section,
        skills_section=build_skills_section(secretary_name, cfg.BASE_DIR),
    )


# ============================================================
#  执行函数
# ============================================================

def run_secretary(task_file: Path, verbose: bool = True, secretary_name: str = "study",
                  dialog_file=None) -> bool:
    """运行秘书 Agent 处理任务文件。返回是否成功。"""
    first   = build_secretary_prompt(task_file, secretary_name)
    cont    = build_secretary_continue_prompt(task_file, secretary_name)
    result  = run_agent_with_session(
        secretary_name, first, cont,
        dialog_file=dialog_file, verbose=verbose,
    )
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
        return "secretary_first.md"

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        secretary_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=secretary_dir,
            input_dir=secretary_dir / "tasks",
            processing_dir=secretary_dir / "ongoing",
            output_dir=secretary_dir / "reports",
            logs_dir=secretary_dir / "logs",
            stats_dir=secretary_dir / "stats",
            dialog_dir=secretary_dir / "dialog",
            trigger=TriggerConfig(
                watch_dirs=[secretary_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt="secretary_first.md",
            continue_prompt="secretary_continue.md",
            use_ongoing=False,
            log_file=secretary_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """处理 Secretary 任务 — 单次阻塞调用，agent 自行管理文件"""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] ▶ {task_file.name} ({config.name})")
        dialog_file = prepare_dialog_file(config, task_file.stem)
        try:
            run_secretary(task_file, verbose=verbose,
                          secretary_name=config.name, dialog_file=dialog_file)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ✅ {task_file.name} 完成")
        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ❌ {task_file.name}: {e}")
            traceback.print_exc()

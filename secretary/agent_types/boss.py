"""
Boss Agent 类型定义与执行逻辑

Boss 负责监控指定 worker 的任务队列，在队列为空时生成新任务推进目标，特点：
- 触发规则：监控的 worker 的 tasks/ 和 ongoing/ 都为空时触发（自定义触发函数）
- 终止条件：单次执行后终止
- 处理逻辑：调用 run_boss 生成任务并写入 worker 的 tasks/ 目录
"""
import json
from pathlib import Path
from typing import List

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agents import _worker_tasks_dir, _worker_ongoing_dir, _worker_reports_dir
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType


# ============================================================
#  Boss 执行逻辑（供 scanner 与类型内部使用）
# ============================================================

def _load_boss_goal(boss_dir: Path) -> str:
    """从 boss 目录加载持续目标"""
    goal_file = boss_dir / "goal.md"
    if goal_file.exists():
        content = goal_file.read_text(encoding="utf-8").strip()
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        return "\n".join(lines) if lines else content
    return ""


def _load_boss_worker_name(boss_dir: Path) -> str:
    """从 boss 目录加载监控的 worker 名称"""
    config_file = boss_dir / "config.md"
    if config_file.exists():
        content = config_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "worker:" in line.lower() or "监控的worker:" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    return parts[1].strip()
    return ""


def _get_completed_tasks_summary(worker_name: str) -> str:
    """获取 worker 已完成的任务摘要"""
    worker_dir = cfg.AGENTS_DIR / worker_name
    reports_dir = worker_dir / "reports"
    stats_dir = worker_dir / "stats"
    completed_tasks_info = []
    if stats_dir.exists():
        for stats_file in sorted(stats_dir.glob("*-stats.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            try:
                stats_data = json.loads(stats_file.read_text(encoding="utf-8"))
                task_name = stats_file.stem.replace("-stats", "")
                summary = (stats_data.get("last_response", "")[:200] if isinstance(stats_data, dict) else "")
                completed_tasks_info.append({"name": task_name, "summary": summary})
            except Exception:
                pass
    if not completed_tasks_info and reports_dir.exists():
        for report_file in sorted(reports_dir.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            try:
                content = report_file.read_text(encoding="utf-8")
                title = report_file.stem.replace("-report", "")
                completed_tasks_info.append({"name": title, "summary": content[:300] if len(content) > 300 else content})
            except Exception:
                pass
    if not completed_tasks_info:
        return "暂无已完成的任务。"
    lines = ["已完成的任务："]
    for i, task_info in enumerate(completed_tasks_info, 1):
        lines.append(f"{i}. {task_info['name']}")
        if task_info.get("summary"):
            s = task_info["summary"]
            lines.append(f"   {s[:150] + '...' if len(s) > 150 else s}")
    return "\n".join(lines)


def build_boss_prompt(task_file: Path, boss_dir: Path) -> str:
    """构建 Boss Agent 的提示词"""
    goal = _load_boss_goal(boss_dir)
    worker_name = _load_boss_worker_name(boss_dir)
    if not worker_name:
        return ""
    worker_tasks_dir = _worker_tasks_dir(worker_name)
    worker_ongoing_dir = _worker_ongoing_dir(worker_name)
    worker_reports_dir = _worker_reports_dir(worker_name)
    pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
    ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
    completed_tasks_summary = _get_completed_tasks_summary(worker_name)
    
    # 获取 worker 的 reports 目录信息
    reports_info = ""
    if worker_reports_dir.exists():
        report_files = sorted(worker_reports_dir.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if report_files:
            reports_info = f"\n## Worker 的报告目录\n"
            reports_info += f"报告目录路径: `{worker_reports_dir}`\n"
            reports_info += f"报告文件列表（最近 {min(len(report_files), 10)} 个）:\n"
            for i, report_file in enumerate(report_files[:10], 1):
                reports_info += f"{i}. {report_file.name}\n"
        else:
            reports_info = f"\n## Worker 的报告目录\n"
            reports_info += f"报告目录路径: `{worker_reports_dir}`\n"
            reports_info += "（暂无报告文件）\n"
    else:
        reports_info = f"\n## Worker 的报告目录\n"
        reports_info += f"报告目录路径: `{worker_reports_dir}`\n"
        reports_info += "（目录不存在）\n"
    
    boss_name = boss_dir.name
    from secretary.agents import load_agent_memory, _worker_memory_file
    memory_content = load_agent_memory(boss_name)
    memory_file_path = _worker_memory_file(boss_name)
    memory_section = ""
    if memory_content:
        memory_section = "\n## 你的工作历史（Memory）\n" + memory_content + "\n"
    memory_file_path_section = f"`{memory_file_path}`" if memory_file_path else "未提供"
    template = load_prompt("boss.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        task_file=task_file,
        goal=goal,
        worker_name=worker_name,
        worker_tasks_dir=worker_tasks_dir,
        worker_ongoing_dir=worker_ongoing_dir,
        worker_reports_dir=worker_reports_dir,
        pending_count=pending_count,
        ongoing_count=ongoing_count,
        completed_tasks_summary=completed_tasks_summary,
        reports_info=reports_info,
        memory_section=memory_section,
        memory_file_path=memory_file_path_section,
    )


def run_boss(task_file: Path, boss_dir: Path, verbose: bool = True) -> bool:
    """运行 Boss Agent 处理任务。返回是否成功。"""
    worker_name = _load_boss_worker_name(boss_dir)
    if not worker_name:
        if verbose:
            print("❌ Boss 配置不完整：缺少 worker 名称")
        return False
    worker_tasks_dir = _worker_tasks_dir(worker_name)
    worker_ongoing_dir = _worker_ongoing_dir(worker_name)
    pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
    ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
    if pending_count > 0 or ongoing_count > 0:
        if verbose:
            print(f"ℹ️ Worker '{worker_name}' 队列不为空，无需生成新任务")
        return True
    if verbose:
        goal = _load_boss_goal(boss_dir)
        print(f"📋 Boss Agent 收到任务: 为 worker '{worker_name}' 生成新任务")
        if goal:
            print(f"   持续目标: {goal[:100]}...")
    prompt = build_boss_prompt(task_file, boss_dir)
    if not prompt:
        if verbose:
            print("❌ 无法构建 Boss 提示词：配置不完整")
        return False
    from secretary.settings import get_model
    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.BASE_DIR),
        model=get_model(),
        verbose=verbose,
    )
    if result.success:
        if verbose:
            print(f"\n✅ Boss Agent 完成 (耗时 {result.duration:.1f}s)")
    else:
        if verbose:
            print(f"\n❌ Boss Agent 失败: {result.output[:300]}")
    return result.success


# ============================================================
#  Agent 类型定义
# ============================================================

class BossAgent(AgentType):
    """Boss Agent 类型"""
    
    @property
    def name(self) -> str:
        return "boss"
    
    @property
    def label_template(self) -> str:
        return "👔 {name}"
    
    @property
    def prompt_template(self) -> str:
        return "boss.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """
        构建 Boss 的配置
        
        Boss的触发规则：检查所监视worker的tasks/和ongoing/是否为空
        如果为空，直接触发（不创建触发文件）
        """
        boss_dir = base_dir / "agents" / agent_name
        
        # Boss使用自定义触发函数（需要动态获取worker目录）
        def boss_trigger_fn(config: AgentConfig) -> List[Path]:
            """Boss的触发函数：检查worker的目录是否为空，为空则返回特殊标记触发"""
            worker_name = _load_boss_worker_name(config.base_dir)
            if not worker_name:
                return []
            
            worker_tasks_dir = _worker_tasks_dir(worker_name)
            worker_ongoing_dir = _worker_ongoing_dir(worker_name)
            
            # 检查worker的tasks/和ongoing/是否为空
            pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
            ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
            
            # 如果worker的队列不为空，不触发
            if pending_count > 0 or ongoing_count > 0:
                return []
            
            # 如果为空，返回一个特殊标记（使用一个不存在的路径作为标记，不依赖文件）
            # process_task 会识别这个标记并直接处理，不需要实际文件
            return [config.base_dir / ".boss_trigger_marker"]
        
        return AgentConfig(
            name=agent_name,
            base_dir=boss_dir,
            tasks_dir=boss_dir / "tasks",
            ongoing_dir=boss_dir / "ongoing",
            reports_dir=boss_dir / "reports",
            logs_dir=boss_dir / "logs",
            stats_dir=boss_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[],  # Boss不使用标准目录监视，使用自定义函数
                condition=TriggerCondition.IS_EMPTY,
                create_virtual_file=False,  # 不创建触发文件
                custom_trigger_fn=boss_trigger_fn,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,  # Boss持续运行，处理完任务后继续循环
            first_round_prompt="boss.md",
            use_ongoing=False,  # Boss不需要ongoing目录
            log_file=boss_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理 Boss 任务
        
        流程：
        1. 检查是否是触发标记（.boss_trigger_marker）
        2. 直接调用 run_boss 处理（不需要实际文件，只检查目录是否为空）
        """
        # Boss使用触发标记，直接处理，不需要文件存在
        # task_file 只是标记，实际处理时重新检查目录状态
        run_boss(task_file, config.base_dir, verbose=verbose)


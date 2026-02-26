"""
Boss Agent 类型定义与执行逻辑

Boss 负责监控指定 worker，生成新任务推进目标，特点：
- 目录结构：统一的 input_dir (tasks/), processing_dir (ongoing/), output_dir (reports/)
- 触发规则：
  1. 自己的 input_dir（全局目标，goal.md）有内容，或
  2. 监控的 worker 的 output_dir 出现了新的 reports
- 终止条件：持续运行（UNTIL_FILE_DELETED）
- 处理逻辑：调用 run_boss 生成任务并写入监控的 worker 的 input_dir
- 会话管理：每次都是新会话（单次执行）
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


def _load_boss_max_executions(boss_dir: Path) -> int | None:
    """从 boss 目录加载最大执行次数限制"""
    config_file = boss_dir / "config.md"
    if config_file.exists():
        content = config_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "最大执行次数:" in line or "max_executions:" in line.lower():
                parts = line.split(":", 1)
                if len(parts) > 1:
                    try:
                        return int(parts[1].strip())
                    except ValueError:
                        return None
    return None


def _get_boss_execution_count(boss_dir: Path) -> int:
    """获取 boss 已执行次数（从 stats 目录统计）"""
    stats_dir = boss_dir / "stats"
    if not stats_dir.exists():
        return 0
    # 统计 stats 目录中的任务统计文件数量
    stats_files = list(stats_dir.glob("*-stats.json"))
    return len(stats_files)


def _get_last_processed_report_time(boss_dir: Path) -> float:
    """获取 boss 最后处理报告的时间戳（从 stats 目录）"""
    stats_dir = boss_dir / "stats"
    if not stats_dir.exists():
        return 0.0
    
    # 获取最新的 stats 文件的时间戳
    stats_files = list(stats_dir.glob("*-stats.json"))
    if not stats_files:
        return 0.0
    
    # 返回最新文件的时间戳
    latest_file = max(stats_files, key=lambda p: p.stat().st_mtime)
    return latest_file.stat().st_mtime


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

    w_tasks = _worker_tasks_dir(worker_name)
    w_ongoing = _worker_ongoing_dir(worker_name)
    w_reports = _worker_reports_dir(worker_name)

    # 精简的报告目录信息
    reports_info = f"\n## Worker 报告目录\n路径: `{w_reports}`\n"
    if w_reports.exists():
        rfiles = sorted(w_reports.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        if rfiles:
            reports_info += "\n".join(f"- {r.name}" for r in rfiles) + "\n"

    boss_name = boss_dir.name
    from secretary.agents import _worker_memory_file
    memory_file_path = _worker_memory_file(boss_name)

    template = load_prompt("boss.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        goal=goal,
        worker_name=worker_name,
        worker_tasks_dir=w_tasks,
        worker_ongoing_dir=w_ongoing,
        worker_reports_dir=w_reports,
        boss_reports_dir=boss_dir / "reports",
        completed_tasks_summary=_get_completed_tasks_summary(worker_name),
        reports_info=reports_info,
        memory_file_path=f"`{memory_file_path}`" if memory_file_path else "",
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
        workspace=str(cfg.get_workspace()),
        model=get_model(),
        verbose=verbose,
    )
    
    # 写入 stats 文件以便统计执行次数
    from datetime import datetime
    stats_dir = boss_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_name = f"boss-execution-{timestamp}"
    stats_file = stats_dir / f"{task_name}-stats.json"
    stats_data = {
        "task_name": task_name,
        "success": result.success,
        "duration": result.duration,
        "start_time": datetime.now().isoformat(),
        "worker_name": worker_name,
    }
    stats_file.write_text(json.dumps(stats_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    if result.success:
        if verbose:
            print(f"\n✅ Boss Agent 完成 (耗时 {result.duration:.1f}s)")
    else:
        if verbose:
            print(f"\n❌ Boss Agent 失败: {result.output[:300]}")
    return result.success


class BossAgent(AgentType):
    """Boss Agent — 监控 worker 并生成任务"""
    name = "boss"
    icon = "👔"
    first_prompt = "boss.md"
    continue_prompt = "boss_continue.md"

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        config = super().build_config(base_dir, agent_name)

        def boss_trigger_fn(cfg_: AgentConfig) -> List[Path]:
            worker_name = _load_boss_worker_name(cfg_.base_dir)
            if not worker_name:
                return []
            marker = cfg_.base_dir / ".boss_trigger_marker"
            # goal.md 存在 → 触发
            goal_file = cfg_.base_dir / "goal.md"
            if goal_file.exists() and goal_file.stat().st_size > 0:
                return [marker]
            # worker reports 有新报告 → 触发
            w_reports = _worker_reports_dir(worker_name)
            if w_reports.exists():
                cutoff = _get_last_processed_report_time(cfg_.base_dir)
                if any(r.stat().st_mtime > cutoff for r in w_reports.glob("*-report.md")):
                    return [marker]
            return []

        config.trigger = TriggerConfig(custom_trigger_fn=boss_trigger_fn)
        return config

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        run_boss(task_file, config.base_dir, verbose=verbose)


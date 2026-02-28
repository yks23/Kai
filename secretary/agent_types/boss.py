"""
Boss Agent — 监控关联 agent，生成任务推进目标

触发条件（二选一即触发）：
  1. 自己的 tasks/ 有新文件 → 读取并执行（全局任务/目标设定）
  2. 关联 agent 的 reports/ 出现新报告 → 读取报告，生成后续任务

化被动为主动：boss 不再等待 worker 队列为空，而是主动响应任务写入和报告产出。
"""
import json
from pathlib import Path
from typing import List
from datetime import datetime

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agents import _worker_tasks_dir, _worker_reports_dir
from secretary.agent_config import AgentConfig, TriggerConfig
from secretary.agent_types.base import AgentType


# ---- 配置读取 ----

def _load_boss_goal(boss_dir: Path) -> str:
    goal_file = boss_dir / "goal.md"
    if goal_file.exists():
        content = goal_file.read_text(encoding="utf-8").strip()
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
        return "\n".join(lines) if lines else content
    return ""


def _load_boss_worker_name(boss_dir: Path) -> str:
    config_file = boss_dir / "config.md"
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            if "worker:" in line.lower() or "监控的worker:" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    return parts[1].strip()
    return ""


def _get_last_run_time(boss_dir: Path) -> float:
    """boss 上次执行的时间（从 stats/ 最新文件推断）"""
    stats_dir = boss_dir / "stats"
    if not stats_dir.exists():
        return 0.0
    files = list(stats_dir.glob("*-stats.json"))
    if not files:
        return 0.0
    return max(f.stat().st_mtime for f in files)


def _get_completed_tasks_summary(worker_name: str) -> str:
    """获取 worker 最近完成的任务摘要"""
    reports_dir = cfg.AGENTS_DIR / worker_name / "reports"
    if not reports_dir.exists():
        return "暂无已完成的任务。"
    rfiles = sorted(reports_dir.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if not rfiles:
        return "暂无已完成的任务。"
    lines = ["最近完成的任务："]
    for i, r in enumerate(rfiles, 1):
        try:
            content = r.read_text(encoding="utf-8")[:300]
            lines.append(f"{i}. {r.stem.replace('-report', '')}\n   {content[:150]}")
        except Exception:
            lines.append(f"{i}. {r.stem}")
    return "\n".join(lines)


# ---- 提示词 ----

def build_boss_prompt(task_file: Path, boss_dir: Path) -> str:
    from secretary.agent_types.base import _build_known_agents_section
    from secretary.agents import _worker_memory_file

    goal = _load_boss_goal(boss_dir)
    boss_name = boss_dir.name
    known_section = _build_known_agents_section(boss_name)

    trigger_info = ""
    if task_file.exists():
        trigger_content = task_file.read_text(encoding="utf-8").strip()
        if task_file.name.endswith("-report.md"):
            trigger_info = f"\n## 触发来源：Agent 新报告\n\n---\n{trigger_content}\n---\n"
        else:
            trigger_info = f"\n## 触发来源：新任务\n\n---\n{trigger_content}\n---\n"

    worker_name = _load_boss_worker_name(boss_dir)
    memory_file_path = _worker_memory_file(boss_name)

    template = load_prompt("boss.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        goal=goal,
        known_agents_section=known_section,
        boss_reports_dir=boss_dir / "reports",
        completed_tasks_summary=_get_completed_tasks_summary(worker_name) if worker_name else "",
        trigger_info=trigger_info,
        memory_file_path=f"`{memory_file_path}`" if memory_file_path else "",
    )


# ---- 执行 ----

def run_boss(task_file: Path, boss_dir: Path, verbose: bool = True) -> bool:
    worker_name = _load_boss_worker_name(boss_dir)
    if not worker_name:
        if verbose:
            print("❌ Boss 配置不完整：缺少 worker 名称")
        return False

    if verbose:
        goal = _load_boss_goal(boss_dir)
        src = "Worker 报告" if task_file.name.endswith("-report.md") else "新任务"
        print(f"📋 Boss 触发: {src} → 为 '{worker_name}' 生成任务")
        if goal:
            print(f"   目标: {goal[:80]}…")

    prompt = build_boss_prompt(task_file, boss_dir)
    if not prompt:
        return False

    from secretary.settings import get_model
    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.get_workspace()),
        model=get_model(),
        verbose=verbose,
    )

    # 记录执行
    stats_dir = boss_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    (stats_dir / f"boss-{ts}-stats.json").write_text(
        json.dumps({"success": result.success, "duration": result.duration,
                     "trigger": task_file.name, "worker": worker_name}, ensure_ascii=False),
        encoding="utf-8",
    )

    if verbose:
        status = "✅" if result.success else "❌"
        print(f"   {status} Boss 完成 ({result.duration:.1f}s)")
    return result.success


# ---- Agent 类型定义 ----

class BossAgent(AgentType):
    """Boss Agent — 两种触发：tasks/ 写入 或 关联 agent 新报告"""
    name = "boss"
    icon = "👔"
    first_prompt = "boss.md"
    continue_prompt = "boss_continue.md"

    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        config = super().build_config(base_dir, agent_name)

        def boss_trigger_fn(cfg_: AgentConfig) -> List[Path]:
            # 触发 1: 自己的 tasks/ 有新文件
            if cfg_.input_dir.exists():
                tasks = sorted(cfg_.input_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
                if tasks:
                    return [tasks[0]]

            # 触发 2: 关联 agent 的 reports/ 有新报告
            from secretary.agents import get_worker
            info = get_worker(cfg_.name)
            known = info.get("known_agents", []) if info else []
            cutoff = _get_last_run_time(cfg_.base_dir)
            for peer in known:
                rdir = _worker_reports_dir(peer)
                if rdir.exists():
                    for r in sorted(rdir.glob("*-report.md"), key=lambda p: p.stat().st_mtime):
                        if r.stat().st_mtime > cutoff:
                            return [r]
            return []

        config.trigger = TriggerConfig(custom_trigger_fn=boss_trigger_fn)
        return config

    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        run_boss(task_file, config.base_dir, verbose=verbose)
        # 如果触发来源是自己 tasks/ 的文件，处理后删除
        if task_file.exists() and str(config.input_dir) in str(task_file.parent):
            try:
                task_file.unlink()
            except Exception:
                pass

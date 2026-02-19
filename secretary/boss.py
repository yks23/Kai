"""
Boss Agent — 监控指定 worker 的任务队列，在队列为空时生成新任务推进目标

工作逻辑:
  1. 检查指定 worker 的 tasks/ 和 ongoing/ 目录是否为空
  2. 如果为空，调用 Agent 生成新任务
  3. 将生成的任务写入 worker 的 tasks/ 目录
  4. 使用统一的扫描器框架
"""
import json
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent
from secretary.agents import _worker_tasks_dir, _worker_ongoing_dir


def _load_boss_goal(boss_dir: Path) -> str:
    """从boss目录加载持续目标"""
    goal_file = boss_dir / "goal.md"
    if goal_file.exists():
        content = goal_file.read_text(encoding="utf-8").strip()
        # 提取目标内容（跳过标题）
        lines = content.splitlines()
        goal_lines = []
        for line in lines:
            if line.strip() and not line.strip().startswith("#"):
                goal_lines.append(line.strip())
        return "\n".join(goal_lines) if goal_lines else content
    return ""


def _load_boss_worker_name(boss_dir: Path) -> str:
    """从boss目录加载监控的worker名称"""
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
    """获取worker已完成的任务摘要"""
    worker_dir = cfg.AGENTS_DIR / worker_name
    reports_dir = worker_dir / "reports"
    stats_dir = worker_dir / "stats"
    
    completed_tasks_info = []
    
    # 从stats目录读取统计信息
    if stats_dir.exists():
        for stats_file in sorted(stats_dir.glob("*-stats.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            try:
                stats_data = json.loads(stats_file.read_text(encoding="utf-8"))
                task_name = stats_file.stem.replace("-stats", "")
                summary = stats_data.get("last_response", "")[:200] if isinstance(stats_data, dict) else ""
                completed_tasks_info.append({"name": task_name, "summary": summary})
            except Exception:
                pass
    
    # 从reports目录读取报告
    if not completed_tasks_info and reports_dir.exists():
        for report_file in sorted(reports_dir.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
            try:
                content = report_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = ""
                for line in lines[:10]:
                    if line.strip().startswith("#"):
                        title = line.strip().lstrip("#").strip()
                        break
                if not title:
                    title = report_file.stem.replace("-report", "")
                completed_tasks_info.append({"name": title, "summary": content[:300] if len(content) > 300 else content})
            except Exception:
                pass
    
    if not completed_tasks_info:
        return "暂无已完成的任务。"
    
    summary_lines = ["已完成的任务："]
    for i, task_info in enumerate(completed_tasks_info, 1):
        summary_lines.append(f"{i}. {task_info['name']}")
        if task_info.get('summary'):
            s = task_info['summary']
            summary_lines.append(f"   {s[:150] + '...' if len(s) > 150 else s}")
    
    return "\n".join(summary_lines)


def build_boss_prompt(task_file: Path, boss_dir: Path) -> str:
    """构建Boss Agent的提示词"""
    goal = _load_boss_goal(boss_dir)
    worker_name = _load_boss_worker_name(boss_dir)
    boss_name = boss_dir.name  # 从目录名获取boss名称
    
    if not worker_name:
        return ""  # 配置不完整
    
    worker_tasks_dir = _worker_tasks_dir(worker_name)
    worker_ongoing_dir = _worker_ongoing_dir(worker_name)
    
    # 统计任务数量
    pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
    ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
    
    completed_tasks_summary = _get_completed_tasks_summary(worker_name)
    
    # 加载boss的memory
    from secretary.agents import load_agent_memory, _worker_memory_file
    memory_content = load_agent_memory(boss_name)
    memory_file_path = _worker_memory_file(boss_name)
    memory_section = ""
    if memory_content:
        memory_section = (
            "\n## 你的工作历史（Memory）\n"
            "以下是你的工作总结，包含你之前生成的任务和工作经验：\n\n"
            f"{memory_content}\n"
        )
    memory_file_path_section = f"`{memory_file_path}`" if memory_file_path else "未提供"
    
    template = load_prompt("boss.md")
    return template.format(
        base_dir=cfg.BASE_DIR,
        task_file=task_file,
        goal=goal,
        worker_name=worker_name,
        worker_tasks_dir=worker_tasks_dir,
        worker_ongoing_dir=worker_ongoing_dir,
        pending_count=pending_count,
        ongoing_count=ongoing_count,
        completed_tasks_summary=completed_tasks_summary,
        memory_section=memory_section,
        memory_file_path=memory_file_path_section,
    )


def run_boss(task_file: Path, boss_dir: Path, verbose: bool = True) -> bool:
    """
    运行Boss Agent处理任务
    Boss不需要自己的tasks目录，它根据target生成任务并写入worker的tasks目录
    
    Returns:
        是否成功
    """
    worker_name = _load_boss_worker_name(boss_dir)
    if not worker_name:
        if verbose:
            print(f"❌ Boss配置不完整：缺少worker名称")
        return False
    
    # 检查worker的队列状态（触发规则已经在scanner中检查，这里再次确认）
    worker_tasks_dir = _worker_tasks_dir(worker_name)
    worker_ongoing_dir = _worker_ongoing_dir(worker_name)
    
    pending_count = len(list(worker_tasks_dir.glob("*.md"))) if worker_tasks_dir.exists() else 0
    ongoing_count = len(list(worker_ongoing_dir.glob("*.md"))) if worker_ongoing_dir.exists() else 0
    
    # 如果队列不为空，不需要生成任务（双重检查，防止并发问题）
    if pending_count > 0 or ongoing_count > 0:
        if verbose:
            print(f"ℹ️  Worker '{worker_name}' 队列不为空（待处理: {pending_count}, 执行中: {ongoing_count}），无需生成新任务")
        # 如果是虚拟触发文件，删除它（这样下次循环时如果队列为空，会重新创建触发文件）
        if task_file.name == ".boss_trigger" and task_file.exists():
            task_file.unlink()
        return True
    
    if verbose:
        print(f"📋 Boss Agent 收到任务: 为 worker '{worker_name}' 生成新任务")
        goal = _load_boss_goal(boss_dir)
        if goal:
            print(f"   持续目标: {goal[:100]}...")
    
    prompt = build_boss_prompt(task_file, boss_dir)
    if not prompt:
        if verbose:
            print(f"❌ 无法构建Boss提示词：配置不完整")
        return False
    
    # 从设置中获取模型
    from secretary.settings import get_model
    model = get_model()
    
    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.BASE_DIR),
        model=model,
        verbose=verbose,
    )
    
    boss_name = boss_dir.name
    if result.success:
        if verbose:
            print(f"\n✅ Boss Agent 完成 (耗时 {result.duration:.1f}s)")
        # 注意：memory的更新由agent自己决定，不在这里自动更新
        # 删除虚拟触发文件（如果是）
        if task_file.name == ".boss_trigger" and task_file.exists():
            task_file.unlink()
    else:
        if verbose:
            print(f"\n❌ Boss Agent 失败: {result.output[:300]}")
        # 即使失败，也删除虚拟触发文件，以便下次循环时重新触发
        if task_file.name == ".boss_trigger" and task_file.exists():
            task_file.unlink()
    
    return result.success


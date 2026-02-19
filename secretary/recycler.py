"""
回收者 Agent — 审查 Worker 的完成报告，判断任务是否真正完成。
使用 agent_loop.run_loop 统一循环。

## Recycle 触发条件与 Unsolved 记录规则

- **触发条件**: 回收者扫描 report/ 目录下的 *-report.md 文件，对每份报告调用
  Agent 进行审查；审查标准为「任务是否真正完成」（文件是否存在、
  代码是否合理、是否有遗漏等）。

- **未满足则记入 unsolved**: 当审查判定为「未完成」时，必须在 unsolved 中记录该事件：
  1. 将报告文件（及关联的 *-stats.md、*-stats.json）移动到 unsolved-report/ 目录；
  2. 在 unsolved-report/ 下存在 *-unsolved-reason.md 记录未完成原因与改进方向
     （若 Agent 未生成，则代码会写入默认原因文件以保证「记录」完整）；
  3. 调用秘书 Agent 根据改进方向重新提交任务。

工作逻辑:
  1. 扫描 report/ 中的 *-report.md 文件
  2. 对每份报告，调用 Agent 进行审查
  3. Agent 审查内容: 检查文件是否存在、代码是否合理、是否有遗漏
  4. 判定结果:
     - ✅ 已完成 → 将报告(+统计文件) 移动到 solved-report/
     - ❌ 未完成 → 将报告(+统计文件) 移动到 unsolved-report/
                   → 确保存在 unsolved-reason 记录
                   → 调用秘书 Agent 重新提交任务
  5. 每 2 分钟循环一次

提示词模板:
  prompts/recycler.md
"""
import shutil
from pathlib import Path
from datetime import datetime

from secretary.config import (
    BASE_DIR, AGENTS_DIR,
    RECYCLER_INTERVAL,
)
from secretary.agent_loop import run_loop, load_prompt
from secretary.agent_runner import run_agent


def _find_report_files() -> list[Path]:
    """
    从所有agent的reports目录中找到所有报告文件 (*-report.md)
    返回格式: (report_file, agent_name) 的列表，但为了兼容性，只返回report_file
    """
    reports = []
    
    # 扫描所有agent目录
    if not AGENTS_DIR.exists():
        return []
    
    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir():
            continue
        # 跳过非agent目录（如果有）
        if agent_dir.name.startswith('.'):
            continue
        
        # 检查是否有reports目录
        reports_dir = agent_dir / "reports"
        if reports_dir.exists():
            agent_reports = [f for f in reports_dir.glob("*-report.md") if f.is_file()]
            reports.extend(agent_reports)
    
    return sorted(reports, key=lambda p: p.stat().st_mtime)


def _get_related_files(report_file: Path) -> list[Path]:
    """
    获取与报告关联的统计文件
    例: agents/<name>/reports/foo-report.md → agents/<name>/stats/foo-stats.md
    """
    base_name = report_file.stem  # e.g. "foo-report"
    task_name = base_name.replace("-report", "")

    related = []
    
    # 从报告文件所在目录推断agent目录
    # 如果报告在 agents/<name>/reports/ 下，统计文件在 agents/<name>/stats/ 下
    if "agents" in str(report_file) and "reports" in str(report_file):
        # 提取agent目录路径
        parts = report_file.parts
        try:
            agents_idx = parts.index("agents")
            if agents_idx + 1 < len(parts):
                agent_name = parts[agents_idx + 1]
                agent_dir = AGENTS_DIR / agent_name
                stats_dir = agent_dir / "stats"
                for suffix in ["-stats.md", "-stats.json"]:
                    f = stats_dir / f"{task_name}{suffix}"
                    if f.exists():
                        related.append(f)
        except (ValueError, IndexError):
            pass
    
    return related


def _get_recycler_dirs() -> tuple[Path, Path]:
    """获取recycler的solved和unsolved目录"""
    recycler_dir = AGENTS_DIR / "recycler"
    solved_dir = recycler_dir / "solved"
    unsolved_dir = recycler_dir / "unsolved"
    solved_dir.mkdir(parents=True, exist_ok=True)
    unsolved_dir.mkdir(parents=True, exist_ok=True)
    return solved_dir, unsolved_dir


def build_recycler_prompt(report_file: Path, recycler_name: str = "recycler") -> str:
    """
    构建回收者 Agent 的提示词
    """
    report_content = report_file.read_text(encoding="utf-8")

    # 查找统计文件 (在对应agent的stats目录下)
    task_name = report_file.stem.replace("-report", "")
    
    # 从报告文件位置推断stats目录
    stats_dir = None
    if "agents" in str(report_file) and "reports" in str(report_file):
        parts = report_file.parts
        try:
            agents_idx = parts.index("agents")
            if agents_idx + 1 < len(parts):
                agent_name = parts[agents_idx + 1]
                agent_dir = AGENTS_DIR / agent_name
                stats_dir = agent_dir / "stats"
        except (ValueError, IndexError):
            pass
    
    if stats_dir is None:
        # 如果无法推断，使用报告文件所在agent的stats目录
        # 从报告文件路径提取agent名称
        parts = report_file.parts
        try:
            agents_idx = parts.index("agents")
            if agents_idx + 1 < len(parts):
                agent_name = parts[agents_idx + 1]
                agent_dir = AGENTS_DIR / agent_name
                stats_dir = agent_dir / "stats"
        except (ValueError, IndexError):
            # 如果还是无法推断，使用recycler自己的stats目录
            stats_dir = AGENTS_DIR / "recycler" / "stats"
    
    stats_md = stats_dir / f"{task_name}-stats.md"
    stats_json = stats_dir / f"{task_name}-stats.json"

    stats_section = ""
    if stats_md.exists():
        stats_content = stats_md.read_text(encoding="utf-8")
        stats_section = (
            f"## 执行统计数据\n"
            f"以下是 Scanner 记录的调用统计:\n\n"
            f"---\n{stats_content}\n---\n"
        )
    else:
        stats_section = "(无统计数据 — 此任务在统计功能上线前完成)\n"

    # 使用recycler的solved和unsolved目录
    solved_dir, unsolved_dir = _get_recycler_dirs()
    reason_filename = f"{task_name}-unsolved-reason.md"
    
    # 加载recycler的memory
    from secretary.agents import load_agent_memory, _worker_memory_file
    memory_content = load_agent_memory(recycler_name)
    memory_file_path = _worker_memory_file(recycler_name)
    memory_section = ""
    if memory_content:
        memory_section = (
            "\n## 你的工作历史（Memory）\n"
            "以下是你的工作总结，包含你之前审查的任务和经验：\n\n"
            f"{memory_content}\n"
        )
    memory_file_path_section = f"`{memory_file_path}`" if memory_file_path else "未提供"

    template = load_prompt("recycler.md")
    return template.format(
        base_dir=BASE_DIR,
        report_file=report_file,
        report_content=report_content,
        stats_section=stats_section,
        solved_dir=solved_dir,
        unsolved_dir=unsolved_dir,
        stats_md=stats_md,
        stats_json=stats_json,
        memory_section=memory_section,
        memory_file_path=memory_file_path_section,
        reason_filename=reason_filename,
    )


def process_report(report_file: Path, recycler_config=None, verbose: bool = True) -> bool:
    """
    对一份报告调用回收者 Agent 进行审查

    Returns:
        True = 已处理 (无论判定结果), False = 处理失败
    """
    task_name = report_file.stem.replace("-report", "")
    recycler_name = recycler_config.name if recycler_config else "recycler"

    # 先保存报告原文，稍后可能用于重新提交
    report_content = report_file.read_text(encoding="utf-8") if report_file.exists() else ""

    if verbose:
        print(f"\n🔍 回收者审查: {report_file.name}")

    prompt = build_recycler_prompt(report_file, recycler_name=recycler_name)

    result = run_agent(
        prompt=prompt,
        workspace=str(BASE_DIR),
        verbose=verbose,
    )

    if not result.success:
        print(f"   ❌ 回收者 Agent 调用失败: {result.output[:200]}")
        return False

    # 判断 Agent 的决策: 检查文件被移到了哪里
    # Agent 会自行执行 mv 命令来移动文件
    solved_dir, unsolved_dir = _get_recycler_dirs()
    report_gone = not report_file.exists()
    in_solved = (solved_dir / report_file.name).exists()
    in_unsolved = (unsolved_dir / report_file.name).exists()

    if in_solved:
        # 确保统计文件也被移走
        _move_related_stats(report_file, solved_dir)
        print(f"   ✅ 判定: 已完成 → {solved_dir.name}/")
        # 注意：memory的更新由agent自己决定，不在这里自动更新
        return True
    elif in_unsolved:
        # 确保统计文件也被移走
        _move_related_stats(report_file, unsolved_dir)
        # 未满足完成条件时，必须在 unsolved 中记录该事件（含原因文件）
        _ensure_unsolved_reason_record(task_name, unsolved_dir)
        print(f"   ❌ 判定: 未完成 → {unsolved_dir.name}/")
        # 注意：memory的更新由agent自己决定，不在这里自动更新
        # 调用秘书重新提交任务，附带改进方向
        _resubmit_task(task_name, report_content=report_content, verbose=verbose)
        return True
    elif report_gone:
        # Agent 移动了但我们不确定去了哪里
        print(f"   ⚠️  报告已被移动（Agent 已处理）")
        return True
    else:
        # Agent 没有移动文件 — 可能出了问题，手动兜底
        print(f"   ⚠️  Agent 未移动报告文件，尝试根据输出判断...")
        return _fallback_judgment(report_file, result.output, task_name,
                                  report_content=report_content, verbose=verbose)


def _move_related_stats(report_file: Path, dest_dir: Path):
    """确保 stats/ 中的关联文件也移到目标目录（Agent 可能只移了报告没移 stats）"""
    related = _get_related_files(report_file)
    for f in related:
        dest = dest_dir / f.name
        if not dest.exists():
            try:
                shutil.move(str(f), str(dest))
            except Exception:
                pass  # Agent 可能已经移了


def _ensure_unsolved_reason_record(task_name: str, unsolved_dir: Path | None = None, reason_content: str | None = None) -> None:
    """
    确保 unsolved 中对该任务有明确记录：若不存在 *-unsolved-reason.md 则写入默认内容。
    满足「未满足条件时，将对应事件记录到 unsolved」的完整语义。
    """
    if unsolved_dir is None:
        _, unsolved_dir = _get_recycler_dirs()
    unsolved_dir.mkdir(parents=True, exist_ok=True)
    reason_file = unsolved_dir / f"{task_name}-unsolved-reason.md"
    if reason_file.exists():
        return
    default = (
        "# 未完成原因\n\n"
        "（回收者判定为未完成；若未提供具体原因，请查看同目录下的报告文件。）\n\n"
        "# 下一步改进方向\n\n"
        "请根据报告内容与实际情况，明确需要补充或修正的部分。\n"
    )
    reason_file.write_text(reason_content if reason_content else default, encoding="utf-8")


def _fallback_judgment(report_file: Path, agent_output: str, task_name: str,
                       report_content: str, verbose: bool) -> bool:
    """
    当 Agent 没有移动文件时，根据输出文本做兜底判断
    """
    # 从输出中判断
    is_solved = "[判定: ✅" in agent_output or "已完成" in agent_output
    is_unsolved = "[判定: ❌" in agent_output or "未完成" in agent_output

    related = _get_related_files(report_file)
    solved_dir, unsolved_dir = _get_recycler_dirs()

    if is_unsolved:
        # 移动到 unsolved，并确保在 unsolved 中有记录（原因文件）
        dest = unsolved_dir / report_file.name
        unsolved_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(report_file), str(dest))
        for f in related:
            shutil.move(str(f), str(unsolved_dir / f.name))
        # 从 Agent 输出中尝试提取简要原因作为记录
        reason_from_output = ""
        if "[判定: ❌" in agent_output or "未完成" in agent_output:
            for line in agent_output.splitlines():
                line = line.strip()
                if "原因:" in line or "未完成" in line:
                    reason_from_output = (
                        "# 未完成原因\n\n" + line + "\n\n"
                        "# 下一步改进方向\n\n请根据上述原因与报告内容，给出可执行的改进步骤。\n"
                    )
                    break
        _ensure_unsolved_reason_record(task_name, unsolved_dir=unsolved_dir, reason_content=reason_from_output or None)
        if verbose:
            print(f"   ❌ 兜底判定: 未完成 → {unsolved_dir.name}/")
        _resubmit_task(task_name, report_content=report_content, verbose=verbose)
        return True
    elif is_solved:
        # 移动到 solved
        dest = solved_dir / report_file.name
        shutil.move(str(report_file), str(dest))
        for f in related:
            shutil.move(str(f), str(solved_dir / f.name))
        if verbose:
            print(f"   ✅ 兜底判定: 已完成 → {solved_dir.name}/")
        return True
    else:
        # 无法判断 — 保留在 report/ 中，下次再审
        if verbose:
            print(f"   ⚠️  无法判断，保留在 report/ 中待下次审查")
        return False


def _resubmit_task(task_name: str, report_content: str = "", verbose: bool = True):
    """
    调用秘书 Agent 重新提交未完成的任务，附带回收者的改进方向
    支持多secretary选择
    """
    from secretary.agents import list_workers
    from secretary.cli import _write_kai_task, _select_secretary, _cli_name

    # 读取 unsolved 原因 + 改进方向
    _, unsolved_dir = _get_recycler_dirs()
    reason_file = unsolved_dir / f"{task_name}-unsolved-reason.md"
    reason = ""
    if reason_file.exists():
        reason = reason_file.read_text(encoding="utf-8").strip()

    # 构建富含上下文的重新提交请求
    parts = [
        f"之前的任务 `{task_name}` 经回收者审查判定为**未完成**，需要重新提交。\n",
    ]

    if reason:
        parts.append(f"## 回收者的审查意见与改进方向\n\n{reason}\n")

    if report_content:
        # 截断过长的报告
        trimmed = report_content if len(report_content) <= 2000 else report_content[:2000] + "\n...(已截断)"
        parts.append(f"## 上一轮 Worker 的完成报告（供参考）\n\n{trimmed}\n")

    parts.append(
        "## 要求\n"
        "请根据回收者的改进方向重新创建任务。新任务应:\n"
        "1. 明确指出上一轮遗漏或未完成的部分\n"
        "2. 包含回收者给出的具体改进方向作为行动指引\n"
        "3. 让下一轮 Worker 知道之前已经做了什么，避免重复工作\n"
    )

    resubmit_request = "\n".join(parts)

    if verbose:
        print(f"   📨 重新提交任务: {task_name}")
        if reason:
            # 提取改进方向的摘要 (取前3行)
            direction_lines = [l.strip() for l in reason.splitlines() if l.strip()]
            preview = direction_lines[:3]
            print(f"   📋 改进方向: {' | '.join(preview)}")

    # 选择secretary（支持多secretary场景）
    secretaries = [w for w in list_workers() if w.get("type") == "secretary"]
    if len(secretaries) == 0:
        if verbose:
            print(f"   ⚠️ 没有可用的secretary agent，无法重新提交任务")
        return
    elif len(secretaries) == 1:
        secretary_name = secretaries[0]["name"]
    else:
        # 多个secretary，使用第一个（或可以改进为让用户选择）
        secretary_name = secretaries[0]["name"]
        if verbose:
            print(f"   ℹ️ 检测到多个secretary，使用: {secretary_name}")
    
    # 将重新提交请求写入secretary的tasks目录
    _write_kai_task(resubmit_request, min_time=0, secretary_name=secretary_name)
    if verbose:
        print(f"   ✅ 已提交到 {secretary_name} 的任务队列")


def run_recycler_once(verbose: bool = True) -> int:
    """
    执行一次回收检查

    Returns:
        处理的报告数量
    """
    reports = _find_report_files()
    if not reports:
        if verbose:
            print("♻️  回收者: report/ 中没有待审查的报告")
        return 0

    if verbose:
        print(f"\n♻️  回收者: 发现 {len(reports)} 份报告待审查")

    processed = 0
    for report_file in reports:
        if process_report(report_file, verbose=verbose):
            processed += 1

    return processed


def run_recycler(once: bool = False, verbose: bool = True):
    """
    运行回收者循环（使用 agent_loop.run_loop）。
    """
    solved_dir, unsolved_dir = _get_recycler_dirs()
    print("=" * 60)
    print("♻️  Recycler Agent 启动")
    print(f"   扫描目录: 所有agent的reports/目录")
    print(f"   已解决: {solved_dir}")
    print(f"   未解决: {unsolved_dir}")
    print(f"   检查间隔: {RECYCLER_INTERVAL}s ({RECYCLER_INTERVAL // 60}分钟)")
    print(f"   模式: {'单次' if once else '持续运行'}")
    print("=" * 60)

    def process_fn(report_file: Path):
        process_report(report_file, verbose=verbose)

    def on_idle():
        if verbose:
            print("♻️  Recycler: 没有待审查的报告")
            next_ts = datetime.now().strftime("%H:%M:%S")
            print(f"💤 [{next_ts}] 下次检查在 {RECYCLER_INTERVAL}s 后...")

    run_loop(
        trigger_fn=_find_report_files,
        process_fn=process_fn,
        interval_sec=RECYCLER_INTERVAL,
        once=once,
        label="回收者",
        verbose=verbose,
        on_idle=on_idle,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回收者 — 审查任务报告")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    args = parser.parse_args()
    run_recycler(once=args.once, verbose=not args.quiet)


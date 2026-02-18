"""
回收者 Agent — 审查 Worker 的完成报告，判断任务是否真正完成

## Recycle 触发条件与 Unsolved 记录规则

- **触发条件**: 回收者扫描 report/ 目录下的 *-report.md 文件，对每份报告调用
  Cursor Agent 进行审查；审查标准为「任务是否真正完成」（文件是否存在、
  代码是否合理、是否有遗漏等）。

- **未满足则记入 unsolved**: 当审查判定为「未完成」时，必须在 unsolved 中记录该事件：
  1. 将报告文件（及关联的 *-stats.md、*-stats.json）移动到 unsolved-report/ 目录；
  2. 在 unsolved-report/ 下存在 *-unsolved-reason.md 记录未完成原因与改进方向
     （若 Agent 未生成，则代码会写入默认原因文件以保证「记录」完整）；
  3. 调用秘书 Agent 根据改进方向重新提交任务。

工作逻辑:
  1. 扫描 report/ 中的 *-report.md 文件
  2. 对每份报告，调用 Cursor Agent 进行审查
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
import time
from pathlib import Path
from datetime import datetime

from secretary.config import (
    BASE_DIR, REPORT_DIR, STATS_DIR, SOLVED_DIR, UNSOLVED_DIR,
    PROMPTS_DIR, RECYCLER_INTERVAL,
)
from secretary.agent_runner import run_agent


def _load_prompt_template() -> str:
    """加载回收者提示词模板"""
    tpl_path = PROMPTS_DIR / "recycler.md"
    return tpl_path.read_text(encoding="utf-8")


def _find_report_files() -> list[Path]:
    """
    在 report/ 中找到所有 Worker 报告文件 (*-report.md)
    排除统计文件 (*-stats.md)
    """
    if not REPORT_DIR.exists():
        return []
    reports = [
        f for f in REPORT_DIR.glob("*-report.md")
        if f.is_file()
    ]
    return sorted(reports, key=lambda p: p.stat().st_mtime)


def _get_related_files(report_file: Path) -> list[Path]:
    """
    获取与报告关联的统计文件 (在 stats/ 目录下)
    例: foo-report.md → stats/foo-stats.md, stats/foo-stats.json
    """
    base_name = report_file.stem  # e.g. "foo-report"
    task_name = base_name.replace("-report", "")

    related = []
    for suffix in ["-stats.md", "-stats.json"]:
        f = STATS_DIR / f"{task_name}{suffix}"
        if f.exists():
            related.append(f)
    return related


def build_recycler_prompt(report_file: Path) -> str:
    """
    构建回收者 Agent 的提示词
    """
    report_content = report_file.read_text(encoding="utf-8")

    # 查找统计文件 (在 stats/ 目录下)
    task_name = report_file.stem.replace("-report", "")
    stats_md = STATS_DIR / f"{task_name}-stats.md"
    stats_json = STATS_DIR / f"{task_name}-stats.json"

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

    reason_filename = f"{task_name}-unsolved-reason.md"

    template = _load_prompt_template()
    return template.format(
        base_dir=BASE_DIR,
        report_file=report_file,
        report_content=report_content,
        stats_section=stats_section,
        solved_dir=SOLVED_DIR,
        unsolved_dir=UNSOLVED_DIR,
        stats_md=stats_md,
        stats_json=stats_json,
        reason_filename=reason_filename,
    )


def process_report(report_file: Path, verbose: bool = True) -> bool:
    """
    对一份报告调用回收者 Agent 进行审查

    Returns:
        True = 已处理 (无论判定结果), False = 处理失败
    """
    task_name = report_file.stem.replace("-report", "")

    # 先保存报告原文，稍后可能用于重新提交
    report_content = report_file.read_text(encoding="utf-8") if report_file.exists() else ""

    if verbose:
        print(f"\n🔍 回收者审查: {report_file.name}")

    prompt = build_recycler_prompt(report_file)

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
    report_gone = not report_file.exists()
    in_solved = (SOLVED_DIR / report_file.name).exists()
    in_unsolved = (UNSOLVED_DIR / report_file.name).exists()

    if in_solved:
        # 确保统计文件也被移走
        _move_related_stats(report_file, SOLVED_DIR)
        print(f"   ✅ 判定: 已完成 → solved-report/")
        return True
    elif in_unsolved:
        # 确保统计文件也被移走
        _move_related_stats(report_file, UNSOLVED_DIR)
        # 未满足完成条件时，必须在 unsolved 中记录该事件（含原因文件）
        _ensure_unsolved_reason_record(task_name)
        print(f"   ❌ 判定: 未完成 → unsolved-report/")
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


def _ensure_unsolved_reason_record(task_name: str, reason_content: str | None = None) -> None:
    """
    确保 unsolved 中对该任务有明确记录：若不存在 *-unsolved-reason.md 则写入默认内容。
    满足「未满足条件时，将对应事件记录到 unsolved」的完整语义。
    """
    UNSOLVED_DIR.mkdir(parents=True, exist_ok=True)
    reason_file = UNSOLVED_DIR / f"{task_name}-unsolved-reason.md"
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

    if is_unsolved:
        # 移动到 unsolved，并确保在 unsolved 中有记录（原因文件）
        dest = UNSOLVED_DIR / report_file.name
        UNSOLVED_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(report_file), str(dest))
        for f in related:
            shutil.move(str(f), str(UNSOLVED_DIR / f.name))
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
        _ensure_unsolved_reason_record(task_name, reason_content=reason_from_output or None)
        if verbose:
            print(f"   ❌ 兜底判定: 未完成 → unsolved-report/")
        _resubmit_task(task_name, report_content=report_content, verbose=verbose)
        return True
    elif is_solved:
        # 移动到 solved
        dest = SOLVED_DIR / report_file.name
        shutil.move(str(report_file), str(dest))
        for f in related:
            shutil.move(str(f), str(SOLVED_DIR / f.name))
        if verbose:
            print(f"   ✅ 兜底判定: 已完成 → solved-report/")
        return True
    else:
        # 无法判断 — 保留在 report/ 中，下次再审
        if verbose:
            print(f"   ⚠️  无法判断，保留在 report/ 中待下次审查")
        return False


def _resubmit_task(task_name: str, report_content: str = "", verbose: bool = True):
    """
    调用秘书 Agent 重新提交未完成的任务，附带回收者的改进方向
    """
    from secretary.secretary_agent import run_secretary

    # 读取 unsolved 原因 + 改进方向
    reason_file = UNSOLVED_DIR / f"{task_name}-unsolved-reason.md"
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

    run_secretary(resubmit_request, verbose=verbose)


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
    运行回收者循环

    Args:
        once: 只执行一次
        verbose: 详细输出
    """
    print("=" * 60)
    print("♻️  Secretary Recycler 启动")
    print(f"   报告目录: {REPORT_DIR}")
    print(f"   已解决: {SOLVED_DIR}")
    print(f"   未解决: {UNSOLVED_DIR}")
    print(f"   检查间隔: {RECYCLER_INTERVAL}s ({RECYCLER_INTERVAL // 60}分钟)")
    print(f"   模式: {'单次' if once else '持续运行'}")
    print("=" * 60)

    cycle = 0

    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if verbose:
                print(f"\n--- 回收者 第 {cycle} 轮 [{ts}] ---")

            processed = run_recycler_once(verbose=verbose)

            if verbose and processed > 0:
                print(f"\n   📊 本轮处理了 {processed} 份报告")

            if once:
                break

            if verbose:
                next_ts = datetime.now().strftime("%H:%M:%S")
                print(f"💤 [{next_ts}] 下次检查在 {RECYCLER_INTERVAL}s 后...")
            time.sleep(RECYCLER_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n\n🛑 回收者已停止 (共 {cycle} 个周期)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回收者 — 审查任务报告")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    args = parser.parse_args()
    run_recycler(once=args.once, verbose=not args.quiet)


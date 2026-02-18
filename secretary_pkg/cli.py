#!/usr/bin/env python3
"""
Secretary Agent System — CLI 入口

角色:
  🗂️ 秘书 (Secretary) — 归类任务写入 tasks/
  ⚙️ 工作者 (Worker)   — 执行 ongoing/ 中的任务
  ♻️ 回收者 (Recycler)  — 审查 report/ 中的报告

用法:
  secretary task "实现一个HTTP服务器"
  secretary envolving / analysis / debug
  secretary scan
  secretary recycle
  secretary status
  secretary stop
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime

from secretary.config import (
    TASKS_DIR, ONGOING_DIR, REPORT_DIR, TESTCASES_DIR,
    SOLVED_DIR, UNSOLVED_DIR, PRESETS, DEFAULT_MIN_TIME,
    STATS_DIR, BASE_DIR, ensure_dirs,
)


def _submit_task(request: str, quiet: bool = False, min_time: int = 0):
    """公用: 通过秘书Agent提交任务，可选嵌入最低执行时间元数据"""
    from secretary.secretary_agent import run_secretary

    if not request.strip():
        print("❌ 请提供任务描述")
        sys.exit(1)

    # 快照: 提交前 tasks/ 中的文件
    before = {f.name: f.stat().st_mtime for f in TASKS_DIR.glob("*.md")} if TASKS_DIR.exists() else {}

    print(f"\n📨 提交任务: {request}")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    print()

    success = run_secretary(request, verbose=not quiet)
    if not success:
        sys.exit(1)

    # 如果指定了 min_time, 在秘书新建/修改的任务文件中嵌入元数据
    effective_min_time = min_time or DEFAULT_MIN_TIME
    if effective_min_time > 0:
        after = {f.name: f.stat().st_mtime for f in TASKS_DIR.glob("*.md")} if TASKS_DIR.exists() else {}
        new_or_changed = [
            TASKS_DIR / name for name, mtime in after.items()
            if name not in before or mtime != before[name]
        ]
        for tf in new_or_changed:
            content = tf.read_text(encoding="utf-8")
            if f"<!-- min_time:" not in content:
                tf.write_text(content.rstrip() + f"\n\n<!-- min_time: {effective_min_time} -->\n",
                              encoding="utf-8")
                if not quiet:
                    print(f"   ⏱️ 已嵌入 min_time={effective_min_time}s → {tf.name}")


def cmd_task(args):
    """通过秘书Agent提交自定义任务"""
    request = " ".join(args.request)
    _submit_task(request, quiet=args.quiet, min_time=args.time)


def cmd_envolving(args):
    """预设: 优化仓库"""
    prompt = PRESETS["envolving"]
    print("🔄 预设指令: envolving (优化仓库)")
    _submit_task(prompt, quiet=args.quiet, min_time=args.time)


def cmd_analysis(args):
    """预设: 分析功能 + 生成测试样例"""
    prompt = PRESETS["analysis"].format(testcases_dir=TESTCASES_DIR)
    print("🔬 预设指令: analysis (分析 + 测试样例)")
    _submit_task(prompt, quiet=args.quiet, min_time=args.time)


def cmd_debug(args):
    """预设: 通过所有测试样例"""
    prompt = PRESETS["debug"].format(testcases_dir=TESTCASES_DIR)
    print("🐛 预设指令: debug (通过所有测试)")
    _submit_task(prompt, quiet=args.quiet, min_time=args.time)


def cmd_scan(args):
    """启动任务扫描器"""
    from secretary.scanner import run_scanner
    run_scanner(once=args.once, verbose=not args.quiet)


def cmd_recycle(args):
    """启动回收者"""
    from secretary.recycler import run_recycler
    run_recycler(once=args.once, verbose=not args.quiet)


def cmd_stop(args):
    """停止所有 worker（scan 进程），并清空 tasks/ 下的任务文件"""
    import subprocess
    import os

    print("\n🛑 执行 stop...")

    # 1. 停止所有 main.py scan 进程
    my_pid = os.getpid()
    try:
        if sys.platform == "win32":
            print("   ℹ️ Windows: 请手动关闭运行中的 scan 窗口；已清空 tasks/")
        else:
            r = subprocess.run(
                ["pkill", "-f", "secretary scan"],
                capture_output=True, timeout=10
            )
            if r.returncode == 0:
                print("   ✅ 已发送停止信号给 scan 进程")
            else:
                print("   ℹ️ 未发现正在运行的 scan 进程")

            # 同时停止回收者
            r2 = subprocess.run(
                ["pkill", "-f", "secretary recycle"],
                capture_output=True, timeout=10
            )
            if r2.returncode == 0:
                print("   ✅ 已发送停止信号给 recycle 进程")
    except FileNotFoundError:
        print("   ℹ️ 未找到 pkill，请手动停止进程")
    except subprocess.TimeoutExpired:
        print("   ⚠️ 停止进程超时，请手动检查")
    except Exception as e:
        print(f"   ⚠️ 停止进程时出错: {e}")

    # 2. 清空 tasks/ 下的任务文件
    removed = 0
    if TASKS_DIR.exists():
        for f in TASKS_DIR.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError as e:
                    print(f"   ⚠️ 删除失败 {f.name}: {e}")
    print(f"   📂 已删除 tasks/ 下 {removed} 个任务文件")
    print("✅ stop 完成\n")


def cmd_status(args):
    """查看系统状态"""
    print("\n📊 Secretary Agent 系统状态\n")

    # tasks/
    tasks = list(TASKS_DIR.glob("*.md"))
    print(f"📂 待处理 (tasks/): {len(tasks)} 个")
    for f in tasks:
        print(f"   • {f.name}")

    # ongoing/
    ongoing = list(ONGOING_DIR.glob("*.md"))
    print(f"\n⚙️  执行中 (ongoing/): {len(ongoing)} 个")
    for f in ongoing:
        print(f"   • {f.name}")

    # report/ — 待审查
    reports = sorted(REPORT_DIR.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    stats_files = list(REPORT_DIR.glob("*-stats.json"))
    stats_names = {f.stem.replace("-stats", "") for f in stats_files}
    print(f"\n📄 待审查 (report/): {len(reports)} 份报告, {len(stats_files)} 份统计")
    for f in reports[:10]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        task_name = f.stem.replace("-report", "")
        has_stats = "📊" if task_name in stats_names else "  "
        print(f"   {has_stats} [{mtime}] {f.name}")
    if len(reports) > 10:
        print(f"   ... 还有 {len(reports)-10} 个")

    # solved-report/
    solved = list(SOLVED_DIR.glob("*-report.md"))
    print(f"\n✅ 已解决 (solved-report/): {len(solved)} 份")
    for f in sorted(solved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
    if len(solved) > 5:
        print(f"   ... 还有 {len(solved)-5} 个")

    # unsolved-report/
    unsolved = list(UNSOLVED_DIR.glob("*-report.md"))
    print(f"\n❌ 未解决 (unsolved-report/): {len(unsolved)} 份")
    for f in sorted(unsolved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
        # 显示未解决原因
        reason_file = UNSOLVED_DIR / f.name.replace("-report.md", "-unsolved-reason.md")
        if reason_file.exists():
            reason = reason_file.read_text(encoding="utf-8").strip().splitlines()
            if reason:
                print(f"     原因: {reason[0][:80]}")

    # testcases/
    testcases = list(TESTCASES_DIR.glob("*"))
    testcases = [t for t in testcases if t.is_file()]
    print(f"\n🧪 测试样例 (testcases/): {len(testcases)} 个")
    for f in testcases[:10]:
        print(f"   • {f.name}")

    # 预设指令提示
    print(f"\n💡 预设指令: envolving | analysis | debug")
    print(f"💡 后台服务: scan (工作者) | recycle (回收者)")


def main():
    parser = argparse.ArgumentParser(
        description="Secretary Agent — 基于 Cursor Agent 的自动化任务系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
角色:
  🗂️ 秘书    task / envolving / analysis / debug → 归类任务到 tasks/
  ⚙️ 工作者   scan                                → 执行 ongoing/ 中的任务
  ♻️ 回收者   recycle                             → 审查 report/ 中的报告

完整流程:
  tasks/ → ongoing/ → report/ → solved-report/ 或 unsolved-report/

自定义任务:
  secretary task "你的任务描述"
  secretary task "优化性能" --time 120

预设指令:
  secretary envolving           🔄 自动优化仓库
  secretary analysis            🔬 分析功能 + 生成测试样例
  secretary debug               🐛 通过所有测试

后台服务:
  secretary scan                ⚙️ 启动工作者扫描器
  secretary recycle             ♻️ 启动回收者 (每2分钟审查)

状态:
  secretary status              📊 查看系统状态

停止:
  secretary stop                🛑 停止所有进程 + 清空 tasks/
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 公共参数: --time
    time_help = "最低执行时间(秒)，Agent 提前完成也会被要求继续完善直到达到此时间"

    # ---- task 命令 ----
    task_parser = subparsers.add_parser("task", help="提交自定义任务")
    task_parser.add_argument("request", nargs="+", help="任务描述")
    task_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    task_parser.add_argument("--time", type=int, default=0, help=time_help)

    # ---- 预设指令 ----
    envolving_parser = subparsers.add_parser("envolving", help="🔄 优化仓库")
    envolving_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    envolving_parser.add_argument("--time", type=int, default=0, help=time_help)

    analysis_parser = subparsers.add_parser("analysis", help="🔬 分析功能 + 生成测试样例")
    analysis_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    analysis_parser.add_argument("--time", type=int, default=0, help=time_help)

    debug_parser = subparsers.add_parser("debug", help="🐛 通过所有测试样例")
    debug_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    debug_parser.add_argument("--time", type=int, default=0, help=time_help)

    # ---- scan 命令 ----
    scan_parser = subparsers.add_parser("scan", help="⚙️ 启动工作者扫描器")
    scan_parser.add_argument("--once", action="store_true", help="只执行一次")
    scan_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")

    # ---- recycle 命令 ----
    recycle_parser = subparsers.add_parser("recycle", help="♻️ 启动回收者")
    recycle_parser.add_argument("--once", action="store_true", help="只执行一次")
    recycle_parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")

    # ---- status 命令 ----
    subparsers.add_parser("status", help="📊 查看系统状态")

    # ---- stop 命令 ----
    subparsers.add_parser("stop", help="🛑 停止所有进程 + 清空 tasks/")

    # ---- 全局参数 ----
    parser.add_argument(
        "-w", "--workspace",
        type=str, default=None,
        help="工作区目录 (默认=当前目录)。所有数据目录 (tasks/, ongoing/ 等) 相对于此目录",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 如果指定了 workspace，重设 BASE_DIR
    if args.workspace:
        import secretary.config as _cfg
        ws = Path(args.workspace).resolve()
        _cfg.BASE_DIR = ws
        _cfg.TASKS_DIR = ws / "tasks"
        _cfg.ONGOING_DIR = ws / "ongoing"
        _cfg.REPORT_DIR = ws / "report"
        _cfg.STATS_DIR = ws / "stats"
        _cfg.SOLVED_DIR = ws / "solved-report"
        _cfg.UNSOLVED_DIR = ws / "unsolved-report"
        _cfg.TESTCASES_DIR = ws / "testcases"
        _cfg.SECRETARY_MEMORY_FILE = ws / "secretary_memory.md"

    # 确保运行时目录存在
    ensure_dirs()

    handlers = {
        "task": cmd_task,
        "envolving": cmd_envolving,
        "analysis": cmd_analysis,
        "debug": cmd_debug,
        "scan": cmd_scan,
        "recycle": cmd_recycle,
        "status": cmd_status,
        "stop": cmd_stop,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()

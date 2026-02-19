"""
kai 实时监控面板 — 用 rich 实现美观的 TUI 监控

功能:
  - 实时显示各文件夹的任务数量
  - 最近活动日志
  - 自动刷新 (默认 2s)
  - q 退出
  - 支持文本模式 (--text / --once)：输出与旧 status 等价的文本，无 TUI 时自动退化
"""
import time
import threading
import sys
from pathlib import Path
from datetime import datetime

# Windows 和 Unix 的键盘输入处理
if sys.platform == "win32":
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    try:
        import select
        import termios
        import tty
    except ImportError:
        select = None
        termios = None
        tty = None

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich import box

import secretary.config as cfg
from secretary.settings import get_cli_name


# ============================================================
#  数据采集
# ============================================================

def _count_files(directory: Path, pattern: str = "*.md") -> int:
    """统计目录下符合 pattern 的文件数"""
    if not directory.exists():
        return 0
    return len(list(directory.glob(pattern)))


def _list_files(directory: Path, pattern: str = "*.md", limit: int = 5) -> list[dict]:
    """列出目录下最新的文件 (名称 + 修改时间)"""
    if not directory.exists():
        return []
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for f in files[:limit]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        result.append({
            "name": f.stem,
            "time": mtime.strftime("%H:%M:%S"),
            "date": mtime.strftime("%m-%d"),
        })
    return result


def _count_workers() -> list[dict]:
    """采集所有命名工人的状态"""
    try:
        from secretary.agents import list_workers
        return list_workers()
    except Exception:
        return []


def collect_status() -> dict:
    """采集所有文件夹状态 (包括命名工人)"""
    workers = _count_workers()

    # 所有工人的任务数总和（不再有全局目录）
    worker_tasks = sum(w.get("pending_count", 0) for w in workers)
    worker_ongoing = sum(w.get("ongoing_count", 0) for w in workers)

    # 收集所有 worker 的任务列表
    from secretary.agents import _worker_tasks_dir, _worker_ongoing_dir
    all_tasks_list = []
    all_ongoing_list = []
    for w in workers:
        wtd = _worker_tasks_dir(w["name"])
        wod = _worker_ongoing_dir(w["name"])
        all_tasks_list.extend(_list_files(wtd))
        all_ongoing_list.extend(_list_files(wod))

    return {
        "tasks": worker_tasks,
        "ongoing": worker_ongoing,
        "global_tasks": 0,  # 已废弃，保留用于兼容
        "global_ongoing": 0,  # 已废弃，保留用于兼容
        "report": _count_files(cfg.REPORT_DIR, "*-report.md"),
        "solved": _count_files(cfg.SOLVED_DIR, "*-report.md"),
        "unsolved": _count_files(cfg.UNSOLVED_DIR, "*-report.md"),
        "stats": _count_files(cfg.STATS_DIR, "*-stats.json"),
        "workers": workers,
        # 详细列表
        "tasks_list": all_tasks_list,
        "ongoing_list": all_ongoing_list,
        "report_list": _list_files(cfg.REPORT_DIR, "*-report.md"),
        "solved_list": _list_files(cfg.SOLVED_DIR, "*-report.md", limit=3),
        "unsolved_list": _list_files(cfg.UNSOLVED_DIR, "*-report.md", limit=3),
    }


# ============================================================
#  渲染组件
# ============================================================

def _make_count_box(label: str, count: int, emoji: str, style: str) -> Panel:
    """创建一个计数卡片"""
    count_text = Text(str(count), style=f"bold {style}", justify="center")
    count_text.stylize(f"bold {style}")

    content = Text(justify="center")
    content.append(f"{emoji}\n", style="dim")
    content.append(f"{count}\n", style=f"bold {style}")
    content.append(label, style="dim")

    return Panel(
        Align.center(content),
        border_style=style if count > 0 else "dim",
        width=14,
        height=6,
        padding=(0, 1),
    )


def _make_status_bar(status: dict) -> Columns:
    """创建状态栏 — 六个计数卡片"""
    workers = status.get("workers", [])
    boxes = [
        _make_count_box("待处理", status["tasks"], "📂", "yellow"),
        _make_count_box("执行中", status["ongoing"], "⚙️ ", "cyan"),
        _make_count_box("待审查", status["report"], "📄", "blue"),
        _make_count_box("已解决", status["solved"], "✅", "green"),
        _make_count_box("未解决", status["unsolved"], "❌", "red"),
        _make_count_box("工人", len(workers), "👷", "magenta"),
    ]
    return Columns(boxes, equal=True, expand=True)


def _make_file_list_table(title: str, files: list[dict], style: str) -> Table:
    """创建文件列表小表格"""
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("time", style="dim", width=8)
    table.add_column("name", style=style, ratio=1)

    if not files:
        table.add_row("", Text("(空)", style="dim italic"))
    else:
        for f in files:
            table.add_row(f["time"], f["name"])

    return Panel(table, title=f"[bold]{title}[/]", border_style="dim", expand=True)


def _make_workers_table(workers: list[dict]) -> Table:
    """创建工人列表小表格"""
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("icon", width=3)
    table.add_column("name", style="magenta", ratio=1)
    table.add_column("info", style="dim", ratio=2)

    if not workers:
        table.add_row("", Text("(无)", style="dim italic"), "")
    else:
        for w in workers:
            status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
            info = f"完成:{w.get('completed_tasks',0)} 待:{w.get('pending_count',0)} 中:{w.get('ongoing_count',0)}"
            table.add_row(status_icon, w["name"], info)

    return Panel(table, title="[bold]👷 工人[/]", border_style="dim", expand=True)


def _make_worker_detail_table(worker: dict) -> Table:
    """为单个工人创建详细任务列表"""
    name = worker["name"]
    tasks_dir = cfg.WORKERS_DIR / name / "tasks"
    ongoing_dir = cfg.WORKERS_DIR / name / "ongoing"
    
    table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("type", width=3)
    table.add_column("name", style="magenta", ratio=1)
    
    # 待处理任务
    if tasks_dir.exists():
        tasks = sorted(tasks_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for f in tasks:
            table.add_row("📂", f.stem[:40])
    
    # 执行中任务
    if ongoing_dir.exists():
        ongoings = sorted(ongoing_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for f in ongoings:
            table.add_row("⚙️", f.stem[:40])
    
    if not table.rows:
        table.add_row("", Text("(空闲)", style="dim italic"))
    
    status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(worker.get("status", ""), "❓")
    title = f"[bold]{status_icon} {name}[/] (完成:{worker.get('completed_tasks',0)} 待:{worker.get('pending_count',0)} 中:{worker.get('ongoing_count',0)})"
    return Panel(table, title=title, border_style="magenta", expand=True)


def _make_activity_panel(status: dict) -> Panel:
    """创建活动面板 — 显示各队列的文件列表 + 工人详细信息"""
    workers = status.get("workers", [])
    
    if workers:
        # 有工人时：显示工人详情 + 通用队列 + 报告
        layout = Layout()
        layout.split_row(
            Layout(name="workers", ratio=2),
            Layout(name="global", ratio=1),
            Layout(name="reports", ratio=1),
        )
        
        # 左：工人详情（每个工人一个面板）
        workers_layout = Layout()
        if len(workers) == 1:
            workers_layout.update(_make_worker_detail_table(workers[0]))
        else:
            workers_layout.split_column(*[
                Layout(_make_worker_detail_table(w)) for w in workers[:4]  # 最多显示4个工人
            ])
        layout["workers"].update(workers_layout)
        
        # 中：通用队列
        global_layout = Layout()
        global_layout.split_column(
            Layout(_make_file_list_table("📂 通用 tasks/", status["tasks_list"], "yellow")),
            Layout(_make_file_list_table("⚙️  通用 ongoing/", status["ongoing_list"], "cyan")),
        )
        layout["global"].update(global_layout)
        
        # 右：报告
        reports_layout = Layout()
        reports_layout.split_column(
            Layout(_make_file_list_table("📄 待审查 report/", status["report_list"], "blue")),
            Layout(_make_file_list_table("✅ 已解决", status["solved_list"], "green")),
            Layout(_make_file_list_table("❌ 未解决", status["unsolved_list"], "red")),
        )
        layout["reports"].update(reports_layout)
        
        return Panel(layout, title="[bold]任务详情 (workers/ 结构)[/]", border_style="dim", height=20)
    else:
        # 无工人时：保持原布局
        layout = Layout()
        layout.split_row(
            Layout(name="left", ratio=1),
            Layout(name="mid", ratio=1),
            Layout(name="right", ratio=1),
        )
        
        left_layout = Layout()
        left_layout.split_column(
            Layout(_make_file_list_table("📂 待处理 tasks/", status["tasks_list"], "yellow")),
            Layout(_make_file_list_table("⚙️  执行中 ongoing/", status["ongoing_list"], "cyan")),
        )
        layout["left"].update(left_layout)
        
        mid_layout = Layout()
        mid_layout.split_column(
            Layout(_make_file_list_table("📄 待审查 report/", status["report_list"], "blue")),
            Layout(_make_workers_table([])),
        )
        layout["mid"].update(mid_layout)
        
        right_layout = Layout()
        right_layout.split_column(
            Layout(_make_file_list_table("✅ 已解决 (最近)", status["solved_list"], "green")),
            Layout(_make_file_list_table("❌ 未解决 (最近)", status["unsolved_list"], "red")),
        )
        layout["right"].update(right_layout)
        
        return Panel(layout, title="[bold]任务详情[/]", border_style="dim", height=18)


def build_dashboard() -> Layout:
    """构建完整的监控面板"""
    name = get_cli_name()
    status = collect_status()
    now = datetime.now().strftime("%H:%M:%S")

    # 总数统计
    total = status["tasks"] + status["ongoing"] + status["report"] + status["solved"] + status["unsolved"]

    # 顶部布局
    root = Layout()
    root.split_column(
        Layout(name="header", size=3),
        Layout(name="bar", size=8),
        Layout(name="detail", ratio=1),
        Layout(name="footer", size=1),
    )

    # Header
    header_text = Text(justify="center")
    header_text.append(f"🤖 {name} ", style="bold bright_white")
    header_text.append("任务监控面板", style="bold")
    header_text.append(f"  │  ", style="dim")
    header_text.append(f"工作区: {cfg.BASE_DIR}", style="dim")

    root["header"].update(Panel(header_text, style="bold", border_style="bright_blue"))

    # Status Bar
    root["bar"].update(_make_status_bar(status))

    # Detail
    root["detail"].update(_make_activity_panel(status))

    # Footer
    footer = Text(justify="center")
    footer.append(f" ⏱  {now} ", style="dim")
    footer.append("│", style="dim")
    footer.append(f" 共 {total} 个任务 ", style="dim")
    footer.append("│", style="dim")
    footer.append(f" 每 2s 刷新 ", style="dim")
    footer.append("│", style="dim")
    footer.append(" q 退出 ", style="dim italic")
    root["footer"].update(footer)

    return root


# ============================================================
#  一行式状态栏 (用于交互模式等)
# ============================================================

def build_status_line() -> Text:
    """构建一行式状态摘要 (用于嵌入交互模式)"""
    status = collect_status()
    line = Text()
    line.append(" 📂 ", style="yellow")
    line.append(str(status["tasks"]), style="bold yellow")
    line.append("  ⚙️  ", style="cyan")
    line.append(str(status["ongoing"]), style="bold cyan")
    line.append("  📄 ", style="blue")
    line.append(str(status["report"]), style="bold blue")
    line.append("  ✅ ", style="green")
    line.append(str(status["solved"]), style="bold green")
    line.append("  ❌ ", style="red")
    line.append(str(status["unsolved"]), style="bold red")
    return line


def print_status_line():
    """打印一行状态栏到终端"""
    console = Console()
    line = build_status_line()
    bar = Text()
    bar.append("┃ ", style="dim")
    bar.append_text(line)
    bar.append(" ┃", style="dim")
    console.print(Panel(bar, box=box.HORIZONTALS, style="dim", expand=True, padding=0))


# ============================================================
#  文本状态输出 (与旧 status 等价，供 monitor --text / 无 TUI 退化)
# ============================================================

def print_status_text():
    """输出与旧 status 子命令等价的文本状态（供 kai monitor --text 或无 TUI 时使用）"""
    from secretary.i18n import t
    from secretary.settings import get_language
    from secretary.agents import list_workers, _worker_tasks_dir, _worker_ongoing_dir
    from secretary.skills import list_skills

    name = get_cli_name()
    print(f"\n📊 {name} {t('status_title')}")
    print(f"   {t('status_workspace')}: {cfg.BASE_DIR}\n")

    all_tasks = []
    all_ongoing = []
    for w in list_workers():
        wtd = _worker_tasks_dir(w["name"])
        if wtd.exists():
            for f in wtd.glob("*.md"):
                all_tasks.append((w["name"], f))
        wod = _worker_ongoing_dir(w["name"])
        if wod.exists():
            for f in wod.glob("*.md"):
                all_ongoing.append((w["name"], f))

    count_suffix = f" {t('status_count')}" if get_language() == "zh" else ""
    print(f"📂 {t('status_pending')}: {len(all_tasks)}{count_suffix}")
    for worker_name, f in all_tasks:
        print(f"   • [{worker_name}] {f.name}")

    print(f"\n⚙️  {t('status_ongoing')}: {len(all_ongoing)}{count_suffix}")
    for worker_name, f in all_ongoing:
        print(f"   • [{worker_name}] {f.name}")

    reports = sorted(cfg.REPORT_DIR.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    stats_files = list(cfg.STATS_DIR.glob("*-stats.json"))
    stats_names = {f.stem.replace("-stats", "") for f in stats_files}
    reports_suffix = " 份报告" if get_language() == "zh" else " report(s)"
    print(f"\n📄 {t('status_reports')}: {len(reports)}{reports_suffix}")
    for f in reports[:10]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        task_name = f.stem.replace("-report", "")
        has_stats = "📊" if task_name in stats_names else "  "
        print(f"   {has_stats} [{mtime}] {f.name}")
    if len(reports) > 10:
        print(f"   ... 还有 {len(reports)-10} 个")

    stats_count = len(stats_files)
    stats_suffix = " 份" if get_language() == "zh" else ""
    print(f"\n📊 {t('status_stats')}: {stats_count}{stats_suffix}")

    solved = list(cfg.SOLVED_DIR.glob("*-report.md"))
    solved_suffix = " 份" if get_language() == "zh" else ""
    print(f"\n✅ {t('status_solved')}: {len(solved)}{solved_suffix}")
    for f in sorted(solved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
    if len(solved) > 5:
        print(f"   ... 还有 {len(solved)-5} 个")

    unsolved = list(cfg.UNSOLVED_DIR.glob("*-report.md"))
    unsolved_suffix = " 份" if get_language() == "zh" else ""
    print(f"\n❌ {t('status_unsolved')}: {len(unsolved)}{unsolved_suffix}")
    for f in sorted(unsolved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
        reason_file = cfg.UNSOLVED_DIR / f.name.replace("-report.md", "-unsolved-reason.md")
        if reason_file.exists():
            try:
                reason = reason_file.read_text(encoding="utf-8").strip().splitlines()
                if reason:
                    print(f"     原因: {reason[0][:80]}")
            except Exception:
                pass

    testcases = [f for f in cfg.TESTCASES_DIR.glob("*") if f.is_file()]
    print(f"\n🧪 {t('status_testcases')}: {len(testcases)}{count_suffix}")
    for f in testcases[:10]:
        print(f"   • {f.name}")

    workers = list_workers()
    print(f"\n👷 {t('status_workers')}: {len(workers)}{count_suffix}")
    for w in workers:
        status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
        pid_str = f"PID={w['pid']}" if w.get("pid") else ""
        completed = w.get("completed_tasks", 0)
        pending = w.get("pending_count", 0)
        ongoing = w.get("ongoing_count", 0)
        if get_language() == "zh":
            print(f"   {status_icon} {w['name']:15s}  完成:{completed:3d}  待处理:{pending}  执行中:{ongoing}  {pid_str}")
        else:
            print(f"   {status_icon} {w['name']:15s}  {t('status_completed')}:{completed:3d}  {t('status_pending_count')}:{pending}  {t('status_ongoing_count')}:{ongoing}  {pid_str}")

    skills = list_skills()
    print(f"\n📚 {t('status_skills')}: {len(skills)}{count_suffix}")
    for s in skills[:10]:
        tag = "📦" if s["builtin"] else "🎓"
        print(f"   {tag} {s['name']}")
    if len(skills) > 10:
        print(f"   ... 还有 {len(skills)-10} 个")

    logs = list(cfg.LOGS_DIR.glob("*.log")) if cfg.LOGS_DIR.exists() else []
    print(f"\n📋 {t('status_logs')}: {len(logs)}{count_suffix}")

    print(f"\n💡 {t('status_tips_workers')}:     {name} hire <名字> | {name} start <名字> | {name} fire <名字> | {name} workers")
    print(f"💡 {t('status_tips_skills')}:     {name} skills | {name} <技能名> | {name} learn")
    print(f"💡 {t('status_tips_services')}: start (工作者) | recycle (回收者)")
    print(f"💡 {t('status_tips_settings')}:     {name} base <路径> | {name} name <新名字> | {name} model [模型名]")
    print(f"💡 {t('status_tips_cleanup')}:     {name} clean-logs | {name} clean-processes")


# ============================================================
#  运行监控
# ============================================================

def run_monitor(refresh_interval: float = 2.0, text_mode: bool = False, once: bool = False):
    """启动实时监控面板 (阻塞), 按 q 退出。text_mode/once 时或无可用时输出文本状态并返回。"""
    if text_mode or once:
        print_status_text()
        return

    # 无 TTY 时退化为文本输出（与旧 status 等价）
    if not sys.stdout.isatty():
        print_status_text()
        return

    console = Console()
    stop = threading.Event()

    # 后台线程: 非阻塞读取按键（Windows 兼容）
    def _key_listener():
        if sys.platform == "win32" and msvcrt:
            # Windows 使用 msvcrt
            try:
                while not stop.is_set():
                    if msvcrt.kbhit():
                        try:
                            ch = msvcrt.getch().decode('utf-8').lower()
                        except UnicodeDecodeError:
                            # 处理特殊按键
                            ch = msvcrt.getch()
                            continue
                        if ch == "q":
                            stop.set()
                            return
                    time.sleep(0.2)
            except Exception:
                pass
        elif select and termios and tty:
            # Unix/Linux 使用 termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)  # cbreak 模式: 单字符读取, 不回显
                while not stop.is_set():
                    # select 等待 0.2s, 避免 busy-loop
                    if select.select([sys.stdin], [], [], 0.2)[0]:
                        ch = sys.stdin.read(1)
                        if ch.lower() == "q":
                            stop.set()
                            return
            except Exception:
                pass
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        else:
            # 降级：使用提示模式
            console.print("[yellow]⚠️  键盘监听不可用，使用 Ctrl+C 退出[/]")
            while not stop.is_set():
                time.sleep(1)

    listener = threading.Thread(target=_key_listener, daemon=True)
    listener.start()

    try:
        with Live(
            build_dashboard(),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while not stop.is_set():
                stop.wait(refresh_interval)
                if not stop.is_set():
                    live.update(build_dashboard())
    except KeyboardInterrupt:
        pass
    except Exception:
        # 无 rich 或 TUI 不可用时退化为文本输出
        print_status_text()
        return
    finally:
        stop.set()
        listener.join(timeout=1)
        name = get_cli_name()
        console.print(f"\n👋 {name} 监控面板已退出\n")


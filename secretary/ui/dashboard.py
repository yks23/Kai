"""
kai 实时监控面板 — 简化的 Agent 状态监控

功能:
  - 实时显示所有 agent 及其任务统计
  - 自动刷新 (默认 2s)
  - q 退出
  - 支持文本模式 (--text / --once)
"""
import time
import threading
import sys
from pathlib import Path
from datetime import datetime

# 使用公共的键盘输入处理
from secretary.ui.common import setup_keyboard_input, restore_keyboard_input, read_key

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

import secretary.config as cfg
from secretary.settings import get_cli_name


# ============================================================
#  数据采集
# ============================================================

def _count_workers() -> list[dict]:
    """采集所有命名工人的状态"""
    try:
        from secretary.agents import list_workers
        return list_workers()
    except Exception:
        return []


def _count_all_agent_reports() -> int:
    """统计所有agent的reports目录中的报告文件数"""
    reports = []
    if cfg.AGENTS_DIR.exists():
        for agent_dir in cfg.AGENTS_DIR.iterdir():
            if not agent_dir.is_dir():
                continue
            # 跳过recycler自己的reports目录
            if agent_dir.name == "recycler":
                continue
            reports_dir = agent_dir / "reports"
            if reports_dir.exists():
                reports.extend(reports_dir.glob("*-report.md"))
    return len(reports)


def _count_recycler_solved() -> int:
    """统计recycler的solved目录中的报告文件数"""
    recycler_dir = cfg.AGENTS_DIR / "recycler"
    solved_dir = recycler_dir / "solved"
    if solved_dir.exists():
        return len(list(solved_dir.glob("*-report.md")))
    return 0


def _count_recycler_unsolved() -> int:
    """统计recycler的unsolved目录中的报告文件数"""
    recycler_dir = cfg.AGENTS_DIR / "recycler"
    unsolved_dir = recycler_dir / "unsolved"
    if unsolved_dir.exists():
        return len(list(unsolved_dir.glob("*-report.md")))
    return 0


def collect_status() -> dict:
    """采集所有文件夹状态 (用于状态栏)"""
    workers = _count_workers()

    # 所有工人的任务数总和
    worker_tasks = sum(w.get("pending_count", 0) for w in workers)
    worker_ongoing = sum(w.get("ongoing_count", 0) for w in workers)

    return {
        "tasks": worker_tasks,
        "ongoing": worker_ongoing,
        "report": _count_all_agent_reports(),
        "solved": _count_recycler_solved(),
        "unsolved": _count_recycler_unsolved(),
    }


# ============================================================
#  渲染组件
# ============================================================



def _build_simple_dashboard(refresh_interval: float = 2.0) -> Layout:
    """构建简化的监控面板：显示agent及其任务统计和进程信息（合并到一个表）"""
    from secretary.agents import list_workers
    
    workers = list_workers()
    
    # 获取活跃进程：完全基于全局队列
    active_procs = []
    try:
        from secretary.cli import _get_active_processes, _sync_processes_to_queue
        # 先同步agents.json到队列（确保队列完整）
        _sync_processes_to_queue()
        # 然后从队列获取
        active_procs = _get_active_processes()
    except Exception:
        active_procs = []
    
    # 创建进程PID映射（快速查找）
    proc_pid_map = {proc.get("name"): proc.get("pid") for proc in active_procs}
    
    # 创建合并的表格（调整列宽以适应终端）
    table = Table(
        show_header=True,
        header_style="bold cyan",
        box=box.ROUNDED,
        expand=True,
    )
    table.add_column("Agent", style="cyan", width=15)
    table.add_column("类型", style="magenta", width=10)
    table.add_column("执行中", style="cyan", justify="center", width=8)
    table.add_column("已完成", style="green", justify="right", width=8)
    table.add_column("状态", style="dim", width=4)
    table.add_column("PID", style="dim", justify="right", width=8)
    
    if not workers:
        table.add_row("(无agent)", "", "", "", "", "")
    else:
        type_icons = {
            "secretary": "🤖",
            "worker": "👷",
            "boss": "👔",
            "recycler": "♻️",
        }
        for w in workers:
            agent_name = w.get("name", "unknown")
            agent_type = w.get("type", "unknown")
            executing = w.get("executing", False)
            completed = w.get("completed_tasks", 0)
            status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
            
            type_icon = type_icons.get(agent_type, "❓")
            type_display = f"{type_icon} {agent_type}"
            
            # 执行中显示勾或叉
            executing_display = "✓" if executing else "✗"
            
            # 获取PID（从进程队列或agents.json）
            pid = proc_pid_map.get(agent_name) or w.get("pid")
            pid_display = f"{pid}" if pid else "-"
            
            table.add_row(
                agent_name,
                type_display,
                executing_display,
                str(completed),
                status_icon,
                pid_display,
            )
    
    # 底部提示：第一行 时间/刷新/退出，第二行 日志与报告引导
    now = datetime.now().strftime("%H:%M:%S")
    name = get_cli_name()
    footer1 = Text(justify="center")
    footer1.append(f" ⏱  {now} ", style="dim")
    footer1.append("│", style="dim")
    footer1.append(f" 每 {refresh_interval}s 刷新 ", style="dim")
    footer1.append("│", style="dim")
    footer1.append(" q 退出 ", style="dim italic")
    footer2 = Text(justify="center")
    footer2.append(f" 日志: {name} check <名> ", style="dim")
    footer2.append("│", style="dim")
    footer2.append(" 报告: (待接 report 命令) ", style="dim")
    
    layout = Layout()
    layout.split_column(
        Layout(Panel(table, title="[bold]Agent状态与进程[/]", border_style="cyan")),
        Layout(footer1, size=1),
        Layout(footer2, size=1),
    )
    
    return layout




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
    """状态快照：用 rich Table 显示 agent 列表和活跃进程"""
    from secretary.agents import list_workers

    name = get_cli_name()
    workers = list_workers()

    console = Console()
    table = Table(
        title=f"📊 {name} Agent 状态 — {cfg.BASE_DIR}",
        box=box.ROUNDED,
        title_style="bold",
    )
    table.add_column("Agent", style="cyan")
    table.add_column("类型", style="magenta")
    table.add_column("待办", justify="right", style="yellow")
    table.add_column("进行", justify="right", style="blue")
    table.add_column("完成", justify="right", style="green")
    table.add_column("状态")
    table.add_column("PID", justify="right", style="dim")

    type_icons = {"secretary": "🤖", "worker": "👷", "boss": "👔", "recycler": "♻️"}

    active_procs = []
    try:
        from secretary.cli import _get_active_processes, _sync_processes_to_queue
        _sync_processes_to_queue()
        active_procs = _get_active_processes()
    except Exception:
        pass
    proc_pid_map = {p.get("name"): p.get("pid") for p in active_procs}

    if not workers:
        table.add_row("(无 agent)", "", "", "", "", "", "")
    else:
        for w in workers:
            agent_name = w.get("name", "?")
            agent_type = w.get("type", "?")
            pending = w.get("pending_count", 0)
            ongoing = w.get("ongoing_count", 0)
            completed = w.get("completed_tasks", 0)
            icon = type_icons.get(agent_type, "❓")
            pid = proc_pid_map.get(agent_name) or w.get("pid")

            status = w.get("status", "")
            if pid:
                try:
                    import os
                    os.kill(pid, 0)
                    status_display = "[green]运行[/]"
                except (OSError, ProcessLookupError):
                    status_display = "[dim]💤 空闲[/]"
                    pid = None
            else:
                status_display = "[dim]💤 空闲[/]"

            table.add_row(
                agent_name,
                f"{icon} {agent_type}",
                str(pending) if pending else "-",
                str(ongoing) if ongoing else "-",
                str(completed),
                status_display,
                str(pid) if pid else "-",
            )

    console.print()
    console.print(table)

    running_count = len([p for p in active_procs])
    console.print(f"  [dim]活跃进程: {running_count} 个  |  {name} check <名> 查看日志[/]\n")


# ============================================================
#  运行监控
# ============================================================

def run_monitor(refresh_interval: float = 2.0, text_mode: bool = False, once: bool = False):
    """简化的监控面板：只显示agent及其任务统计"""
    if text_mode or once:
        print_status_text()
        return

    # 无 TTY 时退化为文本输出
    if not sys.stdout.isatty():
        print_status_text()
        return

    console = Console()
    stop = threading.Event()

    # 后台线程: 非阻塞读取按键（使用公共函数）
    def _key_listener():
        original_settings, success = setup_keyboard_input()
        try:
            while not stop.is_set():
                ch = read_key(timeout=0.2)
                if ch:
                    ch_lower = ch.lower()
                    if ch_lower == "q" or ch == "\x1b":  # q 或 ESC
                        stop.set()
                        return
        except Exception:
            pass
        finally:
            restore_keyboard_input(original_settings)

    listener = threading.Thread(target=_key_listener, daemon=True)
    listener.start()

    try:
        with Live(
            _build_simple_dashboard(refresh_interval),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while not stop.is_set():
                stop.wait(refresh_interval)
                if not stop.is_set():
                    live.update(_build_simple_dashboard(refresh_interval))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # 无 rich 或 TUI 不可用时退化为文本输出
        console.print(f"[red]TUI模式失败: {e}[/]")
        print_status_text()
        return
    finally:
        stop.set()
        listener.join(timeout=1)
        name = get_cli_name()
        console.print(f"\n👋 {name} 监控面板已退出\n")


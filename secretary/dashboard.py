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
    table.add_column("待处理", style="yellow", justify="right", width=6)
    table.add_column("执行中", style="cyan", justify="right", width=6)
    table.add_column("已完成", style="green", justify="right", width=6)
    table.add_column("状态", style="dim", width=4)
    table.add_column("PID", style="dim", justify="right", width=8)
    
    if not workers:
        table.add_row("(无agent)", "", "", "", "", "", "")
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
            pending = w.get("pending_count", 0)
            ongoing = w.get("ongoing_count", 0)
            completed = w.get("completed_tasks", 0)
            status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
            
            type_icon = type_icons.get(agent_type, "❓")
            type_display = f"{type_icon} {agent_type}"
            
            # 获取PID（从进程队列或agents.json）
            pid = proc_pid_map.get(agent_name) or w.get("pid")
            pid_display = f"{pid}" if pid else "-"
            
            table.add_row(
                agent_name,
                type_display,
                str(pending),
                str(ongoing),
                str(completed),
                status_icon,
                pid_display,
            )
    
    # 添加时间戳
    now = datetime.now().strftime("%H:%M:%S")
    footer = Text(justify="center")
    footer.append(f" ⏱  {now} ", style="dim")
    footer.append("│", style="dim")
    footer.append(f" 每 {refresh_interval}s 刷新 ", style="dim")
    footer.append("│", style="dim")
    footer.append(" q 退出 ", style="dim italic")
    
    # 计算布局大小（确保至少能显示表头和所有数据行）
    # header (1行) + border (2行) + rows
    if workers:
        # 有数据：header + border + 数据行数，至少5行（1 header + 2 border + 2 rows）
        table_size = max(len(workers) + 3, 5)
    else:
        # 无数据：header + border + 1行提示
        table_size = 4
    
    layout = Layout()
    # 使用计算的大小，确保表格完整显示
    # 不设置 size，让表格自动适应内容
    layout.split_column(
        Layout(Panel(table, title="[bold]Agent状态与进程[/]", border_style="cyan")),
        Layout(footer, size=1),
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
    """简化的状态输出：只显示agent及其任务统计和活跃进程"""
    from secretary.agents import list_workers
    
    name = get_cli_name()
    print(f"\n📊 {name} Agent状态")
    print(f"   工作区: {cfg.BASE_DIR}\n")
    
    workers = list_workers()
    
    if not workers:
        print("   (无agent)")
    else:
        # 表头
        print(f"{'Agent':<20} {'类型':<12} {'待处理':<8} {'执行中':<8} {'已完成':<8} {'状态'}")
        print("-" * 70)
        
        for w in workers:
            agent_name = w.get("name", "unknown")
            agent_type = w.get("type", "unknown")
            pending = w.get("pending_count", 0)
            ongoing = w.get("ongoing_count", 0)
            completed = w.get("completed_tasks", 0)
            status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
            
            # 类型图标
            type_icons = {
                "secretary": "🤖",
                "worker": "👷",
                "boss": "👔",
                "recycler": "♻️",
            }
            type_icon = type_icons.get(agent_type, "❓")
            
            print(f"{type_icon} {agent_name:<17} {agent_type:<12} {pending:<8} {ongoing:<8} {completed:<8} {status_icon}")
    
    # 显示活跃进程：完全基于全局队列
    active_procs = []
    try:
        from secretary.cli import _get_active_processes, _sync_processes_to_queue
        # 先同步agents.json到队列（确保队列完整）
        _sync_processes_to_queue()
        # 然后从队列获取
        active_procs = _get_active_processes()
        
        if active_procs:
            print(f"\n⚙️  活跃进程 ({len(active_procs)} 个):")
            type_icons = {
                "secretary": "🤖",
                "worker": "👷",
                "boss": "👔",
                "recycler": "♻️",
            }
            for proc in active_procs:
                icon = type_icons.get(proc.get("type", ""), "❓")
                proc_name = proc.get("name", "unknown")
                proc_type = proc.get("type", "unknown")
                pid = proc.get("pid", 0)
                print(f"   {icon} {proc_name:15s} ({proc_type}) PID={pid}")
        else:
            print(f"\n⚙️  活跃进程: 无")
    except Exception:
        pass
    
    print()


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
                        if ch == "q" or ch == "\x1b":  # q 或 ESC
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
                        if ch.lower() == "q" or ch == "\x1b":
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


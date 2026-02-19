"""
交互式任务报告查看器 — 参考 monitor 实现

功能:
  - 按时间顺序显示 worker 的最新任务
  - 显示任务详细内容（待处理/执行中/已完成）
  - 按 'p' 查看上一个任务
  - 按 'n' 查看下一个任务
  - 按 'q' 退出
"""
import time
import threading
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

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
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.markdown import Markdown
from rich import box

import secretary.config as cfg
from secretary.agents import _worker_tasks_dir, _worker_ongoing_dir, get_worker


def _collect_worker_tasks(worker_name: str) -> list[dict]:
    """收集 worker 的所有任务，按时间排序（最新的在前）"""
    tasks = []
    
    # 1. 待处理任务
    tasks_dir = _worker_tasks_dir(worker_name)
    if tasks_dir.exists():
        for task_file in tasks_dir.glob("*.md"):
            mtime = task_file.stat().st_mtime
            try:
                content = task_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = ""
                for line in lines[:10]:
                    if line.strip().startswith("#"):
                        title = line.strip().lstrip("#").strip()
                        break
                if not title and lines:
                    title = lines[0].strip()[:50]
                if not title:
                    title = task_file.stem
            except Exception:
                title = task_file.stem
                content = ""
            
            tasks.append({
                "name": task_file.stem,
                "file": task_file,
                "type": "pending",
                "mtime": mtime,
                "title": title,
                "content": content,
            })
    
    # 2. 执行中任务
    ongoing_dir = _worker_ongoing_dir(worker_name)
    if ongoing_dir.exists():
        for task_file in ongoing_dir.glob("*.md"):
            mtime = task_file.stat().st_mtime
            try:
                content = task_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = ""
                for line in lines[:10]:
                    if line.strip().startswith("#"):
                        title = line.strip().lstrip("#").strip()
                        break
                if not title and lines:
                    title = lines[0].strip()[:50]
                if not title:
                    title = task_file.stem
            except Exception:
                title = task_file.stem
                content = ""
            
            tasks.append({
                "name": task_file.stem,
                "file": task_file,
                "type": "ongoing",
                "mtime": mtime,
                "title": title,
                "content": content,
            })
    
    # 3. 已完成报告（从worker自己的reports目录读取）
    from secretary.agents import _worker_reports_dir
    reports_dir = _worker_reports_dir(worker_name)
    if reports_dir.exists():
        for report_file in reports_dir.glob("*-report.md"):
            task_name = report_file.stem.replace("-report", "")
            mtime = report_file.stat().st_mtime
            try:
                content = report_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = ""
                for line in lines[:10]:
                    if line.strip().startswith("#"):
                        title = line.strip().lstrip("#").strip()
                        break
                if not title and lines:
                    title = lines[0].strip()[:50]
                if not title:
                    title = task_name
            except Exception:
                title = task_name
                content = ""
            
            tasks.append({
                "name": task_name,
                "file": report_file,
                "type": "completed",
                "mtime": mtime,
                "title": title,
                "content": content,
            })
    
    # 4. 已解决报告（从recycler的solved目录读取）
    recycler_dir = cfg.AGENTS_DIR / "recycler"
    solved_dir = recycler_dir / "solved"
    if solved_dir.exists():
        for report_file in solved_dir.glob("*-report.md"):
            task_name = report_file.stem.replace("-report", "")
            mtime = report_file.stat().st_mtime
            try:
                content = report_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                title = ""
                for line in lines[:10]:
                    if line.strip().startswith("#"):
                        title = line.strip().lstrip("#").strip()
                        break
                if not title and lines:
                    title = lines[0].strip()[:50]
                if not title:
                    title = task_name
            except Exception:
                title = task_name
                content = ""
            
            tasks.append({
                "name": task_name,
                "file": report_file,
                "type": "solved",
                "mtime": mtime,
                "title": title,
                "content": content,
            })
    
    # 按时间排序（最新的在前）
    tasks.sort(key=lambda x: x["mtime"], reverse=True)
    return tasks


def _build_task_panel(task: dict, index: int, total: int) -> Panel:
    """构建任务详情面板"""
    task_type = task["type"]
    type_info = {
        "pending": ("📂 待处理", "yellow"),
        "ongoing": ("⚙️  执行中", "cyan"),
        "completed": ("✅ 已完成", "blue"),
        "solved": ("✅ 已解决", "green"),
    }
    type_label, type_style = type_info.get(task_type, ("❓ 未知", "dim"))
    
    # 标题
    title_text = Text()
    title_text.append(f"{type_label} ", style=f"bold {type_style}")
    title_text.append(f"[{index+1}/{total}] ", style="dim")
    title_text.append(task["name"], style="bold")
    
    # 时间
    mtime = datetime.fromtimestamp(task["mtime"])
    time_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
    
    # 内容
    content = task.get("content", "")
    if content:
        # 限制内容长度，避免过长
        lines = content.splitlines()
        if len(lines) > 50:
            content = "\n".join(lines[:50]) + "\n\n... (内容已截断，共 {} 行) ...".format(len(lines))
        try:
            content_panel = Markdown(content)
        except Exception:
            content_panel = Text(content)
    else:
        content_panel = Text("(无内容)", style="dim italic")
    
    # 布局
    layout = Layout()
    layout.split_column(
        Layout(Text(f"时间: {time_str}", style="dim"), size=1),
        Layout(content_panel, ratio=1),
    )
    
    return Panel(
        layout,
        title=title_text,
        border_style=type_style,
        padding=(1, 2),
    )


def _build_report_dashboard(worker_name: str, current_index: int, tasks: list[dict]) -> Layout:
    """构建报告面板"""
    if not tasks:
        return Layout(Panel(Text("(无任务)", style="dim italic"), title=f"[bold]{worker_name} 的任务报告[/]"))
    
    total = len(tasks)
    current_index = max(0, min(current_index, total - 1))
    current_task = tasks[current_index]
    
    # 检查 worker 信息
    worker_info = get_worker(worker_name)
    worker_stats = ""
    if worker_info:
        worker_stats = (
            f"已完成: {worker_info.get('completed_tasks', 0)} | "
            f"待处理: {worker_info.get('pending_count', 0)} | "
            f"执行中: {worker_info.get('ongoing_count', 0)}"
        )
    
    # 构建布局
    root = Layout()
    root.split_column(
        Layout(name="header", size=3),
        Layout(name="task", ratio=1),
        Layout(name="footer", size=1),
    )
    
    # Header
    header_text = Text(justify="center")
    header_text.append(f"📋 {worker_name} 的任务报告", style="bold bright_white")
    if worker_stats:
        header_text.append(f"  │  {worker_stats}", style="dim")
    
    root["header"].update(Panel(header_text, style="bold", border_style="bright_blue"))
    
    # Task Panel
    root["task"].update(_build_task_panel(current_task, current_index, total))
    
    # Footer
    footer = Text(justify="center")
    footer.append(" [p] 上一个 ", style="dim")
    footer.append("│", style="dim")
    footer.append(" [n] 下一个 ", style="dim")
    footer.append("│", style="dim")
    footer.append(" [q] 退出 ", style="dim italic")
    
    root["footer"].update(footer)
    
    return root


def run_interactive_report(worker_name: str):
    """启动交互式报告查看器"""
    # 检查 worker 是否存在
    worker_info = get_worker(worker_name)
    if not worker_info:
        console = Console()
        console.print(f"[red]❌ Worker '{worker_name}' 不存在[/]")
        console.print(f"   使用 `{cfg.DEFAULT_WORKER_NAME} workers` 查看所有 worker")
        return
    
    console = Console()
    stop = threading.Event()
    current_index = [0]  # 使用列表以便在闭包中修改
    
    # 收集任务
    tasks = _collect_worker_tasks(worker_name)
    
    if not tasks:
        console.print(f"[yellow]⚠️  {worker_name} 暂无任务[/]")
        return
    
    # 键盘监听（Windows 兼容）
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
                        elif ch == "p":  # previous
                            if current_index[0] > 0:
                                current_index[0] -= 1
                        elif ch == "n":  # next
                            if current_index[0] < len(tasks) - 1:
                                current_index[0] += 1
                    time.sleep(0.1)
            except Exception:
                pass
        elif select and termios and tty:
            # Unix/Linux 使用 termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while not stop.is_set():
                    if select.select([sys.stdin], [], [], 0.2)[0]:
                        ch = sys.stdin.read(1).lower()
                        if ch == "q":
                            stop.set()
                            return
                        elif ch == "p":  # previous
                            if current_index[0] > 0:
                                current_index[0] -= 1
                        elif ch == "n":  # next
                            if current_index[0] < len(tasks) - 1:
                                current_index[0] += 1
            except Exception:
                pass
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        else:
            # 降级：使用 input() 提示
            console.print("[yellow]⚠️  键盘监听不可用，使用输入模式[/]")
            while not stop.is_set():
                try:
                    cmd = input("\n命令 (p/n/q): ").strip().lower()
                    if cmd == "q":
                        stop.set()
                        return
                    elif cmd == "p":
                        if current_index[0] > 0:
                            current_index[0] -= 1
                    elif cmd == "n":
                        if current_index[0] < len(tasks) - 1:
                            current_index[0] += 1
                except (EOFError, KeyboardInterrupt):
                    stop.set()
                    return
    
    listener = threading.Thread(target=_key_listener, daemon=True)
    listener.start()
    
    try:
        with Live(
            _build_report_dashboard(worker_name, current_index[0], tasks),
            console=console,
            refresh_per_second=10,  # 高刷新率以响应按键
            screen=True,
        ) as live:
            while not stop.is_set():
                stop.wait(0.1)  # 快速刷新
                if not stop.is_set():
                    live.update(_build_report_dashboard(worker_name, current_index[0], tasks))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        listener.join(timeout=1)
        console.print(f"\n👋 {worker_name} 的报告查看器已退出\n")


#!/usr/bin/env python3
"""
Kai — CLI 入口（基于 Cursor Agent 的自动化任务系统）

用法:
  kai task "实现一个HTTP服务器"
  kai evolving / analysis / debug        (内置技能)
  kai learn "任务描述" skill-name         (学技能)
  kai <skill-name>                       (使用技能)
  kai forget <skill-name>                (忘技能)
  kai skills                             (列出所有技能)
  kai hire / recycle                     (后台服务)
  kai monitor / stop / clean-logs
  kai base ./          设定工作区为当前目录
  kai name lily        给我改个名字叫 lily
  kai target "目标描述"  创建Boss Agent (boss yks "目标" ykc)
"""
import argparse
import os
import shlex
import subprocess
import sys
from collections import deque
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.settings import (
    get_cli_name, set_cli_name, get_base_dir, set_base_dir,
    get_model, set_model, get_language, load_settings,
)
from secretary.i18n import t


def _cli_name() -> str:
    """获取当前 CLI 命令名 (用于帮助文本)"""
    return get_cli_name()


def _is_workspace_configured(args) -> bool:
    """检测是否已通过 kai base / -w / SECRETARY_WORKSPACE 设定工作区（未设定则使用 CWD）"""
    if get_base_dir():
        return True
    if os.environ.get("SECRETARY_WORKSPACE", "").strip():
        return True
    if getattr(args, "workspace", None):
        return True
    return False


def _check_process_exists(pid: int) -> bool:
    """检查进程是否存在（跨平台）"""
    if sys.platform == "win32":
        # Windows: 使用 tasklist 检查
        try:
            check_result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=5,
            )
            if check_result.returncode == 0 and check_result.stdout:
                try:
                    output = check_result.stdout.decode("gbk", errors="ignore")
                    if str(pid) in output and "信息" not in output:
                        return True
                except:
                    # 如果解码失败，尝试直接检查
                    if str(pid).encode() in check_result.stdout:
                        return True
        except Exception:
            pass
        return False
    else:
        # Unix/Linux: 使用 os.kill(pid, 0)
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


# ============================================================
#  进程队列管理
# ============================================================

# 全局进程队列：跟踪所有启动的agent扫描进程
# 格式: deque([{"name": str, "type": str, "pid": int, "started_at": datetime}, ...])
_active_processes: deque = deque(maxlen=100)  # 最多保留100条记录


def _register_process(agent_name: str, agent_type: str, pid: int):
    """注册一个启动的agent扫描进程"""
    from datetime import datetime
    _active_processes.append({
        "name": agent_name,
        "type": agent_type,
        "pid": pid,
        "started_at": datetime.now(),
    })


def _get_active_processes() -> list[dict]:
    """获取所有活跃的进程（检查进程是否真的存在）"""
    active = []
    for proc_info in _active_processes:
        pid = proc_info.get("pid")
        if pid and _check_process_exists(pid):
            active.append(proc_info)
    return active


def _remove_process(agent_name: str | None = None, pid: int | None = None):
    """从队列中移除进程（通过name或pid）"""
    # deque不支持切片赋值，需要重建deque
    from collections import deque
    if agent_name:
        filtered = [p for p in _active_processes if p.get("name") != agent_name]
        _active_processes.clear()
        _active_processes.extend(filtered)
    elif pid:
        filtered = [p for p in _active_processes if p.get("pid") != pid]
        _active_processes.clear()
        _active_processes.extend(filtered)


# ============================================================
#  Agent Scanner 启动
# ============================================================

def _auto_start_agents(silent: bool = True) -> int:
    """
    自动启动所有已注册但未运行的agent扫描器
    同时同步agents.json中的进程到全局队列
    
    Args:
        silent: 是否静默启动（不打印输出）
    
    Returns:
        int: 成功启动的agent数量
    """
    from secretary.agents import list_workers, update_worker_status
    
    workers = list_workers()
    started_count = 0
    
    # 先同步agents.json中的进程到队列（确保队列完整）
    _sync_processes_to_queue()
    
    for worker in workers:
        agent_name = worker.get("name")
        agent_type = worker.get("type", "worker")
        pid = worker.get("pid")
        
        # 检查是否已经在运行
        is_running = False
        if pid:
            if _check_process_exists(pid):
                is_running = True
                # 确保在队列中（可能队列丢失了）
                _ensure_process_in_queue(agent_name, agent_type, pid)
            else:
                # 进程不存在，清除pid记录
                update_worker_status(agent_name, "idle", pid=None)
                _remove_process(agent_name=agent_name)
        
        # 如果未运行，启动scanner
        if not is_running:
            try:
                if _start_agent_scanner(agent_name, agent_type, silent=silent):
                    started_count += 1
            except Exception as e:
                # 启动失败时记录错误，但不中断其他agent的启动
                if not silent:
                    print(f"⚠️  启动 {agent_name} ({agent_type}) 失败: {e}")
                # 在静默模式下，错误会被忽略，但可以通过日志查看
    
    return started_count


def _sync_processes_to_queue():
    """同步agents.json中的进程到全局队列（确保队列完整）"""
    from secretary.agents import list_workers
    workers = list_workers()
    
    for worker in workers:
        agent_name = worker.get("name")
        agent_type = worker.get("type", "worker")
        pid = worker.get("pid")
        status = worker.get("status", "idle")
        
        # 如果状态是busy且有PID，确保在队列中
        if pid and status == "busy":
            if _check_process_exists(pid):
                _ensure_process_in_queue(agent_name, agent_type, pid)


def _ensure_process_in_queue(agent_name: str, agent_type: str, pid: int):
    """确保进程在队列中（如果不在则添加）"""
    # 检查是否已在队列中
    for proc_info in _active_processes:
        if proc_info.get("name") == agent_name and proc_info.get("pid") == pid:
            return  # 已在队列中
    
    # 不在队列中，添加
    _register_process(agent_name, agent_type, pid)


def _start_agent_scanner(agent_name: str, agent_type: str, silent: bool = False) -> bool:
    """
    根据agent类型启动对应的scanner进程
    
    使用注册表动态查找 agent 类型，支持内置类型和自定义类型。
    
    Args:
        agent_name: agent名称
        agent_type: agent类型 (secretary/worker/boss/recycler 或自定义类型)
        silent: 是否静默启动（不打印输出）
    
    Returns:
        bool: 是否成功启动
    """
    import secretary.config as cfg
    import subprocess
    import os
    from secretary.agents import update_worker_status, _worker_logs_dir
    from secretary.agent_registry import get_agent_type, initialize_registry, list_agent_types
    
    # 确保注册表已初始化
    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass  # 如果初始化失败，继续尝试使用已注册的类型
    
    try:
        # 设置环境变量（在所有类型分支之前）
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        
        # 从注册表获取 agent 类型
        agent_type_instance = get_agent_type(agent_type)
        
        if agent_type_instance is None:
            # 类型未找到，显示错误信息
            if not silent:
                available_types = list_agent_types()
                print(f"⚠️  未知的agent类型: {agent_type}")
                if available_types:
                    print(f"   可用类型: {', '.join(available_types)}")
                else:
                    print(f"   未找到任何已注册的 agent 类型")
            return False
        
        # 准备日志目录
        log_dir = _worker_logs_dir(agent_name)
        log_dir.mkdir(parents=True, exist_ok=True)
        scanner_log_file = log_dir / "scanner.log"
        
        # 根据类型名称构建启动命令
        # 对于内置类型，使用特定的启动方式（保持向后兼容）
        # 对于自定义类型，使用统一的 scanner 启动方式
        if agent_type == "secretary":
            # Secretary 使用 scanner.run_kai_scanner
            sub_cmd = [sys.executable, "-c", f"from secretary.scanner import run_kai_scanner; run_kai_scanner(once=False, verbose=True, secretary_name='{agent_name}')"]
        elif agent_type == "recycler":
            # Recycler 使用 secretary.recycler，需要特殊环境变量
            sub_cmd = [sys.executable, "-m", "secretary.recycler"]
            env["KAI_RECYCLE_BACKGROUND"] = "1"
        else:
            # 其他类型（worker, boss 或自定义类型）使用统一的 scanner
            sub_cmd = [sys.executable, "-m", "secretary.scanner", "--agent", agent_name, "--type", agent_type, "--quiet"]
        
        # 打开日志文件用于重定向输出
        log_file_handle = open(scanner_log_file, "a", encoding="utf-8", buffering=1)
        
        proc = subprocess.Popen(
            sub_cmd,
            stdout=log_file_handle,
            stderr=subprocess.STDOUT,
            cwd=cfg.BASE_DIR,
            env=env,
            bufsize=1,
        )
        
        # 不关闭文件句柄，让进程持续写入
        
        # 更新状态和注册进程
        update_worker_status(agent_name, "busy", pid=proc.pid)
        _register_process(agent_name, agent_type, proc.pid)
        
        if not silent:
            type_icons = {
                "secretary": "🤖",
                "worker": "👷",
                "boss": "👔",
                "recycler": "♻️",
            }
            icon = type_icons.get(agent_type, "❓")
            print(f"✅ {icon} {agent_name} ({agent_type}) 已在后台启动 (PID={proc.pid})")
            if agent_type != "recycler":
                print(f"   日志: {scanner_log_file}")
        
        return True
        
    except Exception as e:
        if not silent:
            print(t("error_agent_start_failed"))
            print(f"   详情: {e}")
        return False


# ============================================================
#  任务提交
# ============================================================

def _write_kai_task(request: str, min_time: int = 0, secretary_name: str = "kai") -> Path:
    """公用：将任务写入指定secretary的 tasks 目录，由secretary扫描器处理（run_secretary）。
    与 task 命令不指定 --worker 时行为一致。返回写入的文件路径。
    """
    from secretary.agents import _worker_tasks_dir
    tasks_dir = _worker_tasks_dir(secretary_name)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    task_file_name = f"task-{timestamp}.md"
    task_file = tasks_dir / task_file_name
    task_content = request
    if min_time > 0:
        task_content += f"\n\n<!-- min_time: {min_time} -->\n"
    task_file.write_text(task_content, encoding="utf-8")
    return task_file


def _select_secretary(secretaries: list[dict]) -> str | None:
    """在TUI中让用户选择secretary，返回选中的secretary名称"""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Prompt
        from rich.table import Table
        
        console = Console()
        
        # 构建选择表格
        table = Table(title="选择Secretary Agent", show_header=True, header_style="bold magenta")
        table.add_column("序号", style="cyan", width=6)
        table.add_column("名称", style="green", width=20)
        table.add_column("描述", style="yellow", width=40)
        table.add_column("状态", style="blue", width=10)
        
        for idx, sec in enumerate(secretaries, 1):
            name = sec.get("name", "unknown")
            desc = sec.get("description", "(无描述)")
            status = sec.get("status", "unknown")
            table.add_row(str(idx), name, desc[:40], status)
        
        console.print("\n")
        console.print(table)
        console.print("\n")
        
        # 提示用户选择
        while True:
            choice = Prompt.ask(
                f"请选择secretary (1-{len(secretaries)})",
                default="1",
                console=console
            )
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(secretaries):
                    return secretaries[idx]["name"]
                else:
                    console.print(f"[red]❌ 无效选择，请输入 1-{len(secretaries)}[/]")
            except ValueError:
                console.print(f"[red]❌ 请输入数字 1-{len(secretaries)}[/]")
    except ImportError:
        # 如果没有rich库，使用简单的文本选择
        print("\n请选择secretary:")
        for idx, sec in enumerate(secretaries, 1):
            name = sec.get("name", "unknown")
            desc = sec.get("description", "(无描述)")
            print(f"  {idx}. {name} - {desc}")
        
        while True:
            try:
                choice = input(f"\n请输入序号 (1-{len(secretaries)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(secretaries):
                    return secretaries[idx]["name"]
                else:
                    print(f"❌ 无效选择，请输入 1-{len(secretaries)}")
            except ValueError:
                print(f"❌ 请输入数字 1-{len(secretaries)}")
            except (EOFError, KeyboardInterrupt):
                return None


def _submit_task(request: str, min_time: int = 0, worker_name: str | None = None):
    """公用: 通过秘书Agent提交任务，可选嵌入最低执行时间元数据
    
    Args:
        request: 任务描述
        min_time: 最低执行时间（秒）
        worker_name: 如果指定，直接分配给该 worker，跳过秘书判断
    """
    if not request.strip():
        print("❌ 请提供任务描述")
        sys.exit(1)

    # 如果指定了 worker，直接写入该 worker 的 tasks 目录；否则交给下面写 secretary tasks
    if worker_name:
        from secretary.agents import get_worker, register_worker, _worker_tasks_dir
        import secretary.config as cfg
        
        # 确保 worker 存在
        worker = get_worker(worker_name)
        worker_created = False
        if not worker:
            print(f"ℹ️  Worker '{worker_name}' 不存在，自动创建...")
            register_worker(worker_name, description=f"由任务分配创建")
            worker = get_worker(worker_name)
            worker_created = True
        
        # 如果worker是新创建的，自动启动它的扫描器
        if worker_created:
            _start_agent_scanner(worker_name, "worker", silent=False)
        
        # 生成任务文件名
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 从请求中提取简短描述作为文件名
        task_name = request[:50].replace(" ", "-").replace("/", "-").replace("\\", "-")
        task_name = "".join(c for c in task_name if c.isalnum() or c in ("-", "_"))
        task_file_name = f"{task_name}-{timestamp}.md"
        
        # 创建任务文件
        tasks_dir = _worker_tasks_dir(worker_name)
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_file = tasks_dir / task_file_name
        
        # 写入任务内容
        task_content = f"""# 任务: {request[:100]}

## 描述
{request}

## 目标
完成用户指定的任务

## 工作区
待指定
"""
        if min_time > 0:
            task_content += f"\n<!-- min_time: {min_time} -->\n"
        
        task_file.write_text(task_content, encoding="utf-8")
        
        print(f"\n📨 任务已直接分配给 worker '{worker_name}'")
        print(f"   ✅ 任务文件: {worker_name}/{task_file_name}")
        if min_time > 0:
            print(f"   ⏱️ 最低执行时间: {min_time}s")
        return

    # 否则，通过秘书 Agent 提交（后台执行，输出到 secretary.log）
    from secretary.agents import list_workers, _worker_tasks_dir, get_worker
    import subprocess
    
    # 检查是否有 secretary，如果没有则提示（不再检查 kai，因为可能使用其他 secretary）
    secretaries = [w for w in list_workers() if w.get("type") == "secretary"]
    if not secretaries:
        print(t("error_no_secretary").format(name=_cli_name()))
        return
    
    # 收集所有 worker 的任务文件（用于检测新任务）
    before = {}
    for w in list_workers():
        wtd = _worker_tasks_dir(w["name"])
        if wtd.exists():
            for f in wtd.glob("*.md"):
                before[f"{w['name']}/{f.name}"] = f.stat().st_mtime
    
    # 使用第一个 secretary（或让用户选择，但这里简化处理）
    secretary_name = secretaries[0]["name"]
    
    print(f"\n📨 提交任务: {request}")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    
    # 使用 secretary 的日志目录（不再使用固定的 kai 日志目录）
    from secretary.agents import _worker_logs_dir
    log_dir = _worker_logs_dir(secretary_name)
    log_dir.mkdir(parents=True, exist_ok=True)
    secretary_log_file = log_dir / "secretary.log"
    
    print(f"   ⏳ 后台执行中，输出写入 {secretary_log_file}")
    print(f"   使用 `{_cli_name()} check {secretary_name}` 查看日志\n")
    
    # 构建命令（使用 shlex 正确处理带引号的任务描述）
    import shlex
    sub_cmd = [sys.executable, "-m", "secretary.cli", "task"] + shlex.split(request)
    if min_time > 0:
        sub_cmd.extend(["--time", str(min_time)])
    
    # 设置环境变量
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    # 后台执行
    fh = open(secretary_log_file, "a", encoding="utf-8")
    fh.write(f"# Task submitted: {request}\n")
    fh.write(f"# Min time: {min_time}s\n")
    fh.write(f"# Started: {datetime.now().isoformat()}\n\n")
    fh.flush()
    
    proc = subprocess.Popen(
        sub_cmd,
        stdout=fh,
        stderr=subprocess.STDOUT,
        cwd=str(cfg.BASE_DIR),
        env=env,
    )
    
    # 不等待进程完成，立即返回
    # min_time 的嵌入逻辑会在秘书完成后由后台进程处理
    # 由于是后台执行，无法立即等待，所以 min_time 的嵌入需要在后台进程中处理
    fh.close()
    
    # 注意：min_time 的嵌入逻辑现在由后台进程中的 run_secretary 处理
    # 这里不再需要等待和嵌入逻辑


def cmd_task(args):
    if not _is_workspace_configured(args):
        print(t("workspace_not_set_hint").format(name=_cli_name()))
    request = " ".join(args.request)
    worker_name = getattr(args, "worker", None)
    # 如果指定了 worker，直接写入任务文件（前台执行）
    if worker_name:
        _submit_task(request, min_time=args.time, worker_name=worker_name)
    else:
        # 检查是否有secretary和worker类型的agent
        from secretary.agents import list_workers, register_agent, get_worker, pick_available_name
        all_workers = list_workers()
        secretaries = [w for w in all_workers if w.get("type") == "secretary"]
        workers = [w for w in all_workers if w.get("type") == "worker"]
        
        # 如果没有secretary，自动创建一个（优先使用yks，如果被占用则选择其他可用名字）
        if len(secretaries) == 0:
            secretary_name = pick_available_name(preferred_names=["yks", "ykx", "yky", "aks", "akx"])
            if not get_worker(secretary_name):
                register_agent(secretary_name, agent_type="secretary", description="默认秘书Agent")
                print(f"   ✅ 已自动创建secretary: {secretary_name}")
                _start_agent_scanner(secretary_name, "secretary", silent=True)
            secretaries = [{"name": secretary_name, "type": "secretary"}]
        
        # 如果没有worker，自动创建一个（优先使用ykc，如果被占用则选择其他可用名字）
        if len(workers) == 0:
            worker_name = pick_available_name(preferred_names=["ykc", "ykz", "aky", "akz", "akc"])
            if not get_worker(worker_name):
                register_agent(worker_name, agent_type="worker", description="默认通用工人")
                print(f"   ✅ 已自动创建worker: {worker_name}")
                _start_agent_scanner(worker_name, "worker", silent=True)
        
        # 重新获取secretaries列表（可能刚创建了yks）
        secretaries = [w for w in list_workers() if w.get("type") == "secretary"]
        
        if len(secretaries) == 1:
            # 只有一个secretary，直接使用
            secretary_name = secretaries[0]["name"]
            task_file = _write_kai_task(request, min_time=args.time, secretary_name=secretary_name)
            print(f"\n📨 任务已提交到 {secretary_name}")
            print(f"   ✅ 任务文件: {task_file}")
            if args.time > 0:
                print(f"   ⏱️ 最低执行时间: {args.time}s")
            print(f"   💡 使用 `{_cli_name()} check {secretary_name}` 查看处理日志")
        else:
            # 多个secretary，显示TUI让用户选择
            secretary_name = _select_secretary(secretaries)
            if not secretary_name:
                print("❌ 未选择secretary，任务提交已取消")
                return
            task_file = _write_kai_task(request, min_time=args.time, secretary_name=secretary_name)
            print(f"\n📨 任务已提交到 {secretary_name}")
            print(f"   ✅ 任务文件: {task_file}")
            if args.time > 0:
                print(f"   ⏱️ 最低执行时间: {args.time}s")
            print(f"   💡 使用 `{_cli_name()} check {secretary_name}` 查看处理日志")


def cmd_boss(args):
    """创建并启动Boss Agent：监控指定worker，在队列为空时生成新任务"""
    import secretary.config as cfg
    import subprocess
    import os
    
    # 解析参数：boss <boss-name> task <worker-name> [number]
    # 新格式：boss name1 task name2 number
    boss_name = args.boss_name
    # goal 参数现在应该是固定关键字 "task"，实际目标从 goal.md 读取或使用默认
    goal_keyword = args.goal  # 应该是 "task"
    worker_name = args.worker_name or cfg.DEFAULT_WORKER_NAME
    max_executions = args.max_executions  # 执行次数限制，None 表示无限次
    
    # 如果 goal_keyword 不是 "task"，将其作为实际目标使用
    if goal_keyword != "task":
        goal = goal_keyword
    else:
        # 如果用户输入的是 "task"，尝试从已存在的 goal.md 读取，否则使用默认
        boss_dir = cfg.AGENTS_DIR / boss_name
        goal_file = boss_dir / "goal.md"
        if goal_file.exists():
            goal = goal_file.read_text(encoding="utf-8").strip()
            # 移除 markdown 标题
            lines = [l.strip() for l in goal.splitlines() if l.strip() and not l.strip().startswith("#")]
            goal = "\n".join(lines) if lines else goal
        else:
            goal = "推进项目目标"  # 默认目标
    
    # 检查boss_name是否已被使用（且不是boss类型）
    from secretary.agents import register_agent, get_worker
    existing_boss = get_worker(boss_name)
    if existing_boss and existing_boss.get("type") != "boss":
        print(f"⚠️  名字 '{boss_name}' 已被注册为 {existing_boss.get('type')} 类型，不能用作boss")
        print(f"   请使用其他名字或先解雇该agent")
        return
    
    # 确保worker存在（如果不存在则创建）
    # 检查是否试图将 secretary 类型的 agent 作为 worker
    from secretary.agents import get_worker
    existing_agent = get_worker(worker_name)
    if existing_agent and existing_agent.get("type") == "secretary":
        print(f"⚠️  '{worker_name}' 是 secretary 类型，不能作为 worker，请使用其他名称")
        return
    
    # 检查worker_name是否已被使用（且不是worker类型）
    existing_worker = get_worker(worker_name)
    if existing_worker and existing_worker.get("type") != "worker":
        print(f"⚠️  名字 '{worker_name}' 已被注册为 {existing_worker.get('type')} 类型，不能用作worker")
        print(f"   请使用其他名字或先解雇该agent")
        return
    
    worker_created = False
    if not existing_worker:
        register_agent(worker_name, agent_type="worker", description=f"由Boss {boss_name}监控的Worker")
        print(f"✅ 已创建worker: {worker_name}")
        worker_created = True
    
    # 如果worker是新创建的，自动启动它的扫描器
    if worker_created:
        _start_agent_scanner(worker_name, "worker", silent=False)
    
    # 创建boss目录和配置
    boss_dir = cfg.AGENTS_DIR / boss_name
    boss_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建必要的子目录
    (boss_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (boss_dir / "reports").mkdir(parents=True, exist_ok=True)
    (boss_dir / "logs").mkdir(parents=True, exist_ok=True)
    (boss_dir / "stats").mkdir(parents=True, exist_ok=True)
    
    # 写入目标文件（如果 goal 不是 "task"，使用用户提供的目标；否则使用默认）
    goal_file = boss_dir / "goal.md"
    if goal != "task" or not goal_file.exists():
        goal_file.write_text(f"# 持续目标\n\n{goal}\n", encoding="utf-8")
    
    # 写入配置文件
    config_file = boss_dir / "config.md"
    config_content = (
        f"# Boss配置\n\n"
        f"监控的Worker: {worker_name}\n"
        f"持续目标: {goal[:100]}...\n"
    )
    if max_executions is not None:
        config_content += f"最大执行次数: {max_executions}\n"
    config_file.write_text(config_content, encoding="utf-8")
    
    # 注册boss agent（检查是否已被其他类型占用）
    existing_boss_check = get_worker(boss_name)
    if existing_boss_check and existing_boss_check.get("type") != "boss":
        print(f"⚠️  名字 '{boss_name}' 已被注册为 {existing_boss_check.get('type')} 类型，不能用作boss")
        print(f"   请使用其他名字或先解雇该agent")
        return
    if not existing_boss_check:
        register_agent(boss_name, agent_type="boss", description=f"Boss: {goal[:50]}")
    
    # Boss不需要初始任务文件，因为它通过检查worker队列来触发
    # 使用统一的启动函数启动boss扫描器
    _start_agent_scanner(boss_name, "boss", silent=False)
    
    print(f"✅ Boss '{boss_name}' 已创建并启动")
    print(f"   持续目标: {goal}")
    print(f"   监控Worker: {worker_name}")




# ============================================================
#  技能系统
# ============================================================

def cmd_use_skill(args):
    """使用一个已学会的技能 — 直接写入 worker 的 tasks/ (跳过秘书，派发给 sen)"""
    from secretary.skills import invoke_skill, get_skill
    import secretary.config as cfg

    skill_name = args.skill_name
    info = get_skill(skill_name)
    if not info:
        print(f"❌ 未知技能: {skill_name}")
        print(f"   用 `{_cli_name()} skills` 查看所有已学技能")
        sys.exit(1)

    desc = info.get("description", "")
    print(f"\n🎯 使用技能: {skill_name}  {desc}")
    task_file = invoke_skill(skill_name, min_time=args.time)
    if task_file:
        print(f"   ✅ 任务已写入: {cfg.DEFAULT_WORKER_NAME}/{task_file.name}")
        print(f"   💡 使用 `{_cli_name()} hire` 招募工作者来执行")
    else:
        print(f"   ❌ 技能模板为空，请检查 skills/{skill_name}.md")
        sys.exit(1)


def cmd_learn(args):
    """学习一个新技能"""
    from secretary.skills import learn_skill, get_skill

    description = " ".join(args.description)
    skill_name = args.skill_name

    existing = get_skill(skill_name)
    if existing and existing.get("builtin"):
        print(f"   ⚠️ {skill_name} 是内置技能，将被覆盖为自定义版本")

    fp = learn_skill(skill_name, description)
    print(f"\n📚 学会了新技能: {skill_name}")
    print(f"   📄 文件: {fp}")
    print(f"   之后可以直接 `{_cli_name()} {skill_name}` 来使用！")
    print(f"   忘记: `{_cli_name()} forget {skill_name}`")


def cmd_forget(args):
    """忘掉一个技能"""
    from secretary.skills import forget_skill, get_skill

    skill_name = args.skill_name
    info = get_skill(skill_name)
    if not info:
        print(f"❌ 没有这个技能: {skill_name}")
        return

    if info.get("builtin"):
        print(f"   ⚠️ {skill_name} 是内置技能，忘了之后下次会自动恢复")

    success = forget_skill(skill_name)
    if success:
        print(f"🧹 已忘记技能: {skill_name}")
    else:
        print(f"❌ 删除失败: {skill_name}")


def cmd_skills(args):
    """列出所有已学技能"""
    from secretary.skills import list_skills

    skills = list_skills()
    name = _cli_name()

    if not skills:
        print(f"\n📚 还没有学会任何技能")
        print(f"   用 `{name} learn \"任务描述\" skill-name` 来教我！")
        return

    print(f"\n📚 已学技能 ({len(skills)} 个):\n")
    for s in skills:
        tag = "📦" if s["builtin"] else "🎓"
        desc = s["description"] or "(无描述)"
        print(f"   {tag} {s['name']:20s}  {desc}")

    print(f"\n   📦 = 内置技能   🎓 = 已学技能")
    print(f"   使用: {name} <技能名>")
    print(f"   学习: {name} learn \"描述\" <名字>")
    print(f"   忘记: {name} forget <名字>")


# ============================================================
#  后台服务
# ============================================================

def cmd_hire(args):
    """招募并启动agent：hire <name> <type>，type 可以是 secretary / worker / recycler"""
    from secretary.agents import pick_random_name, register_agent, get_worker
    import secretary.config as cfg

    # 解析参数：hire <name> [type] 或 hire <name> <type> --description "描述"
    # worker_names 来自解析器：不填则 []，hire alice -> [alice]，hire alice worker -> [alice, worker]
    worker_names = getattr(args, "worker_names", None)
    # 确保 worker_names 是列表
    if worker_names is None:
        names = []
    elif isinstance(worker_names, list):
        names = worker_names
    else:
        # 如果不是列表，尝试转换为列表
        names = [worker_names] if worker_names else []
    
    # 支持的类型关键词
    valid_types = ("secretary", "worker", "recycler")
    
    # 识别参数中的类型关键词和名称
    agent_type = "worker"  # 默认类型
    agent_name = None
    
    for arg in names:
        arg_lower = arg.lower()
        if arg_lower in valid_types:
            # 如果参数是类型关键词，设置为类型
            agent_type = arg_lower
        else:
            # 如果参数不是类型关键词，作为名称（只取第一个非类型参数作为名称）
            if agent_name is None:
                agent_name = arg
    
    # 如果没有找到名称，随机生成一个
    if agent_name is None:
        agent_name = pick_random_name()
    
    description = getattr(args, "description", None) or ""

    # 检查是否已存在
    existing = get_worker(agent_name)
    if existing:
        print(f"ℹ️  Agent '{agent_name}' 已存在")
        print(f"   类型: {existing.get('type', 'unknown')}")
        return

    # 注册agent
    register_agent(agent_name, agent_type=agent_type, description=description)
    print(f"✅ 已注册 {agent_type} agent: {agent_name}")

    # 长时间操作提示，再启动
    print(t("msg_starting_agent").format(agent_name=agent_name, agent_type=agent_type))
    _start_agent_scanner(agent_name, agent_type, silent=False)




def cmd_workers(args):
    """列出当前工作区内已注册的 agent（名称、类型、PID、状态等），与 kai monitor --text 对齐"""
    if not _is_workspace_configured(args):
        print(t("workspace_not_set_hint").format(name=_cli_name()))
        return
    from secretary.agents import list_workers

    workers = list_workers()
    name = _cli_name()

    # 同步进程队列以便 PID 准确
    _sync_processes_to_queue()
    active_procs = _get_active_processes()
    proc_pid_map = {p.get("name"): p.get("pid") for p in active_procs}

    type_icons = {
        "secretary": "🤖",
        "worker": "👷",
        "boss": "👔",
        "recycler": "♻️",
    }
    status_icons = {"idle": "💤", "busy": "⚙️", "offline": "📴"}

    print(f"\n📋 {name} 已注册 Agent")
    print(f"   工作区: {cfg.BASE_DIR}\n")
    if not workers:
        print("   (无 agent，使用 kai hire 招募)")
        return
    # 表头与 monitor --text 列对齐，增加 PID
    print(f"{'Agent':<18} {'类型':<12} {'执行中':<8} {'已完成':<8} {'状态':<4} {'PID':<8}")
    print("-" * 62)
    for w in workers:
        agent_name = w.get("name", "unknown")
        agent_type = w.get("type", "unknown")
        executing = w.get("executing", False)
        completed = w.get("completed_tasks", 0)
        status_icon = status_icons.get(w.get("status", ""), "❓")
        type_icon = type_icons.get(agent_type, "❓")
        pid = proc_pid_map.get(agent_name) or w.get("pid")
        pid_display = str(pid) if pid else "-"
        exec_display = "✓" if executing else "✗"
        print(f"{agent_name:<18} {type_icon} {agent_type:<10} {exec_display:<8} {completed:<8} {status_icon:<4} {pid_display:<8}")
    print(f"\n   💡 查看日志: {name} check <名>  |  监控: {name} monitor  |  解雇: {name} fire <名>\n")


def cmd_fire(args):
    """解雇 (删除) 一个或多个命名工人，或使用 'all' 解雇所有agent"""
    from secretary.agents import get_worker, remove_worker, list_workers, update_worker_status

    # 检查是否是 "all"
    worker_names = args.worker_names
    if len(worker_names) == 1 and worker_names[0].lower() == "all":
        # 解雇所有agent
        all_workers = list_workers()
        if not all_workers:
            print("ℹ️  没有已注册的agent")
            return
        
        print(f"⚠️  即将解雇所有 {len(all_workers)} 个agent")
        # 确认（可选，或者直接执行）
        worker_names = [w["name"] for w in all_workers]
    else:
        worker_names = args.worker_names

    for worker_name in worker_names:
        info = get_worker(worker_name)
        if not info:
            print(f"❌ 没有叫 {worker_name} 的agent")
            print(f"   用 `{_cli_name()} workers` 查看所有agent")
            continue

        if info.get("ongoing_count", 0) > 0:
            print(f"⚠️  {worker_name} 还有 {info['ongoing_count']} 个任务在执行中!")
            print(f"   将强制停止进程并解雇")

        # 1. 先停止进程（如果存在）
        pid = info.get("pid")
        if pid and _check_process_exists(pid):
            print(f"   停止 {worker_name} 的进程 (PID={pid})...")
            _stop_process(pid, worker_name, verbose=False)
        
        # 2. 从进程队列中移除
        _remove_process(agent_name=worker_name)
        
        # 3. 更新 agents.json（虽然要删除，但先清理干净）
        update_worker_status(worker_name, "idle", pid=None)
        
        # 4. 删除注册信息和目录
        success = remove_worker(worker_name)
        if success:
            print(f"🔥 已解雇agent: {worker_name}")
            print(f"   已停止进程、删除目录及注册信息")
        else:
            print(f"❌ 解雇失败: {worker_name}")


def cmd_recycle(args):
    """启动回收者：复用 hire/start 体系。未注册则等价 hire recycler recycler，未运行则 _start_agent_scanner。"""
    from secretary.agent_types.recycler import run_recycler
    from secretary.agents import get_worker, register_agent
    import os

    recycler_name = "recycler"

    # 已在后台子进程中，直接执行 recycler 主循环
    if os.environ.get("KAI_RECYCLE_BACKGROUND") == "1":
        run_recycler(once=args.once, verbose=False)
        return

    # --once：前台执行一次后退出，不 spawn 后台进程
    if args.once:
        print(f"\n♻️ 回收者（单次执行）\n")
        run_recycler(once=True, verbose=True)
        return

    # 确保存在名为 recycler 的 agent（未注册则等价 hire recycler recycler）
    existing = get_worker(recycler_name)
    if not existing:
        register_agent(recycler_name, agent_type="recycler", description="回收者：审查报告")
        print(f"✅ 已注册 recycler agent: {recycler_name}")

    # 若已在运行则不再启动
    pid = existing.get("pid") if existing else None
    if pid and _check_process_exists(pid):
        print(f"ℹ️  回收者已在运行 (PID={pid})")
        return

    # 未运行则启动（与 start 逻辑一致）
    print(t("msg_starting_recycler"))
    _start_agent_scanner(recycler_name, "recycler", silent=False)


def cmd_monitor(args):
    """启动实时监控面板；--text/--once 时输出文本状态并退出，否则尝试 TUI（无 TUI 时退化为文本）"""
    if not _is_workspace_configured(args):
        print(t("workspace_not_set_hint").format(name=_cli_name()))
    from secretary.ui.dashboard import run_monitor
    import subprocess
    import os

    text_mode = getattr(args, "text", False)
    once = getattr(args, "once", False)

    # 文本模式或单次快照：前台执行，输出与旧 status 等价的文本后退出
    if text_mode or once:
        run_monitor(
            refresh_interval=args.interval,
            text_mode=text_mode,
            once=once,
        )
        return

    # TUI 模式：前台执行（不 spawn 子进程），便于用户直接与面板交互
    print(t("msg_starting_monitor"))
    print(f"   刷新间隔 {args.interval}s，Ctrl+C 退出\n")
    run_monitor(refresh_interval=args.interval)


# ============================================================
#  控制命令
# ============================================================

def _stop_process(pid: int, name: str, verbose: bool = True):
    """停止指定 PID 的进程（辅助函数，供fire使用）"""
    import signal
    try:
        if sys.platform == "win32":
            # Windows: 使用 taskkill
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                if verbose:
                    print(f"   ✅ 进程 {pid} 已停止")
            else:
                # 检查进程是否还存在
                check_result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    timeout=5,
                )
                if check_result.returncode == 0 and check_result.stdout:
                    try:
                        output = check_result.stdout.decode("gbk", errors="ignore")
                        if str(pid) in output and "信息" not in output:
                            if verbose:
                                print(f"   ⚠️  无法停止进程 PID={pid}，进程仍在运行")
                        else:
                            if verbose:
                                print(f"   ✅ 进程 {pid} 已停止")
                    except:
                        if str(pid).encode() in check_result.stdout:
                            if verbose:
                                print(f"   ⚠️  无法停止进程 PID={pid}，进程仍在运行")
                        else:
                            if verbose:
                                print(f"   ✅ 进程 {pid} 已停止")
                else:
                    if verbose:
                        print(f"   ✅ 进程 {pid} 已停止")
        else:
            # Unix/Linux: 使用 kill
            try:
                os.kill(pid, signal.SIGTERM)
                if verbose:
                    print(f"   ✅ 已发送停止信号给 {name} (PID={pid})")
                # 等待一下，如果还没停止就强制杀死
                import time
                time.sleep(1)
                try:
                    os.kill(pid, 0)  # 检查进程是否还存在
                    os.kill(pid, signal.SIGKILL)
                    if verbose:
                        print(f"   ✅ 已强制停止 {name} (PID={pid})")
                except ProcessLookupError:
                    pass  # 进程已停止
            except ProcessLookupError:
                if verbose:
                    print(f"   ℹ️  进程 PID={pid} 已不存在")
    except Exception as e:
        if verbose:
            print(f"   ⚠️  停止进程时出错: {e}")


def _cleanup_all_processes():
    """
    清理所有正在运行的agent扫描进程并更新agents.json
    在退出交互模式时调用
    注意：只停止进程，不删除文件夹，保留agents.json中的agent记录，只标记为idle
    """
    from secretary.agents import list_workers, update_worker_status
    
    workers = list_workers()
    stopped_count = 0
    updated_count = 0
    
    print("🛑 正在停止所有扫描进程（保留agent配置）...")
    
    # 先同步agents.json中的进程到队列（确保队列完整）
    _sync_processes_to_queue()
    
    # 遍历所有进程队列中的进程（包括已崩溃的）
    # 使用 list() 创建副本，避免在遍历时修改队列
    all_procs = list(_active_processes)
    processed_names = set()
    
    for proc_info in all_procs:
        proc_name = proc_info.get("name")
        proc_pid = proc_info.get("pid")
        
        if not proc_pid:
            continue
        
        # 避免重复处理（可能队列中有重复项）
        if proc_name in processed_names:
            continue
        processed_names.add(proc_name)
        
        # 检查进程是否真的存在
        if _check_process_exists(proc_pid):
            # 进程存在，停止它
            print(f"   停止 {proc_name} (PID={proc_pid})...")
            _stop_process(proc_pid, proc_name, verbose=False)
            stopped_count += 1
        else:
            # 进程不存在（可能已崩溃），只清理记录
            pass
        
        # 更新agents.json中的状态为idle，保留agent记录
        for worker in workers:
            if worker.get("name") == proc_name:
                update_worker_status(proc_name, "idle", pid=None)
                updated_count += 1
                break
        
        # 从队列中移除
        _remove_process(agent_name=proc_name)
    
    # 清理agents.json中其他有PID但不在队列中的记录
    for worker in workers:
        worker_name = worker.get("name")
        pid = worker.get("pid")
        status = worker.get("status", "unknown")
        
        if not pid:
            # 没有PID，但可能status不是idle，确保更新为idle（保留agent记录）
            if status != "idle":
                update_worker_status(worker_name, "idle", pid=None)
                updated_count += 1
            continue
        
        # 如果不在已处理的列表中，说明可能已经清理过了，或者需要清理
        if worker_name not in processed_names:
            # 检查进程是否真的存在
            if not _check_process_exists(pid):
                # 进程不存在，清理记录并更新agents.json为idle（保留agent记录）
                update_worker_status(worker_name, "idle", pid=None)
                updated_count += 1
            else:
                # 进程存在但不在队列中，停止它
                print(f"   停止 {worker_name} (PID={pid})...")
                _stop_process(pid, worker_name, verbose=False)
                update_worker_status(worker_name, "idle", pid=None)
                stopped_count += 1
                updated_count += 1
    
    # 清空进程队列
    _active_processes.clear()
    
    if stopped_count > 0 or updated_count > 0:
        print(f"   ✅ 已停止 {stopped_count} 个进程，更新 {updated_count} 个agent状态为idle")
        print(f"   📁 Agent配置和文件夹已保留，下次启动时会自动恢复")
    else:
        print(f"   ℹ️  没有需要停止的进程")


def cmd_check(args):
    """实时查看 worker 或秘书的输出（类似 tail -f）"""
    from secretary.agents import get_worker, _worker_logs_dir, update_worker_status
    import threading
    import time
    
    worker_name = getattr(args, "worker_name", None)
    if not worker_name:
        print("❌ 请指定要查看的对象: agent 名称")
        print(f"   用法: {_cli_name()} check <agent_name>")
        print(f"   示例: {_cli_name()} check sen  |  {_cli_name()} check yks")
        return
    
    # 检查 agent 是否存在（统一处理所有类型，包括secretary）
    worker = get_worker(worker_name)
    if not worker:
        print(f"❌ Agent '{worker_name}' 不存在")
        print(t("error_agent_not_found").format(name=_cli_name()))
        return
    
    # 检查 agent 是否在运行
    pid = worker.get("pid")
    pid_info = ""
    if pid and _check_process_exists(pid):
        pid_info = f" (PID={pid})"
    else:
        agent_type = worker.get("type", "agent")
        print(f"ℹ️  {agent_type.capitalize()} '{worker_name}' 没有运行中的进程")
        print(f"   使用 `{_cli_name()} hire {worker_name} {agent_type}` 启动")
        # 即使没有运行，也允许查看日志
    
    # 统一使用 agents/<name>/logs/scanner.log
    log_dir = _worker_logs_dir(worker_name)
    if not log_dir.exists():
        print(f"❌ Agent '{worker_name}' 的日志目录不存在")
        return
    
    log_file = log_dir / "scanner.log"
    if not log_file.exists():
        print(f"❌ Agent '{worker_name}' 没有找到日志文件 (scanner.log)")
        return
    
    agent_type = worker.get("type", "agent")
    print(f"\n📺 实时查看 {agent_type} '{worker_name}' 的输出{pid_info}")
    print(f"   日志文件: {log_file}")
    print(f"   按 'q' 退出查看模式（不打断进程）")
    print(f"   按 Ctrl+C 打断进程执行")
    print(f"{'='*60}\n")
    
    # 用于控制循环的标志
    should_exit = threading.Event()
    should_stop_worker = threading.Event()
    
    def read_log():
        """读取日志并实时显示"""
        try:
            # 先读取已有内容（可选：只显示最后几行）
            tail_lines = getattr(args, "tail", None)
            if tail_lines and tail_lines > 0:
                # 读取最后 N 行
                try:
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        for line in lines[-tail_lines:]:
                            print(line.rstrip())
                except Exception:
                    pass
            
            # 实时跟踪新内容（类似 tail -f）
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                # 如果不需要 tail，先读取所有已有内容
                if not (tail_lines and tail_lines > 0):
                    content = f.read()
                    if content:
                        print(content, end="")
                
                # 实时跟踪新内容
                while not should_exit.is_set():
                    line = f.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        # 检查文件是否被截断或重新创建
                        try:
                            current_size = log_file.stat().st_size
                            if f.tell() > current_size:
                                # 文件被截断，重新打开
                                f.seek(0)
                        except Exception:
                            pass
                        time.sleep(0.1)  # 短暂休眠，避免 CPU 占用过高
        except Exception as e:
            if not should_exit.is_set():
                print(f"\n⚠️  读取日志时出错: {e}")
    
    def read_input():
        """监听键盘输入"""
        if sys.platform == "win32":
            # Windows: 使用 msvcrt
            try:
                import msvcrt
                while not should_exit.is_set():
                    if msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key == b'q' or key == b'Q':
                            should_exit.set()
                            break
                        elif key == b'\x03':  # Ctrl+C
                            should_stop_worker.set()
                            should_exit.set()
                            break
                    time.sleep(0.1)
            except ImportError:
                # 如果 msvcrt 不可用，提示用户使用 Ctrl+C
                print("   ⚠️  键盘输入监听不可用，请使用 Ctrl+C 退出")
                while not should_exit.is_set():
                    time.sleep(0.1)
            except KeyboardInterrupt:
                should_stop_worker.set()
                should_exit.set()
            except Exception:
                # 其他错误，继续运行（至少 Ctrl+C 能工作）
                while not should_exit.is_set():
                    time.sleep(0.1)
        else:
            # Unix/Linux: 尝试使用 termios，如果失败则使用简单方式
            try:
                import select
                import termios
                import tty
                
                # 设置终端为原始模式
                old_settings = termios.tcgetattr(sys.stdin)
                try:
                    tty.setraw(sys.stdin.fileno())
                    while not should_exit.is_set():
                        if select.select([sys.stdin], [], [], 0.1)[0]:
                            key = sys.stdin.read(1)
                            if key == 'q' or key == 'Q':
                                should_exit.set()
                                break
                            elif key == '\x03':  # Ctrl+C
                                should_stop_worker.set()
                                should_exit.set()
                                break
                finally:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except (ImportError, OSError, AttributeError):
                # 如果 termios 不可用，使用简单方式（只支持 Ctrl+C）
                pass
    
    # 启动日志读取线程
    log_thread = threading.Thread(target=read_log, daemon=True)
    log_thread.start()
    
    # 启动输入监听线程
    input_thread = threading.Thread(target=read_input, daemon=True)
    input_thread.start()
    
    try:
        # 等待退出信号
        while not should_exit.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        # Ctrl+C 被捕获，停止 worker
        should_stop_worker.set()
        should_exit.set()
    
    # 如果用户按了 Ctrl+C，停止 agent（仅当 agent 有 PID 时）
    agent_type = worker.get("type", "agent")
    if should_stop_worker.is_set() and pid:
        print(f"\n\n🛑 正在停止 {agent_type} '{worker_name}' (PID={pid})...")
        # 调用 stop 命令的逻辑
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, 15)  # SIGTERM
                time.sleep(1)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, 9)  # SIGKILL
                except ProcessLookupError:
                    pass
            
            # 更新 agent 状态
            update_worker_status(worker_name, "idle", pid=None)
            print(f"   ✅ {agent_type.capitalize()} '{worker_name}' 已停止")
        except Exception as e:
            print(f"   ⚠️  停止 {agent_type} 时出错: {e}")
    else:
        if pid:
            print(f"\n\n👋 退出查看模式（{agent_type} '{worker_name}' 继续运行）")
        else:
            print(f"\n\n👋 退出查看模式")


def cmd_clean_logs(args):
    """清空所有 agent 的 logs/ 目录下的日志文件"""
    removed = 0
    if cfg.AGENTS_DIR.exists():
        for agent_dir in cfg.AGENTS_DIR.iterdir():
            if not agent_dir.is_dir():
                continue
            logs_dir = agent_dir / "logs"
            if not logs_dir.exists():
                continue
            for f in logs_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        removed += 1
                    except OSError as e:
                        print(f"   ⚠️ 删除失败 {f.name}: {e}")
    print(f"🧹 已清理 logs/ 下 {removed} 个日志文件")


def cmd_clean_processes(args):
    """清理泄露的 worker 进程（检查并清理无效的 PID 记录）"""
    from secretary.agents import list_workers, update_worker_status
    import os
    
    workers = list_workers()
    cleaned = 0
    
    print("\n🔍 检查 worker 进程状态...")
    
    for worker in workers:
        worker_name = worker.get("name")
        pid = worker.get("pid")
        status = worker.get("status", "unknown")
        
        if not pid:
            continue  # 没有 PID，跳过
        
        # 检查进程是否存在
        process_exists = False
        try:
            if sys.platform == "win32":
                # Windows: 使用 tasklist 检查
                check_result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    timeout=5,
                )
                if check_result.returncode == 0 and check_result.stdout:
                    try:
                        output = check_result.stdout.decode("gbk", errors="ignore")
                        if str(pid) in output and "信息" not in output:
                            process_exists = True
                    except:
                        if str(pid).encode() in check_result.stdout:
                            process_exists = True
            else:
                # Unix/Linux: 使用 os.kill(pid, 0) 检查
                os.kill(pid, 0)
                process_exists = True
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            process_exists = False
        
        if not process_exists:
            # 进程不存在，但 workers.json 中还有 PID 记录，清理它
            print(f"   🧹 Worker '{worker_name}': PID={pid} 已不存在，清理记录")
            update_worker_status(worker_name, "idle", pid=None)
            cleaned += 1
        else:
            print(f"   ✅ Worker '{worker_name}': PID={pid} 正在运行")
    
    if cleaned == 0:
        print("\n✅ 没有发现泄露的进程记录")
    else:
        print(f"\n🧹 已清理 {cleaned} 个无效的进程记录")


# ============================================================
#  base 命令 — 设定/查看工作区
# ============================================================

def cmd_base(args):
    """设定或查看工作区目录（仅当前交互会话生效，不持久化）"""
    name = _cli_name()

    if args.path is None:
        print(f"\n📁 {name} 工作区配置（当前会话）")
        print(f"   当前生效: {cfg.WORKSPACE}")
        print(f"   系统目录: {cfg.BASE_DIR}")
        print(f"\n   用法:")
        print(f"     {name} base .           设为当前目录")
        print(f"     {name} base /path/to    设为指定路径")
        print(f"     {name} base --clear     清除设定 (回到使用 CWD)")
        print(f"\n   注意: base 命令仅在当前交互会话中生效，退出后恢复默认。")
        return

    if args.path == "--clear":
        # 清除当前会话的工作区设定，回到默认（CWD）
        default_ws = Path.cwd().resolve()
        cfg.apply_workspace(default_ws)
        print(f"   ✅ 已清除工作区设定，当前使用: {default_ws}")
        return

    new_path = Path(args.path).resolve()
    cfg.apply_workspace(new_path)
    cfg.ensure_dirs()
    print(f"\n   ✅ 工作区已设定（当前会话）: {new_path}")
    print(f"   📂 已创建目录结构 (tasks/, ongoing/, reports/, logs/, skills/ ...)")
    print(f"\n   注意: 此设定仅在当前交互会话中生效，退出后恢复默认。")


# ============================================================
#  name 命令 — 改名
# ============================================================

def cmd_name(args):
    """给 CLI 命令改名"""
    new_name = args.new_name
    old_name = _cli_name()

    if not new_name.isidentifier() and not new_name.replace("-", "").isalnum():
        print(f"❌ 无效的命令名: {new_name}")
        print(f"   命令名只能包含字母、数字和连字符")
        return

    if new_name == old_name:
        print(f"   ℹ️ 当前已经叫 {old_name} 了")
        return

    print(f"\n🏷️  改名: {old_name} → {new_name}")

    set_cli_name(new_name)

    print(f"\n   现在可以用 `{new_name}` 来调用我了！")
    print(f"   例如: {new_name} task \"你好\"")
    print(f"         {new_name} monitor")


def cmd_model(args):
    """设置或查看默认模型（支持环境变量 CURSOR_MODEL 优先）"""
    from secretary.settings import get_model, set_model
    name = _cli_name()

    if args.model_name is None:
        # 查看当前模型
        current = get_model()
        env_model = os.environ.get("CURSOR_MODEL")
        print(f"\n🤖 {name} 模型配置")
        if env_model:
            print(f"   配置文件: {current}")
            print(f"   环境变量 (CURSOR_MODEL): {env_model} (优先)")
            print(f"   实际使用: {env_model}")
        else:
            print(f"   当前模型: {current}")
        print(f"\n   用法:")
        print(f"     {name} model Auto         设置为 Auto (自动选择)")
        print(f"     {name} model gpt-4       设置为 gpt-4")
        print(f"     {name} model claude-3    设置为 claude-3")
        return

    # 设置模型
    new_model = args.model_name
    old_model = get_model()
    if new_model == old_model:
        print(f"   ℹ️ 当前已经是 {old_model} 了")
        return
    print(f"\n🤖 设置模型: {old_model} → {new_model}")
    set_model(new_model)
    print(f"   ✅ 已保存，后续任务将使用 {new_model} 模型")


# ============================================================
#  report 命令
# ============================================================

# ============================================================
#  target 命令 — 秘书全局目标
# ============================================================

def cmd_target(args):
    """创建Boss Agent的别名：target "tasks" = boss yks "tasks" ykc"""
    # 解析参数：target "目标描述"
    goal = " ".join(args.goal) if isinstance(args.goal, list) else args.goal
    
    if not goal:
        print(f"❌ 请提供目标描述")
        print(f"   用法: {_cli_name()} target \"目标描述\"")
        print(f"   示例: {_cli_name()} target \"完成登录模块\"")
        return
    
    # 使用智能名字选择，优先使用yks和ykc，如果被占用则选择其他可用名字
    import secretary.config as cfg
    from secretary.agents import register_agent, get_worker, pick_available_name
    
    # 选择boss名称（优先yks，如果被占用则选择其他）
    boss_name = pick_available_name(preferred_names=["yks", "ykx", "yky", "aks", "akx"])
    
    # 选择worker名称（优先ykc，如果被占用则选择其他，但要确保和boss_name不同）
    worker_candidates = ["ykc", "ykz", "aky", "akz", "akc"]
    # 如果boss_name在候选列表中，移除它
    worker_candidates = [n for n in worker_candidates if n != boss_name]
    worker_name = pick_available_name(preferred_names=worker_candidates)
    
    # 确保worker存在（如果不存在则创建）
    worker_created = False
    if not get_worker(worker_name):
        register_agent(worker_name, agent_type="worker", description=f"由Boss {boss_name}监控的Worker")
        print(f"✅ 已创建worker: {worker_name}")
        worker_created = True
    
    # 如果worker是新创建的，自动启动它的扫描器
    if worker_created:
        _start_agent_scanner(worker_name, "worker", silent=False)
    
    # 创建boss目录和配置
    boss_dir = cfg.AGENTS_DIR / boss_name
    boss_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建必要的子目录
    (boss_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (boss_dir / "reports").mkdir(parents=True, exist_ok=True)
    (boss_dir / "logs").mkdir(parents=True, exist_ok=True)
    (boss_dir / "stats").mkdir(parents=True, exist_ok=True)
    
    # 写入目标文件（如果 goal 不是默认值，使用用户提供的目标）
    goal_file = boss_dir / "goal.md"
    if not goal_file.exists() or goal != "推进项目目标":
        goal_file.write_text(f"# 持续目标\n\n{goal}\n", encoding="utf-8")
    
    # 写入配置文件
    config_file = boss_dir / "config.md"
    config_file.write_text(
        f"# Boss配置\n\n"
        f"监控的Worker: {worker_name}\n"
        f"持续目标: {goal[:100]}...\n",
        encoding="utf-8"
    )
    
    # 注册boss agent（如果不存在，检查是否已被其他类型占用）
    existing_boss = get_worker(boss_name)
    if not existing_boss:
        register_agent(boss_name, agent_type="boss", description=f"Boss: {goal[:50]}")
    elif existing_boss.get("type") != "boss":
        # 如果名字已被其他类型占用，选择新名字
        from secretary.agents import pick_available_name
        new_boss_candidates = ["yks", "ykx", "yky", "aks", "akx"]
        new_boss_candidates = [n for n in new_boss_candidates if n != boss_name and n != worker_name]
        boss_name = pick_available_name(preferred_names=new_boss_candidates)
        # 确保新名字和worker_name不同
        while boss_name == worker_name:
            boss_name = pick_available_name(preferred_names=new_boss_candidates)
        if not get_worker(boss_name):
            register_agent(boss_name, agent_type="boss", description=f"Boss: {goal[:50]}")
        # 更新boss_dir路径和配置文件
        boss_dir = cfg.AGENTS_DIR / boss_name
        boss_dir.mkdir(parents=True, exist_ok=True)
        (boss_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (boss_dir / "reports").mkdir(parents=True, exist_ok=True)
        (boss_dir / "logs").mkdir(parents=True, exist_ok=True)
        (boss_dir / "stats").mkdir(parents=True, exist_ok=True)
        goal_file = boss_dir / "goal.md"
        goal_file.write_text(f"# 持续目标\n\n{goal}\n", encoding="utf-8")
        config_file = boss_dir / "config.md"
        config_file.write_text(
            f"# Boss配置\n\n"
            f"监控的Worker: {worker_name}\n"
            f"持续目标: {goal[:100]}...\n",
            encoding="utf-8"
        )
    
    # 使用统一的启动函数启动boss扫描器
    _start_agent_scanner(boss_name, "boss", silent=False)
    
    print(f"✅ Boss '{boss_name}' 已创建并启动")
    print(f"   持续目标: {goal}")
    print(f"   监控Worker: {worker_name}")


# ============================================================
#  help 命令
# ============================================================

def cmd_help(args):
    """显示帮助信息"""
    import sys
    import io
    
    # 确保输出使用UTF-8编码
    if sys.stdout.encoding != 'utf-8':
        # 如果stdout不是UTF-8,尝试重新配置
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            # Python < 3.7 或无法重新配置时,使用TextIOWrapper
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    name = _cli_name()
    
    # 如果指定了命令名,显示该命令的详细帮助
    if args.command_name:
        cmd_name = args.command_name.lower()
        
        # 命令帮助字典
        cmd_helps = {
            "task": f"""
📝 提交任务

用法:
  {name} task "任务描述" [--time 秒数] [--worker 名称]

示例:
  {name} task "实现HTTP服务器"
  {name} task "优化性能" --time 120
""",
            "boss": f"""
👔 创建 Boss Agent

用法:
  {name} boss <名称> "目标" <worker名称>

示例:
  {name} boss myboss "完成登录模块" sen
""",
            "use": f"""
🎯 使用技能

用法:
  {name} use <技能名> [--time 秒数]

示例:
  {name} use evolving
""",
            "learn": f"""
📖 学习技能

用法:
  {name} learn "任务描述" <技能名>

示例:
  {name} learn "分析性能瓶颈" performance
""",
            "forget": f"""
🧹 忘记技能

用法:
  {name} forget <技能名>
""",
            "skills": f"""
📚 列出技能

用法:
  {name} skills
""",
            "hire": f"""
👷 招募 agent

用法:
  {name} hire [<名字>] [<类型>] [-d "描述"]

示例:
  {name} hire alice worker
  {name} hire recycler recycler
""",
            "fire": f"""
🔥 解雇 agent

用法:
  {name} fire <名称>
  {name} fire all         解雇所有 agent
""",
            "workers": f"""
📋 列出 agent

用法:
  {name} workers

说明:
  列出当前工作区内已注册的 agent（名称、类型、执行中、已完成、状态、PID），与 monitor --text 列对齐。
""",
            "recycle": f"""
♻️ 启动回收者

用法:
  {name} recycle [--once]
""",
            "monitor": f"""
📺 监控面板

用法:
  {name} monitor [--text] [--once] [-i 秒数]
""",
            "check": f"""
📺 查看日志

用法:
  {name} check <agent名称> [--tail 行数]
""",
            "clean-logs": f"""
🧹 清理日志

用法:
  {name} clean-logs
""",
            "clean-processes": f"""
🧹 清理进程记录

用法:
  {name} clean-processes
""",
            "base": f"""
📁 设定/查看工作区

用法:
  {name} base
  {name} base .
  {name} base /path/to/project
  {name} base --clear

说明:
  设定或查看工作区目录。工作区是所有任务、报告、技能等数据的存储位置。

参数:
  path             工作区路径 (. = 当前目录, --clear = 清除)

示例:
  {name} base .
  {name} base /home/user/projects/myapp
  {name} base --clear
""",
            "name": f"""
🏷️ 改名

用法:
  {name} name <新名字>

说明:
  给CLI命令改个新名字。

参数:
  new_name         新的命令名 (必需)

示例:
  {name} name lily
  {name} name my-secretary
""",
            "model": f"""
🤖 设置或查看模型

用法:
  {name} model
  {name} model Auto
  {name} model gpt-4
  {name} model claude-3

说明:
  设置或查看默认使用的AI模型。

参数:
  model_name       模型名称 (可选,不指定则查看当前设置)

示例:
  {name} model
  {name} model Auto
  {name} model gpt-4
""",
            "target": f"""
🎯 创建 Boss (快捷方式)

用法:
  {name} target "目标描述"
""",
            "help": f"""
❓ 帮助

用法:
  {name} help [命令名]
""",
        }
        
        if cmd_name in cmd_helps:
            print(cmd_helps[cmd_name])
        else:
            print(f"❌ 未知命令: {cmd_name}")
            print(f"\n可用命令列表:")
            _print_command_list(name)
            print(f"\n使用 '{name} help' 查看所有命令")
            print(f"使用 '{name} help <命令名>' 查看特定命令的详细帮助")
        return
    
    # 显示通用帮助信息（开头突出快速开始与常用命令）
    print(f"\n{name} — 基于 Agent 的自动化任务系统\n")
    print(f"   {t('help_quick_start_line').format(name=name)}")
    print(f"   {t('help_common_commands')}\n")
    _print_command_list(name)
    print(f"\n💡 使用 '{name} help <命令名>' 查看详细帮助\n")

def _print_command_list(name: str):
    """打印命令列表"""
    commands = [
        ("📝 任务相关", [
            ("task", "提交任务（经秘书分配或指定 worker）"),
            ("boss", "创建并启动 Boss Agent，监控 worker 并在队列空时生成任务"),
        ]),
        ("📚 技能相关", [
            ("skills", "列出所有已学技能"),
            ("learn", "学习新技能"),
            ("forget", "忘掉一个技能"),
            ("use", "使用技能（直接写入 tasks/）"),
        ]),
        ("👷 Worker管理", [
            ("hire", "招募并启动 agent（secretary/worker/recycler）"),
            ("fire", "解雇 agent"),
            ("workers", "列出已注册的 agent"),
            ("check", "实时查看 agent 日志输出"),
        ]),
        ("♻️ 后台服务", [
            ("recycle", "启动回收者（审查报告）"),
            ("monitor", "实时监控面板；--text/--once 文本快照"),
        ]),
        ("⚙️ 设置", [
            ("base", "设定/查看工作区目录"),
            ("name", "给CLI命令改名"),
            ("model", "设置或查看AI模型"),
            ("target", "创建Boss Agent的别名"),
        ]),
        ("🧹 清理", [
            ("clean-logs", "清理日志文件"),
            ("clean-processes", "清理泄露的进程记录"),
        ]),
        ("❓ 帮助", [
            ("help", "显示帮助信息"),
        ]),
    ]
    
    for category, cmds in commands:
        print(f"{category}:")
        for cmd, desc in cmds:
            print(f"  {name} {cmd:<12} {desc}")


# ============================================================
#  交互模式
# ============================================================

def _run_interactive_loop(parser, initial_args, handlers, skill_names):
    """无子命令时进入：支持短命令 task/stop/status、exit、monitor。"""
    if initial_args.workspace:
        ws = Path(initial_args.workspace).resolve()
        cfg.apply_workspace(ws)

    name = _cli_name()
    prompt = f"{name}> "

    # 确保目录结构存在
    cfg.ensure_dirs()
    
    # 恢复所有已注册的agent（它们的状态已经在agents.json中）
    from secretary.agents import list_workers
    
    all_agents = list_workers()
    if all_agents:
        print(f"   📋 检测到 {len(all_agents)} 个已注册的agent")
    
    # 自动启动所有已注册但未运行的agents
    try:
        started_count = _auto_start_agents(silent=True)
        # 即使静默启动，也显示启动的agent数量（如果有）
        # 这样用户知道系统正在工作
        if started_count > 0:
            print(f"   🔄 已自动启动 {started_count} 个agent扫描器")
    except Exception as e:
        # 自动启动失败不应该影响交互模式，但记录错误以便调试
        # 在交互模式下，静默处理错误，避免影响用户体验
        # 如果需要调试，可以查看日志文件或使用非静默模式
        pass
    
    # 打印欢迎信息 + 首次状态栏
    print(f"\n🔄 {name} 交互模式 — 输入子命令，monitor 监控面板")
    print(f"   {t('interactive_welcome')}")
    try:
        from secretary.ui.dashboard import print_status_line
        print_status_line()
    except Exception:
        pass
    
    print()

    while True:
        try:
            line = input(prompt).strip()
        except KeyboardInterrupt:
            # Ctrl+C: 清空当前行，重新等待输入
            print()  # 换行，避免提示符粘在 ^C 后面
            continue
        except EOFError:
            # Ctrl+D: 退出
            print(f"👋 退出 {name}")
            _cleanup_all_processes()
            print()
            break
        if not line:
            continue
        if line.lower() == "exit":
            print(f"👋 退出 {name}")
            # 清理所有正在运行的扫描进程
            _cleanup_all_processes()
            print()
            break
        if line.lower() == "bar":
            try:
                from secretary.ui.dashboard import print_status_line
                print_status_line()
            except Exception as e:
                print(f"   ⚠️ {e}")
            continue

        try:
            parts = shlex.split(line)
        except ValueError as e:
            # 处理引号不匹配等解析错误
            if "No closing quotation" in str(e) or "quotation" in str(e).lower():
                print("   ❓ 引号不匹配，请检查输入的命令")
            else:
                print(f"   ❓ 命令解析错误: {e}")
            continue
        
        if not parts:
            continue

        # 如果第一个 token 是命令名本身（kai/secretary），自动去掉
        # 这样用户在交互模式下也可以输入 "kai skills" 而不报错
        first = parts[0]
        if first in (name, "kai", "secretary"):
            parts = parts[1:]
            if not parts:
                continue
            first = parts[0]

        # 检测是否是技能名 (不在 handlers 里的单词)
        # 如果第一个 token 是已知技能，则包装成 use <skill> 命令
        if first not in handlers and first in skill_names:
            parts = ["use", first] + parts[1:]
        
        # 检测是否是 report 命令的特殊格式: "worker report" 或 "all report"
        if len(parts) >= 2 and parts[1] == "report":
            # 将 "worker report" 转换为 "report worker"
            parts = ["report", parts[0]] + parts[2:]

        try:
            args = parser.parse_args(parts)
        except SystemExit:
            print("   ❓ 未知命令或参数错误，请重试")
            continue
        if not getattr(args, "command", None):
            print("   ❓ 请输入子命令，如 task / stop / monitor / skills")
            continue

        # base / name / model / help 不需要 ensure_dirs
        if args.command in ("base", "name", "model", "help"):
            handlers[args.command](args)
            continue

        cfg.ensure_dirs()

        # 刷新可用技能列表 (用户可能刚 learn 了新技能)
        _refresh_skill_names(skill_names)

        # 根据命令类型自动判断执行方式
        # 持续运行的命令已经在各自的 cmd_* 函数中处理后台执行
        # 这里直接调用 handler，让命令自己决定是前台还是后台执行
        try:
            handlers[args.command](args)
        except SystemExit as e:
            if e.code and e.code != 0:
                print(f"   ⚠️ 命令退出码: {e.code}")


def _refresh_skill_names(skill_names: set):
    """刷新可用技能名集合"""
    try:
        from secretary.skills import list_skills
        current = {s["name"] for s in list_skills()}
        skill_names.clear()
        skill_names.update(current)
    except Exception:
        pass


def _get_all_skill_names() -> set:
    """获取所有技能名 (内置 + 用户已学)"""
    try:
        from secretary.skills import list_skills, ensure_builtin_skills
        ensure_builtin_skills()
        return {s["name"] for s in list_skills()}
    except Exception:
        return set(cfg.BUILTIN_SKILLS.keys())


# ============================================================
#  主入口
# ============================================================

def main():
    name = _cli_name()
    _quick_start = t("help_quick_start_line").format(name=name)
    _common = t("help_common_commands")

    parser = argparse.ArgumentParser(
        prog=name,
        description=f"{name} — 基于 Agent 的自动化任务系统\n\n{_quick_start}\n{_common}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
角色:
  🗂️ 秘书    task                                → 归类任务 (自动分配给工人)
  📚 技能    learn / forget / skills / <技能名>   → 管理和使用可复用任务
  👷 工人    hire / fire / workers               → 招募/解雇/列出工人
  ♻️ 回收者   recycle                             → 审查 report/ 中的报告

完整流程:
  task → 秘书分配给工人 → <worker>/tasks/ → <worker>/ongoing/ → report/

任务:
  {name} task "你的任务描述"
  {name} task "优化性能" --time 120
  {name} task "修复bug" --worker sen         直接分配给指定 worker
  {name} keep "持续目标" --worker sen        持续监控模式，自动生成任务推进目标

工人管理:
  {name} hire                       👷 招募 worker (只注册，不启动)
  {name} hire alice                 👷 招募叫 alice 的 worker
  {name} hire <name> secretary      🤖 创建并启动 secretary agent
  {name} fire alice                 🔥 解雇 alice
  {name} workers                    📋 列出已注册的 agent

技能:
  {name} skills                     📚 列出所有技能
  {name} <技能名>                   🎯 使用技能 (直接写入 tasks/)
  {name} learn "描述" my-skill      📖 学习新技能
  {name} forget my-skill            🧹 忘掉技能

内置技能: evolving | analysis | debug

后台:
  {name} hire <name> <type>        👷 招募并启动agent (type: secretary/worker/recycler)
  {name} boss <name> "目标" <worker>  👔 创建Boss Agent监控worker
  {name} recycle                    ♻️ 启动回收者 (每2分钟审查)
  {name} monitor                    📺 实时监控面板 (TUI)

设置:
  {name} base .                     📁 设定工作区为当前目录
  {name} base /path/to/project      📁 设定工作区为指定路径
  {name} base --clear               📁 清除设定 (使用 CWD)
  {name} name lily                  🏷️  改名叫 lily
  {name} model                      🤖 查看当前模型设置
  {name} model Auto                 🤖 设置模型为 Auto
  {name} model gpt-4                🤖 设置模型为 gpt-4
  {name} target "目标描述"          🎯 创建Boss Agent (boss yks "目标" ykc)

监控与控制:
  {name} monitor                    📺 实时监控面板 (TUI)
  {name} monitor --text             📊 查看系统状态 (文本快照)
  {name} monitor -i 5               📺 监控面板，每 5 秒刷新
  {name} check <worker|kai>         📺 实时查看日志输出
  {name} clean-logs                 🧹 清理日志文件
        """,
    )

    # ---- 全局参数 ----
    parser.add_argument(
        "-w", "--workspace",
        type=str, default=None,
        help="临时指定工作区 (不保存，仅本次生效)",
    )
    parser.add_argument(
        "-l", "--language",
        type=str, default=None, choices=["en", "zh"],
        help="Output language: en | zh (or set SECRETARY_LANGUAGE). Default: zh",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    time_help = "最低执行时间(秒)，Agent 提前完成也会被要求继续完善直到达到此时间"

    # ---- task ----
    p = subparsers.add_parser(
        "task",
        help="提交任务（经秘书分配或直接指定 worker）",
        description="提交任务描述，由秘书 Agent 分配或通过 --worker 直接指定执行者。",
    )
    p.add_argument("request", nargs="+", help="任务描述")
    p.add_argument("--time", type=int, default=0, help=time_help)
    p.add_argument("--worker", type=str, default=None, help="直接分配给指定 worker，跳过秘书")
    
    # ---- boss ----
    p = subparsers.add_parser("boss", help="👔 创建并启动Boss Agent：监控指定worker，在队列为空时生成新任务")
    p.add_argument("boss_name", help="Boss名称")
    p.add_argument("goal", help="持续目标描述（固定关键字 'task'）")
    p.add_argument("worker_name", help="监控的worker名称")
    p.add_argument("max_executions", type=int, nargs="?", default=None, help="最大执行次数（可选，不指定则无限次）")
    

    # ---- use <skill> ----
    p = subparsers.add_parser("use", help="🎯 使用技能 (直接写入 tasks/)")
    p.add_argument("skill_name", help="技能名称")
    p.add_argument("--time", type=int, default=0, help=time_help)

    # ---- learn ----
    p = subparsers.add_parser("learn", help="📖 学习新技能")
    p.add_argument("description", nargs="+", help="任务描述")
    p.add_argument("skill_name", help="技能名 (如 my-skill)")

    # ---- forget ----
    p = subparsers.add_parser("forget", help="🧹 忘掉一个技能")
    p.add_argument("skill_name", help="技能名")

    # ---- skills ----
    subparsers.add_parser("skills", help="📚 列出所有已学技能")

    # ---- hire ----
    p = subparsers.add_parser(
        "hire",
        help="招募并启动 agent（secretary/worker/recycler）",
        description="招募并启动后台 agent。可指定名称与类型，不填则随机取名且类型为 worker。",
    )
    p.add_argument("worker_names", nargs="*", default=None,
                   help="名称与可选类型，如 alice worker、recycler recycler；不填则随机取名")
    p.add_argument("-d", "--description", type=str, default="", help="描述")

    # ---- fire ----
    p = subparsers.add_parser("fire", help="🔥 解雇一个或多个工人")
    p.add_argument("worker_names", nargs="+", help="要解雇的工人名，可多个 (如 alice bob)")

    # ---- workers ----
    subparsers.add_parser(
        "workers",
        help="列出已注册的 agent（名称、类型、PID、状态）",
        description="列出当前工作区内已注册的 agent，与 monitor --text 列对齐。",
    )

    # ---- recycle ----
    p = subparsers.add_parser(
        "recycle",
        help="启动回收者（审查报告）",
        description="启动回收者，定期审查 report/ 中的报告。--once 表示前台执行一次后退出。",
    )
    p.add_argument("--once", action="store_true", help="前台执行一次后退出")

    # ---- base ----
    p = subparsers.add_parser("base", help="📁 设定/查看工作区目录")
    p.add_argument("path", nargs="?", default=None,
                   help="工作区路径 (. = 当前目录, --clear = 清除)")

    # ---- name ----
    p = subparsers.add_parser("name", help="🏷️ 给我改个名字")
    p.add_argument("new_name", help="新命令名 (如 lily)")
    
    # ---- model ----
    p = subparsers.add_parser("model", help="🤖 设置或查看模型")
    p.add_argument("model_name", nargs="?", help="模型名称 (如 Auto, gpt-4, claude-3)，不指定则查看当前设置")

    # ---- monitor ----
    p = subparsers.add_parser(
        "monitor",
        help="实时监控面板（TUI 或文本快照）",
        description="启动监控面板，查看 Agent 与任务状态。--text 或 --once 为文本输出后退出。",
    )
    p.add_argument("-i", "--interval", type=float, default=2.0,
                   help="刷新间隔（秒），默认 2")
    p.add_argument("--text", action="store_true", help="输出文本状态后退出")
    p.add_argument("--once", action="store_true", help="输出一次快照后退出")

    # ---- target ----
    p = subparsers.add_parser("target", help="🎯 创建Boss Agent的别名：target \"tasks\" = boss yks \"tasks\" ykc")
    p.add_argument("goal", nargs="+", help="持续目标描述")

    # ---- report ----
    # ---- help ----
    p = subparsers.add_parser("help", help="❓ 显示帮助信息")
    p.add_argument("command_name", nargs="?", default=None,
                   help="命令名称 (可选,显示特定命令的详细帮助)")

    # ---- check / clean-logs / clean-processes ----
    p = subparsers.add_parser("check", help="📺 实时查看 agent 的日志输出")
    p.add_argument("worker_name", help="agent 名称 (如 sen、yks 等)")
    p.add_argument("--tail", type=int, default=0, help="只显示最后 N 行（默认显示所有内容）")
    subparsers.add_parser("clean-logs", help="🧹 清理 logs/ 下的日志文件")
    subparsers.add_parser("clean-processes", help="🧹 清理泄露的 worker 进程记录")

    handlers = {
        "task": cmd_task,
        "boss": cmd_boss,
        "use": cmd_use_skill,
        "learn": cmd_learn,
        "forget": cmd_forget,
        "skills": cmd_skills,
        "hire": cmd_hire,
        "fire": cmd_fire,
        "workers": cmd_workers,
        "recycle": cmd_recycle,
        "monitor": cmd_monitor,
        "check": cmd_check,
        "clean-logs": cmd_clean_logs,
        "clean-processes": cmd_clean_processes,
        "base": cmd_base,
        "name": cmd_name,
        "model": cmd_model,
        "target": cmd_target,
        "help": cmd_help,
    }

    args = parser.parse_args()

    # 全局 language：本次运行优先使用 CLI --language，否则沿用环境变量/配置
    if getattr(args, "language", None) is not None:
        os.environ["SECRETARY_LANGUAGE"] = args.language

    # 无子命令时进入交互模式
    if not args.command:
        skill_names = _get_all_skill_names()
        _run_interactive_loop(parser, args, handlers, skill_names)
        return

    # --workspace 临时覆盖 (不保存)
    if args.workspace:
        ws = Path(args.workspace).resolve()
        cfg.apply_workspace(ws)

    # base / name / model / help 命令不需要 ensure_dirs
    if args.command in ("base", "name", "model", "help"):
        handlers[args.command](args)
        return

    # 其他命令: 确保运行时目录存在
    cfg.ensure_dirs()

    # 如果命令是已知技能名 (非子命令)，转发到 use
    if args.command == "use":
        handlers["use"](args)
        return

    handlers[args.command](args)


if __name__ == "__main__":
    import atexit
    
    # 注册退出时的清理函数（作为兜底，防止异常退出时进程泄漏）
    def _atexit_cleanup():
        """程序退出时的清理函数（兜底）"""
        try:
            from secretary.agents import list_workers
            workers = list_workers()
            for worker in workers:
                pid = worker.get("pid")
                if pid and _check_process_exists(pid):
                    # 静默停止，避免在退出时输出过多信息
                    _stop_process(pid, worker.get("name", "unknown"), verbose=False)
        except Exception:
            pass  # 退出时忽略错误
    
    atexit.register(_atexit_cleanup)
    
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl+C 退出时也清理进程
        print("\n👋 退出")
        _cleanup_all_processes()
        sys.exit(0)

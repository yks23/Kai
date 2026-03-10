#!/usr/bin/env python3
"""
machine — CLI 入口（基于 Cursor Agent 的自动化任务系统）

用法:
  machine task "实现一个HTTP服务器"
  machine evolving / analysis / debug        (内置技能)
  machine learn "任务描述" skill-name         (学技能)
  machine <skill-name>                       (使用技能)
  machine forget <skill-name>                (忘技能)
  machine skills                             (列出所有技能)
  machine hire / recycle                     (后台服务)
  machine monitor / stop / clean-logs
  machine base ./          设定工作区为当前目录
  machine name lily        给我改个名字叫 lily
  machine target "目标描述"  创建Boss Agent (boss yks "目标" ykc)
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
    get_cli_name, set_cli_name, get_base_dir, get_model, set_model,
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
        # 显式传入 WORKSPACE，防止子进程用 cwd（BASE_DIR）推导出错误的路径
        env["SECRETARY_WORKSPACE"] = str(cfg.WORKSPACE)
        
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
                    print("   未找到任何已注册的 agent 类型")
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
        
        # 确保 worker 存在
        worker = get_worker(worker_name)
        worker_created = False
        if not worker:
            print(f"ℹ️  Worker '{worker_name}' 不存在，自动创建...")
            register_worker(worker_name, description="由任务分配创建")
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

    # 否则，写入秘书的 tasks 目录（由秘书扫描器自动处理）
    from secretary.agents import list_workers

    secretaries = [w for w in list_workers() if w.get("type") == "secretary"]
    if not secretaries:
        print(t("error_no_secretary").format(name=_cli_name()))
        return

    secretary_name = secretaries[0]["name"]
    task_file = _write_kai_task(request, min_time=min_time, secretary_name=secretary_name)
    print(f"\n📨 任务已提交到 {secretary_name}")
    print(f"   ✅ 任务文件: {task_file}")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    print(f"   💡 使用 `{_cli_name()} check {secretary_name}` 查看处理日志")


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
            secretary_name = secretaries[0]["name"]
        else:
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


def _create_boss(
    boss_name: str,
    goal: str,
    worker_name: str,
    max_executions: int | None = None,
    start: bool = True,
) -> bool:
    """
    创建 Boss Agent 的共享逻辑。

    验证名称可用性 → 确保 worker 存在 → 创建 boss 目录和配置 → 注册 boss。
    当 start=True 时同时启动 worker 和 boss 的扫描器；
    当 start=False 时只注册和创建目录结构，不启动任何扫描器。
    返回 True 表示成功，False 表示失败（已打印错误信息）。
    """
    import secretary.config as cfg
    from secretary.agents import register_agent, get_worker

    # 检查boss_name是否已被使用（且不是boss类型）
    existing_boss = get_worker(boss_name)
    if existing_boss and existing_boss.get("type") != "boss":
        print(f"⚠️  名字 '{boss_name}' 已被注册为 {existing_boss.get('type')} 类型，不能用作boss")
        print("   请使用其他名字或先解雇该agent")
        return False

    # 确保worker_name可用且不是非worker类型
    existing_worker = get_worker(worker_name)
    if existing_worker and existing_worker.get("type") != "worker":
        print(f"⚠️  名字 '{worker_name}' 已被注册为 {existing_worker.get('type')} 类型，不能用作worker")
        print("   请使用其他名字或先解雇该agent")
        return False

    # 确保 worker 存在
    if not existing_worker:
        register_agent(worker_name, agent_type="worker", description=f"由Boss {boss_name}监控的Worker")
        print(f"✅ 已创建worker: {worker_name}")
        if start:
            _start_agent_scanner(worker_name, "worker", silent=False)

    # 创建boss目录和配置
    boss_dir = cfg.AGENTS_DIR / boss_name
    for sub in ("tasks", "reports", "logs", "stats"):
        (boss_dir / sub).mkdir(parents=True, exist_ok=True)

    # 写入目标文件
    goal_file = boss_dir / "goal.md"
    if not goal_file.exists() or goal != "推进项目目标":
        goal_file.write_text(f"# 持续目标\n\n{goal}\n", encoding="utf-8")

    # 写入配置文件
    config_content = (
        f"# Boss配置\n\n"
        f"监控的Worker: {worker_name}\n"
        f"持续目标: {goal[:100]}...\n"
    )
    if max_executions is not None:
        config_content += f"最大执行次数: {max_executions}\n"
    (boss_dir / "config.md").write_text(config_content, encoding="utf-8")

    # 注册boss agent
    if not existing_boss:
        register_agent(boss_name, agent_type="boss", description=f"Boss: {goal[:50]}")

    if start:
        _start_agent_scanner(boss_name, "boss", silent=False)
        print(f"✅ Boss '{boss_name}' 已创建并启动")
    else:
        print(f"✅ Boss '{boss_name}' 已创建（未启动扫描器）")

    print(f"   持续目标: {goal}")
    print(f"   监控Worker: {worker_name}")
    if not start:
        name = _cli_name()
        print(f"   💡 启动: {name} hire {boss_name}")
    return True


def cmd_boss(args):
    """便捷命令：等价于 hire <name> boss <worker> -d <goal> [--no-start]"""
    import secretary.config as cfg

    boss_name = args.boss_name
    worker_name = args.worker_name or cfg.DEFAULT_WORKER_NAME
    should_start = not getattr(args, "no_start", False)

    goal_keyword = args.goal
    if goal_keyword == "task":
        goal_file = cfg.AGENTS_DIR / boss_name / "goal.md"
        if goal_file.exists():
            content = goal_file.read_text(encoding="utf-8").strip()
            lines = [line.strip() for line in content.splitlines()
                     if line.strip() and not line.strip().startswith("#")]
            goal = "\n".join(lines) if lines else content
        else:
            goal = "推进项目目标"
    else:
        goal = goal_keyword

    _create_boss(boss_name, goal, worker_name,
                 max_executions=args.max_executions, start=should_start)




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
        print("\n📚 还没有学会任何技能")
        print(f"   用 `{name} learn \"任务描述\" skill-name` 来教我！")
        return

    print(f"\n📚 已学技能 ({len(skills)} 个):\n")
    for s in skills:
        tag = "📦" if s["builtin"] else "🎓"
        desc = s["description"] or "(无描述)"
        print(f"   {tag} {s['name']:20s}  {desc}")

    print("\n   📦 = 内置技能   🎓 = 已学技能")
    print(f"   使用: {name} <技能名>")
    print(f"   学习: {name} learn \"描述\" <名字>")
    print(f"   忘记: {name} forget <名字>")


# ============================================================
#  后台服务
# ============================================================

def cmd_hire(args):
    """
    统一的 agent 招募入口。

    语法:
      hire <name> <type> [dep_agent_name ...] [-d desc] [--no-start]

    type: worker / secretary / recycler / boss
    dep_agent_name: 依赖的 agent 名称，类型由主 agent 的逻辑决定。
      例如 boss 依赖 worker，故 `hire myboss boss sen` 中 sen 自动按 worker 创建。

    若 agent 已存在且未运行，再次 hire 会启动其扫描器。
    """
    from secretary.agents import pick_random_name, register_agent, get_worker
    import secretary.config as cfg

    names = list(getattr(args, "worker_names", None) or [])
    no_start = getattr(args, "no_start", False)

    valid_types = ("secretary", "worker", "recycler", "boss")

    agent_type = "worker"
    agent_name = None
    dep_names: list[str] = []

    for arg in names:
        if arg.lower() in valid_types and agent_type == "worker":
            agent_type = arg.lower()
        elif agent_name is None:
            agent_name = arg
        else:
            dep_names.append(arg)

    if agent_name is None:
        agent_name = pick_random_name()

    description = getattr(args, "description", None) or ""

    # ---- 已存在的 agent：空闲则启动，运行中则提示 ----
    existing = get_worker(agent_name)
    if existing:
        existing_type = existing.get("type", "worker")
        pid = existing.get("pid")
        if pid and _check_process_exists(pid):
            print(f"ℹ️  Agent '{agent_name}' 已在运行 (PID={pid})")
        else:
            print(t("msg_starting_agent").format(agent_name=agent_name, agent_type=existing_type))
            _start_agent_scanner(agent_name, existing_type, silent=False)
        return

    # ---- 新建 agent ----
    if agent_type == "boss":
        monitored_worker = dep_names[0] if dep_names else cfg.DEFAULT_WORKER_NAME
        goal = description or "推进项目目标"
        _create_boss(agent_name, goal, monitored_worker, start=not no_start)
    else:
        register_agent(agent_name, agent_type=agent_type, description=description)
        print(f"✅ 已注册 {agent_type} agent: {agent_name}")
        if not no_start:
            print(t("msg_starting_agent").format(agent_name=agent_name, agent_type=agent_type))
            _start_agent_scanner(agent_name, agent_type, silent=False)
        else:
            print(f"   💡 启动: {_cli_name()} hire {agent_name}")




def cmd_workers(args):
    """列出当前工作区内已注册的 agent"""
    if not _is_workspace_configured(args):
        print(t("workspace_not_set_hint").format(name=_cli_name()))
        return
    from secretary.agents import list_workers

    workers = list_workers()
    name = _cli_name()

    _sync_processes_to_queue()
    active_procs = _get_active_processes()
    proc_pid_map = {p.get("name"): p.get("pid") for p in active_procs}

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        table = Table(
            title=f"Agent 列表 — {cfg.BASE_DIR}",
            box=box.ROUNDED,
            show_lines=False,
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
        status_map = {"idle": ("💤", "dim"), "busy": ("运行", "green"), "offline": ("离线", "red")}

        if not workers:
            console.print(f"\n  (无 agent，使用 {name} hire 招募)\n")
            return

        for w in workers:
            agent_name = w.get("name", "?")
            agent_type = w.get("type", "?")
            pending = w.get("pending_count", 0)
            ongoing = w.get("ongoing_count", 0)
            completed = w.get("completed_tasks", 0)
            pid = proc_pid_map.get(agent_name) or w.get("pid")
            icon = type_icons.get(agent_type, "❓")

            status_text, status_style = status_map.get(w.get("status", ""), ("❓", ""))
            if pid and _check_process_exists(pid):
                status_text, status_style = "运行", "green"

            table.add_row(
                agent_name,
                f"{icon} {agent_type}",
                str(pending) if pending else "-",
                str(ongoing) if ongoing else "-",
                str(completed),
                f"[{status_style}]{status_text}[/]",
                str(pid) if pid else "-",
            )

        console.print()
        console.print(table)
        console.print(f"  [dim]日志: {name} check <名>  |  监控: {name} monitor  |  解雇: {name} fire <名>[/]\n")
    except ImportError:
        # rich 不可用时退化为纯文本
        print(f"\n📋 {name} Agent — {cfg.BASE_DIR}\n")
        for w in workers:
            print(f"  {w.get('name', '?'):16s} {w.get('type', '?'):10s} 完成={w.get('completed_tasks', 0)}")
        print()


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
            print("   将强制停止进程并解雇")

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
            print("   已停止进程、删除目录及注册信息")
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
        print("\n♻️ 回收者（单次执行）\n")
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


def cmd_dashboard(args):
    """启动 Web 仪表板，在浏览器中打开。"""
    if not _is_workspace_configured(args):
        print(f"💡 工作区未设置，使用当前目录")
    from secretary.ui.web_dashboard import run_dashboard
    port = getattr(args, "port", 12345)
    open_browser = not getattr(args, "no_browser", False)
    run_dashboard(port=port, open_browser=open_browser)


def cmd_monitor(args):
    """实时监控面板（TUI，q 退出）。--text/--once 输出文本快照。"""
    if not _is_workspace_configured(args):
        print(t("workspace_not_set_hint").format(name=_cli_name()))
    from secretary.ui.dashboard import run_monitor

    text_mode = getattr(args, "text", False)
    once = getattr(args, "once", False)

    if text_mode or once:
        run_monitor(refresh_interval=args.interval, text_mode=True, once=True)
        return

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
        print("   📁 Agent配置和文件夹已保留，下次启动时会自动恢复")
    else:
        print("   ℹ️  没有需要停止的进程")


def _render_dialog_colored(content: str, _console=None):
    """
    解析 dialog.md 并以带颜色的对话格式打印到终端（使用 rich）。

    格式约定（agent_runner.py 写入的）：
      ============================================================  <- 任务分隔头
      ## task-name
      时间: ...
      ============================================================

      ------------------------------------------------------------  <- 消息块分隔
      [HH:MM:SS] 提示词 (首轮/续轮)
      ------------------------------------------------------------
      <prompt 内容>

      ------------------------------------------------------------
      [HH:MM:SS] 首轮 / 续轮 / 用户
      ------------------------------------------------------------
      <agent 回复内容>

    _console: 如果传入 rich Console 对象（如 pager console），则直接使用它；
              否则自动创建一个 UTF-8 stdout 包装的 Console。
    """
    import re
    import sys
    import io
    _utf8_stdout = None
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        from rich.rule import Rule
        if _console is not None:
            _con = _console
        else:
            # Force UTF-8 output so emoji in dialog content render correctly on Windows
            # NOTE: we must call _utf8_stdout.detach() before returning so the wrapper
            # doesn't close sys.stdout.buffer when it goes out of scope.
            _utf8_stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            _con = Console(highlight=False, file=_utf8_stdout, force_terminal=True)
    except ImportError:
        # fallback: plain text
        _con = None

    def _rprint(s, style=""):
        if _con:
            _con.print(s, style=style)
        else:
            print(s)

    def _rule(title="", style="dim"):
        if _con:
            _con.print(Rule(title, style=style))
        else:
            print(f"── {title} " + "─" * max(0, 56 - len(title)))

    DASH60 = "\u2500" * 60  # ─
    EQ60   = "=" * 60

    lines = content.splitlines()
    i = 0
    total = len(lines)

    def _print_task_header(block_lines):
        title = ""
        ts = ""
        for ln in block_lines:
            ln = ln.strip()
            if ln.startswith("## "):
                title = ln[3:].strip()
            elif ln.startswith("\u65f6\u95f4:"):  # 时间:
                ts = ln[3:].strip()
        label = f"  Task: {title}" if title else "  Task"
        ts_suffix = f"  {ts}" if ts else ""
        if _con:
            _con.print()
            _con.rule(f"[bold yellow]{label}[/bold yellow][dim]{ts_suffix}[/dim]",
                      style="yellow dim")
        else:
            print(f"\n{'=' * 60}")
            print(f"{label}{ts_suffix}")
            print("=" * 60)

    def _print_message(header: str, body_lines: list):
        m = re.search(r"\[(\d{2}:\d{2}:\d{2})\]", header)
        ts = m.group(1) if m else ""

        is_prompt = "\u63d0\u793a\u8bcd" in header   # 提示词
        is_user   = "\u7528\u6237" in header           # 用户
        is_first  = "\u9996\u8f6e" in header           # 首轮
        is_resume = "\u7eed\u8f6e" in header           # 续轮

        body = "\n".join(body_lines).strip()
        MAX_BODY = 3000
        truncated = len(body) > MAX_BODY
        display_body = body[:MAX_BODY] if truncated else body

        if is_prompt:
            if is_first:
                title_str = f"[bold cyan]>> 发送 [首轮提示词][/bold cyan]  [dim]{ts}[/dim]"
                border_style = "cyan"
            else:
                title_str = f"[bold magenta]>> 发送 [续轮提示词][/bold magenta]  [dim]{ts}[/dim]"
                border_style = "magenta"
            body_style = "dim"
        elif is_user:
            title_str = f"[bold cyan]>> 用户[/bold cyan]  [dim]{ts}[/dim]"
            border_style = "cyan"
            body_style = ""
        elif is_first or is_resume:
            rnd = "首轮" if is_first else f"续轮"
            title_str = f"[bold green]<< Agent [{rnd}][/bold green]  [dim]{ts}[/dim]"
            border_style = "green"
            body_style = ""
        else:
            title_str = f"[bold white]{header.strip()}[/bold white]  [dim]{ts}[/dim]"
            border_style = "white"
            body_style = ""

        if _con:
            _con.print()
            if display_body:
                _con.print(Panel(
                    Text(display_body, style=body_style, no_wrap=False),
                    title=title_str,
                    border_style=border_style,
                    padding=(0, 1),
                ))
                if truncated:
                    _con.print(f"[dim]  ... (省略 {len(body) - MAX_BODY} 字符)[/dim]")
            else:
                _con.print(f"[{border_style}]{title_str}[/{border_style}]  [dim](empty)[/dim]")
        else:
            # plain fallback
            print(f"\n--- {header.strip()} {ts} ---")
            print(display_body)
            if truncated:
                print(f"  ... (省略 {len(body) - MAX_BODY} 字符)")

    # ── 解析循环 ──
    current_msg_header = ""
    current_msg_body: list = []
    in_msg_body = False

    def _flush_msg():
        nonlocal current_msg_header, current_msg_body, in_msg_body
        if current_msg_header:
            _print_message(current_msg_header, current_msg_body)
        current_msg_header = ""
        current_msg_body = []
        in_msg_body = False

    while i < total:
        line = lines[i]
        stripped = line.strip()

        # 任务分隔头 ====
        if stripped == EQ60:
            _flush_msg()
            j = i + 1
            task_block = []
            while j < total and lines[j].strip() != EQ60:
                task_block.append(lines[j])
                j += 1
            if j < total:
                _print_task_header(task_block)
                i = j + 1
            else:
                i = j
            continue

        # 消息分隔线 ────  (三连格式: ─...─ / header / ─...─)
        if stripped == DASH60:
            if i + 2 < total:
                header_line = lines[i + 1].strip()
                next2 = lines[i + 2].strip()
                if next2 == DASH60:
                    _flush_msg()
                    current_msg_header = header_line
                    current_msg_body = []
                    in_msg_body = True
                    i += 3
                    continue
            i += 1
            continue

        if in_msg_body:
            current_msg_body.append(line)

        i += 1

    _flush_msg()

    # Release the buffer without closing it so sys.stdout stays usable
    # (e.g. for the interactive loop's input() prompt after this returns).
    if _utf8_stdout is not None:
        try:
            _utf8_stdout.detach()
        except Exception:
            pass


def _tail_file_colored(path, render_fn, stop_event, interval=0.3):
    """实时跟踪文件新增内容，用 render_fn 渲染每行"""
    import time
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, 2)  # 跳到末尾
            buf = ""
            while not stop_event.is_set():
                chunk = f.read(4096)
                if chunk:
                    buf += chunk
                    # 按换行刷新
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        render_fn(line)
                else:
                    time.sleep(interval)
    except Exception:
        pass


def cmd_check(args):
    """查看 agent 日志（默认：系统日志；--chat：彩色对话记录）"""
    from secretary.agents import get_worker, _worker_logs_dir
    import threading
    import time
    import sys

    worker_name = getattr(args, "worker_name", None)
    if not worker_name:
        print(f"用法: {_cli_name()} check <agent_name>  [--chat] [-f]")
        return

    worker = get_worker(worker_name)
    if not worker:
        print(f"❌ Agent '{worker_name}' 不存在")
        return

    agent_type = worker.get("type", "agent")
    pid = worker.get("pid")
    is_running = pid and _check_process_exists(pid)

    type_icons = {"secretary": "🤖", "worker": "👷", "boss": "👔", "recycler": "♻️"}
    icon = type_icons.get(agent_type, "❓")
    status_str = f"运行中 PID={pid}" if is_running else "未运行"

    log_dir = _worker_logs_dir(worker_name)
    follow = getattr(args, "follow", False)
    chat_mode = getattr(args, "chat", False)

    # ──────────────────────────────────────────────────
    # --chat 模式：解析 dialog.md，彩色对话渲染
    # ──────────────────────────────────────────────────
    if chat_mode:
        dialog_file = log_dir.parent / "dialog" / "dialog.md"
        if not dialog_file.exists():
            print(f"❌ 暂无对话记录: {dialog_file}")
            print("   💡 对话记录在以下情况下自动生成：")
            print("      · agent 处理任务时（scanner 运行中）")
            print(f"      · 使用 {_cli_name()} chat {worker_name} 直接对话时")
            return

        size = dialog_file.stat().st_size
        size_str = f"{size / 1024:.1f}KB" if size > 1024 else f"{size}B"

        try:
            content = dialog_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"❌ 读取对话文件失败: {e}")
            return

        if not follow:
            # 非跟踪模式：直接渲染到终端（终端缓冲区本身可滚动），底部等待 Enter 返回
            # 不使用系统 pager（Windows more 会在 rich 面板中间插入 -- More -- 破坏布局）
            try:
                import io
                from rich.console import Console
                from rich.rule import Rule
                _utf8 = io.TextIOWrapper(
                    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
                )
                _con = Console(highlight=False, file=_utf8, force_terminal=True)
                _con.print()
                _con.rule(
                    f"[bold cyan]{icon} {worker_name}[/bold cyan]"
                    f"[dim]  {agent_type}  {status_str}  {size_str}[/dim]",
                    style="cyan dim",
                )
                _render_dialog_colored(content, _console=_con)
                _con.rule("[dim]↑ 向上滚动可查看历史  │  按 Enter 返回[/dim]", style="dim")
                _utf8.flush()
                _utf8.detach()  # 释放 buffer，不关闭 sys.stdout
            except Exception:
                print(f"\n\033[1;36m{icon} {worker_name}\033[0m  \033[2m{agent_type}  {status_str}  {size_str}\033[0m")
                _render_dialog_colored(content)
            # 阻塞，等用户按 Enter（Ctrl+C 也可退出）
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass
            print("\033[0m", end="", flush=True)  # 重置颜色
            return

        # -f 跟踪模式：先渲染已有内容，再实时追加
        print(f"\n\033[1;36m{icon} {worker_name}\033[0m  \033[2m{agent_type}  {status_str}  {size_str}\033[0m")
        print("\033[2m实时跟踪中…  Ctrl+C 退出\033[0m")
        _render_dialog_colored(content)

        # 继续实时追加渲染新写入的行
        stop_event = threading.Event()
        buf_lines: list[str] = []
        buf_lock = threading.Lock()

        def _follow_dialog():
            try:
                with open(dialog_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, 2)
                    while not stop_event.is_set():
                        chunk = f.read(4096)
                        if chunk:
                            with buf_lock:
                                buf_lines.append(chunk)
                        else:
                            time.sleep(0.3)
            except Exception:
                pass

        t = threading.Thread(target=_follow_dialog, daemon=True)
        t.start()

        # 定期把新增内容渲染出来
        accumulated = ""
        try:
            while True:
                time.sleep(0.5)
                with buf_lock:
                    if buf_lines:
                        accumulated += "".join(buf_lines)
                        buf_lines.clear()
                if accumulated:
                    _render_dialog_colored(accumulated)
                    accumulated = ""
        except KeyboardInterrupt:
            stop_event.set()
            print("\n\033[0m👋 退出对话查看")
        return

    # ──────────────────────────────────────────────────
    # 默认模式：scanner.log（纯文本系统日志）
    # ──────────────────────────────────────────────────
    log_file = log_dir / "scanner.log"
    if not log_file.exists():
        print(f"❌ 没有日志文件: {log_file}")
        if not is_running:
            print(f"   💡 先启动: {_cli_name()} hire {worker_name}")
        return

    log_size = log_file.stat().st_size
    size_str = f"{log_size / 1024:.1f}KB" if log_size > 1024 else f"{log_size}B"

    print(f"\n\033[1;36m{icon} {worker_name}\033[0m  \033[2m{agent_type}  {status_str}  {size_str}\033[0m")
    print(f"\033[2m{'─' * 64}\033[0m")

    # 打印全部日志
    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        print(content, end="")
    except Exception:
        pass

    stop_event = threading.Event()
    last_pos = [log_file.stat().st_size]

    def _tail_log():
        try:
            while not stop_event.is_set():
                try:
                    cur = log_file.stat().st_size
                    if cur > last_pos[0]:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(last_pos[0])
                            chunk = f.read()
                            if chunk:
                                print(chunk, end="", flush=True)
                            last_pos[0] = cur
                except Exception:
                    pass
                time.sleep(0.4)
        except Exception:
            pass

    tail_t = threading.Thread(target=_tail_log, daemon=True)
    tail_t.start()

    print(f"\n\033[2m{'─' * 60}\033[0m")
    print("\033[2m实时跟踪中… Ctrl+C 退出\033[0m\n")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop_event.set()
        print(f"\n\033[0m\033[2m{'─' * 60}\033[0m")
        print("\033[0m👋 退出日志查看")


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

    workers = list_workers()
    cleaned = 0

    print("\n🔍 检查 worker 进程状态...")

    for worker in workers:
        worker_name = worker.get("name")
        pid = worker.get("pid")
        if not pid:
            continue

        if not _check_process_exists(pid):
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
#  upgrade 命令 + 更新检查
# ============================================================

def _find_repo_root() -> Path | None:
    """查找 Kai 源码的 git 仓库根目录（editable install 时是源码目录）"""
    pkg_dir = Path(__file__).resolve().parent
    candidate = pkg_dir.parent
    if (candidate / ".git").is_dir() and (candidate / "pyproject.toml").is_file():
        return candidate
    return None


def _get_update_check_file() -> Path:
    """更新检查状态文件路径"""
    from secretary.settings import _config_dir
    return _config_dir() / "update_check.json"


def _check_for_updates(silent: bool = True) -> str | None:
    """
    检查远端主分支是否有新提交（每天最多检查一次）。
    返回提示文本，如果无更新或检查跳过则返回 None。
    """
    import json
    import time

    check_file = _get_update_check_file()
    now = time.time()

    # 读取上次检查时间
    last_check = 0
    try:
        if check_file.exists():
            data = json.loads(check_file.read_text(encoding="utf-8"))
            last_check = data.get("last_check", 0)
    except Exception:
        pass

    # 每 24 小时检查一次
    if now - last_check < 86400:
        return None

    repo = _find_repo_root()
    if not repo:
        return None

    try:
        result = subprocess.run(
            ["git", "fetch", "--dry-run"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo),
        )
        has_updates = bool(result.stderr.strip())

        # 保存检查时间
        check_file.parent.mkdir(parents=True, exist_ok=True)
        check_file.write_text(
            json.dumps({"last_check": now, "has_updates": has_updates}),
            encoding="utf-8",
        )

        if has_updates:
            return f"💡 有新版本可用，运行 `{_cli_name()} upgrade` 更新"
    except Exception:
        pass
    return None


def cmd_upgrade(args):
    """从远端 git 拉取最新代码并重新安装"""
    repo = _find_repo_root()
    if not repo:
        print("❌ 未找到 Kai 源码仓库（仅支持 editable install 方式）")
        print("   如果通过 pip install kai 安装，请用: pip install -U kai")
        return

    name = _cli_name()
    print(f"\n🔄 {name} upgrade — {repo}\n")

    # 1. git fetch
    print("   ⏳ 获取远端更新...")
    fetch = subprocess.run(
        ["git", "fetch", "--all", "--prune"],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo),
    )
    if fetch.returncode != 0:
        print(f"   ❌ git fetch 失败: {fetch.stderr.strip()}")
        return

    # 2. 检查当前分支和远端差异
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=5,
        cwd=str(repo),
    )
    branch = branch_result.stdout.strip() or "main"

    # 检查本地有无未提交更改
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, timeout=5,
        cwd=str(repo),
    )
    has_changes = bool(status.stdout.strip())

    # 检查远端是否有新提交
    log_result = subprocess.run(
        ["git", "log", f"HEAD..origin/{branch}", "--oneline"],
        capture_output=True, text=True, timeout=5,
        cwd=str(repo),
    )
    new_commits = log_result.stdout.strip()

    if not new_commits:
        print(f"   ✅ 已是最新版本 (分支: {branch})")
        return

    print(f"   📋 远端有新提交 ({branch}):")
    for line in new_commits.splitlines()[:10]:
        print(f"      {line}")
    if new_commits.count("\n") >= 10:
        print(f"      ... 共 {new_commits.count(chr(10)) + 1} 个提交")

    # 3. git pull (如果有本地更改则 stash)
    stashed = False
    if has_changes:
        print("   ⚠️  检测到本地未提交更改，暂存中...")
        subprocess.run(
            ["git", "stash", "push", "-m", "kai-upgrade-auto-stash"],
            capture_output=True, timeout=10, cwd=str(repo),
        )
        stashed = True

    print(f"   ⏳ 拉取 origin/{branch}...")
    pull = subprocess.run(
        ["git", "pull", "origin", branch, "--ff-only"],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo),
    )
    if pull.returncode != 0:
        print(f"   ❌ git pull 失败: {pull.stderr.strip()}")
        if stashed:
            subprocess.run(["git", "stash", "pop"], capture_output=True, timeout=10, cwd=str(repo))
        return

    # 4. 恢复 stash
    if stashed:
        print("   ⏳ 恢复本地更改...")
        pop = subprocess.run(
            ["git", "stash", "pop"],
            capture_output=True, text=True, timeout=10, cwd=str(repo),
        )
        if pop.returncode != 0:
            print("   ⚠️  stash 恢复冲突，请手动处理: git stash pop")

    # 5. pip install -e .
    print("   ⏳ 重新安装...")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo), "-q"],
        capture_output=True, text=True, timeout=60,
    )
    if install.returncode != 0:
        print(f"   ❌ pip install 失败: {install.stderr.strip()[:200]}")
        return

    # 6. 更新检查状态
    import json
    import time
    check_file = _get_update_check_file()
    check_file.parent.mkdir(parents=True, exist_ok=True)
    check_file.write_text(
        json.dumps({"last_check": time.time(), "has_updates": False}),
        encoding="utf-8",
    )

    print(f"\n   ✅ 更新完成！重启 {name} 以使用新版本。")


# ============================================================
#  chat 命令 — 向 agent 的 session 直接输入消息
# ============================================================

def cmd_chat(args):
    """向指定 agent 的 session 直接发送消息（不走预设对话系统）"""
    agent_name = getattr(args, "agent_name", None)
    if not agent_name:
        print(f"用法: {_cli_name()} chat <agent_name>")
        return

    from secretary.agents import load_agent_session_id, save_agent_session_id, get_worker

    worker = get_worker(agent_name)
    if not worker:
        print(f"❌ Agent '{agent_name}' 不存在")
        print(f"   提示：使用 {_cli_name()} hire {agent_name} 先招募此 agent")
        return

    session_id = load_agent_session_id(agent_name)

    agent_type = worker.get("type", "worker")
    type_icons = {"secretary": "🤖", "worker": "👷", "boss": "👔", "recycler": "♻️"}
    icon = type_icons.get(agent_type, "❓")

    # 确定 dialog 文件路径（与 check --chat 读取同一文件）
    dialog_dir = cfg.AGENTS_DIR / agent_name / "dialog"
    dialog_dir.mkdir(parents=True, exist_ok=True)
    dialog_file = dialog_dir / "dialog.md"

    if not session_id:
        print(f"\n{icon} {agent_name} ({agent_type}) — 无历史 session，将以新会话开始")
    else:
        print(f"\n{icon} 连接到 {agent_name} ({agent_type}) session: {session_id[:16]}…")
    print(f"   工作区: {cfg.WORKSPACE}")
    print(f"   对话记录: {dialog_file}")
    print("   输入消息直接发送，Ctrl+C 或空行退出\n")

    from secretary.agent_runner import run_agent
    from datetime import datetime as _dt

    while True:
        try:
            user_input = input(f"[{agent_name}] > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 退出对话")
            break

        if not user_input or user_input.lower() in ("q", "quit", "exit", "bye"):
            print("👋 退出对话")
            break

        # 先将用户消息写入 dialog 文件
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(dialog_file, "a", encoding="utf-8") as _f:
            _f.write(f"\n{'─'*60}\n[{ts}] 用户\n{'─'*60}\n")
            _f.write(user_input + "\n")

        result = run_agent(
            prompt=user_input,
            workspace=str(cfg.get_workspace()),
            model=get_model(),
            verbose=True,
            session_id=session_id,
            dialog_file=dialog_file,  # agent 回复也追加到同一文件
        )

        # 更新 session_id（以防续轮后 id 变化）
        if result.stats.session_id:
            session_id = result.stats.session_id
            save_agent_session_id(agent_name, session_id)

        if not result.success:
            print(f"❌ 调用失败: {result.output[:300]}")

        print()  # 空行分隔


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
        print("\n   用法:")
        print(f"     {name} base .           设为当前目录")
        print(f"     {name} base /path/to    设为指定路径")
        print(f"     {name} base --clear     清除设定 (回到使用 CWD)")
        print("\n   注意: base 命令仅在当前交互会话中生效，退出后恢复默认。")
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
    print("   📂 已创建目录结构 (tasks/, ongoing/, reports/, logs/, skills/ ...)")
    print("\n   注意: 此设定仅在当前交互会话中生效，退出后恢复默认。")


# ============================================================
#  name 命令 — 改名
# ============================================================

def cmd_name(args):
    """给 CLI 命令改名"""
    new_name = args.new_name
    old_name = _cli_name()

    if not new_name.isidentifier() and not new_name.replace("-", "").isalnum():
        print(f"❌ 无效的命令名: {new_name}")
        print("   命令名只能包含字母、数字和连字符")
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
    from secretary.settings import get_model
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
        print("\n   用法:")
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


def cmd_learn_stream(args):
    """网络学堂输入流：同步作业与课件到指定目录。"""
    from secretary.input_streams import LearnStreamError, run_learn_stream

    cookie = (getattr(args, "cookie", None) or "").strip()
    cookie_file = (getattr(args, "cookie_file", None) or "").strip()
    if not cookie and cookie_file:
        try:
            cookie = Path(cookie_file).expanduser().read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"❌ 读取 cookie 文件失败: {e}")
            return
    if not cookie:
        cookie = os.environ.get("LEARN_COOKIE", "").strip()

    csrf_token = (getattr(args, "csrf", None) or "").strip()
    if not csrf_token:
        csrf_token = os.environ.get("LEARN_CSRF", "").strip()

    if not cookie:
        print("❌ 缺少登录 Cookie")
        print("   传参: --cookie 'name=value; ...' 或 --cookie-file /path/to/cookie.txt")
        print("   或环境变量: LEARN_COOKIE")
        return
    if not csrf_token:
        print("❌ 缺少 CSRF Token")
        print("   传参: --csrf <token> 或环境变量: LEARN_CSRF")
        return

    mode = getattr(args, "only", "all")
    include_files = mode in ("all", "files")
    include_homework = mode in ("all", "homework")

    try:
        result = run_learn_stream(
            cookie=cookie,
            csrf_token=csrf_token,
            output_dir=args.output,
            semester_id=args.semester,
            include_files=include_files,
            include_homework=include_homework,
            download_homework_attachments=getattr(args, "homework_attachments", True),
            dry_run=getattr(args, "dry_run", False),
            lang=args.lang,
            base_url=args.base_url,
            timeout=args.timeout,
        )
    except LearnStreamError as e:
        print(f"❌ 输入流执行失败: {e}")
        return
    except Exception as e:
        print(f"❌ 输入流异常: {e}")
        return

    stats = result.get("stats", {})
    print("\n✅ 网络学堂输入流执行完成")
    print(f"   输出目录: {Path(args.output).expanduser().resolve()}")
    print(f"   学期: {result.get('semester_id')}")
    print(f"   课程: {stats.get('courses_processed', 0)}")
    print(f"   课件: {stats.get('courseware_downloaded', 0)}/{stats.get('courseware_items', 0)}")
    print(
        "   作业附件: "
        f"{stats.get('homework_attachments_downloaded', 0)}/{stats.get('homework_attachments', 0)}"
    )
    print(f"   错误: {stats.get('errors', 0)}")
    print("   清单: learn_stream_manifest.json")


def cmd_learn_stream_setup(args):
    """交互式配置网络学堂输入流。"""
    from secretary.input_streams.scheduler import (
        DEFAULT_INTERVAL_MINUTES,
        get_default_config_path,
        load_stream_config,
        save_stream_config,
        start_scheduler,
    )

    def _ask(prompt: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default not in (None, "") else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        return default or ""

    def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
        mark = "Y/n" if default_yes else "y/N"
        value = input(f"{prompt} ({mark}): ").strip().lower()
        if not value:
            return default_yes
        return value in ("y", "yes", "1", "true")

    cfg_path = get_default_config_path()
    config = load_stream_config(cfg_path)

    print("\n🧭 网络学堂输入流配置向导")
    print(f"   配置文件: {cfg_path}")
    print("   建议优先使用 cookie 文件，避免明文写入配置。")

    output_dir = _ask("1) 输出目录", str(config.get("output_dir", "")))
    mode = _ask("2) 同步范围 all/files/homework", str(config.get("only", "all"))).lower()
    if mode not in ("all", "files", "homework"):
        mode = "all"

    use_cookie_file = _ask_yes_no("3) 使用 cookie 文件而不是明文 cookie", default_yes=bool(config.get("cookie_file")))
    cookie = ""
    cookie_file = ""
    if use_cookie_file:
        cookie_file = _ask("   cookie 文件路径", str(config.get("cookie_file", "")).strip() or "~/.config/machine/learn.cookie")
    else:
        cookie = _ask("   直接粘贴 Cookie", str(config.get("cookie", "")).strip())

    use_env_csrf = _ask_yes_no("4) CSRF Token 使用环境变量 LEARN_CSRF", default_yes=not bool(config.get("csrf_token")))
    csrf_token = "" if use_env_csrf else _ask("   直接填写 CSRF Token", str(config.get("csrf_token", "")).strip())

    semester_id = _ask("5) 学期 ID（留空=自动当前学期）", str(config.get("semester_id", "")).strip())
    lang = _ask("6) 语言 zh/en", str(config.get("lang", "zh"))).lower()
    if lang not in ("zh", "en"):
        lang = "zh"
    timeout_raw = _ask("7) 超时时间（秒）", str(config.get("timeout", 20.0)))
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 20.0

    homework_attachments = _ask_yes_no(
        "8) 下载作业附件（会访问作业详情页）",
        default_yes=bool(config.get("homework_attachments", True)),
    )
    dry_run = _ask_yes_no("9) Dry-run（仅抓索引，不下载）", default_yes=bool(config.get("dry_run", False)))

    interval_raw = _ask(
        "10) 定时拉取间隔（分钟）",
        str(config.get("schedule_interval_minutes", DEFAULT_INTERVAL_MINUTES)),
    )
    try:
        interval_minutes = max(1, int(interval_raw))
    except ValueError:
        interval_minutes = DEFAULT_INTERVAL_MINUTES

    config.update(
        {
            "output_dir": output_dir,
            "only": mode,
            "cookie": cookie,
            "cookie_file": cookie_file,
            "csrf_token": csrf_token,
            "semester_id": semester_id,
            "lang": lang,
            "base_url": str(config.get("base_url", "https://learn.tsinghua.edu.cn")),
            "timeout": timeout,
            "dry_run": dry_run,
            "homework_attachments": homework_attachments,
            "schedule_interval_minutes": interval_minutes,
        }
    )
    saved = save_stream_config(config, cfg_path)
    print(f"\n✅ 配置已保存: {saved}")
    print(f"   立刻单次执行: {_cli_name()} learn-stream-schedule run-once")

    if _ask_yes_no("是否立即启动定时拉取后台进程", default_yes=False):
        result = start_scheduler(config_path=cfg_path, interval_minutes=interval_minutes, run_now=True)
        if result.get("started"):
            print(f"   ✅ 已启动，PID={result.get('pid')}")
            print(f"   📄 日志: {result.get('log_file')}")
        else:
            reason = result.get("reason", "unknown")
            print(f"   ⚠️ 启动失败: {reason}")


def cmd_learn_stream_auth(args):
    """从浏览器自动导入网络学堂 Cookie / CSRF。"""
    from secretary.input_streams.browser_auth import import_learn_auth_from_browser
    from secretary.input_streams.scheduler import get_default_config_path

    config_path = args.config or str(get_default_config_path())
    try:
        result = import_learn_auth_from_browser(
            config_path=config_path,
            browser=getattr(args, "browser", "auto"),
            open_login=bool(getattr(args, "open_login", False)),
            base_url=getattr(args, "base_url", "https://learn.tsinghua.edu.cn"),
        )
    except Exception as e:
        print(f"❌ 浏览器导入失败: {e}")
        print("   提示：先在本机浏览器登录 learn.tsinghua.edu.cn，再重试。")
        print("   如缺少依赖，请执行: pip install browser-cookie3")
        return

    print("✅ 已从浏览器导入登录态")
    print(f"   浏览器: {result.get('browser')}")
    print(f"   配置: {result.get('config_path')}")
    print(f"   Cookie 文件: {result.get('cookie_file')}")
    print(f"   CSRF: {'已找到' if result.get('csrf_found') else '未找到（请在配置中补充）'}")
    print(f"   下一步: {_cli_name()} learn-stream-schedule run-once")


def cmd_learn_stream_schedule(args):
    """管理网络学堂定时拉取任务。"""
    from secretary.input_streams.scheduler import (
        get_default_config_path,
        get_scheduler_status,
        run_once_from_config,
        run_scheduler_loop,
        start_scheduler,
        stop_scheduler,
    )

    action = args.action
    config_path = args.config or str(get_default_config_path())

    if action == "run-once":
        try:
            result = run_once_from_config(config_path=config_path)
        except Exception as e:
            print(f"❌ 单次拉取失败: {e}")
            return
        stats = result.get("stats", {})
        print("✅ 单次拉取完成")
        print(f"   课程: {stats.get('courses_processed', 0)}")
        print(f"   课件: {stats.get('courseware_downloaded', 0)}/{stats.get('courseware_items', 0)}")
        print(
            "   作业附件: "
            f"{stats.get('homework_attachments_downloaded', 0)}/{stats.get('homework_attachments', 0)}"
        )
        print(f"   错误: {stats.get('errors', 0)}")
        return

    if action == "start":
        try:
            result = start_scheduler(
                config_path=config_path,
                interval_minutes=args.interval_minutes,
                run_now=not args.no_run_now,
            )
        except Exception as e:
            print(f"❌ 启动失败: {e}")
            return
        if result.get("started"):
            print("✅ 定时拉取已启动")
            print(f"   PID: {result.get('pid')}")
            print(f"   间隔: {result.get('interval_minutes')} 分钟")
            print(f"   配置: {result.get('config_path')}")
            print(f"   日志: {result.get('log_file')}")
        else:
            print("ℹ️ 定时拉取已在运行")
            status = result.get("status") or {}
            print(f"   PID: {status.get('pid')}")
            print(f"   配置: {status.get('config_path')}")
        return

    if action == "stop":
        result = stop_scheduler()
        if result.get("stopped"):
            forced = " (SIGKILL)" if result.get("forced") else ""
            print(f"✅ 定时拉取已停止{forced}")
            print(f"   PID: {result.get('pid')}")
        else:
            print(f"ℹ️ 未停止: {result.get('reason')}")
        return

    if action == "status":
        status = get_scheduler_status()
        running = bool(status.get("running"))
        print("🕒 定时拉取状态")
        print(f"   运行中: {'是' if running else '否'}")
        print(f"   PID: {status.get('pid')}")
        print(f"   配置: {status.get('config_path')}")
        print(f"   间隔(分钟): {status.get('interval_minutes')}")
        print(f"   启动时间: {status.get('started_at')}")
        last_run = status.get("last_run")
        if last_run:
            print(f"   最近执行: {last_run.get('timestamp')}")
            print(f"   最近成功: {last_run.get('success')}")
            if isinstance(last_run.get("stats"), dict):
                stats = last_run["stats"]
                print(
                    "   最近统计: "
                    f"课程={stats.get('courses_processed', 0)}, "
                    f"课件={stats.get('courseware_downloaded', 0)}/{stats.get('courseware_items', 0)}, "
                    f"作业附件={stats.get('homework_attachments_downloaded', 0)}/{stats.get('homework_attachments', 0)}, "
                    f"错误={stats.get('errors', 0)}"
                )
        return

    if action == "loop":
        try:
            run_scheduler_loop(
                config_path=config_path,
                interval_minutes=args.interval_minutes,
                run_now=args.run_now,
            )
        except Exception as e:
            print(f"❌ 定时循环异常: {e}")
        return

    print(f"❌ 未知动作: {action}")


# ============================================================
#  report 命令
# ============================================================

# ============================================================
#  target 命令 — 秘书全局目标
# ============================================================

def cmd_target(args):
    """便捷命令：自动选名 + hire boss。等价于 hire <auto> boss <auto> -d <goal>"""
    goal = " ".join(args.goal) if isinstance(args.goal, list) else args.goal

    if not goal:
        print("❌ 请提供目标描述")
        print(f"   用法: {_cli_name()} target \"目标描述\"")
        print(f"   示例: {_cli_name()} target \"完成登录模块\"")
        return

    from secretary.agents import pick_available_name

    boss_name = pick_available_name(preferred_names=["yks", "ykx", "yky", "aks", "akx"])
    worker_candidates = [n for n in ["ykc", "ykz", "aky", "akz", "akc"] if n != boss_name]
    worker_name = pick_available_name(preferred_names=worker_candidates)

    should_start = not getattr(args, "no_start", False)
    _create_boss(boss_name, goal, worker_name, start=should_start)


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
👔 hire boss 的便捷写法

等价于 {name} hire <name> boss <worker> -d <goal>

用法:
  {name} boss <名称> "目标" <worker名称> [--no-start]

示例:
  {name} boss myboss "完成登录" sen            创建并启动
  {name} boss myboss "完成登录" sen --no-start  仅创建
  {name} hire myboss                          后续启动
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
👷 统一的 agent 招募入口

语法:
  {name} hire <name> <type> [dep_agent ...] [-d desc] [--no-start]

依赖 agent 的类型由主 agent 自动确定（如 boss 的依赖自动按 worker 创建）。

示例:
  {name} hire alice                           worker（默认类型）
  {name} hire yks secretary                   secretary
  {name} hire myboss boss myworker            boss 监控 myworker
  {name} hire myboss boss sen -d "完成登录"   boss + 目标描述
  {name} hire myboss boss sen --no-start      仅创建，不启动
  {name} hire myboss                          重启空闲的 agent

任务写入:
  {name} task "描述" --agent myboss            直接写入 agents/myboss/tasks/
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
📺 查看 agent 日志

用法:
  {name} check <agent名称>       翻页浏览（q 退出，/ 搜索，G 跳到底部）
  {name} check <agent名称> -f    实时跟踪（Ctrl+C 退出）
""",
            "upgrade": f"""
🔄 更新 Kai 到最新版本

用法:
  {name} upgrade

说明:
  从远端 git 仓库拉取最新代码（git pull）并重新安装（pip install -e .）。
  仅支持 editable install 方式。如果有本地未提交更改会自动 stash/恢复。
  系统每天自动检查一次是否有新版本，有的话会在启动时提示。
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
            "learn-stream": f"""
🌐 网络学堂输入流（同步作业/课件）

用法:
  {name} learn-stream --output <目录> --cookie "<cookie>" --csrf <token>

常用参数:
  --semester <学期ID>         指定学期，不传则自动取当前学期
  --only all|files|homework   只同步课件或作业
  --dry-run                   仅抓索引，不下载文件
  --cookie-file <文件路径>     从文件读取整段 Cookie

说明:
  需要有效登录 Cookie 和 CSRF Token，可通过参数或环境变量 LEARN_COOKIE / LEARN_CSRF 提供。
""",
            "learn-stream-setup": f"""
🧭 交互式配置网络学堂输入流

用法:
  {name} learn-stream-setup

说明:
  逐步引导你配置输出目录、Cookie/CSRF 来源、拉取范围和定时间隔，
  并可在结束后直接启动定时拉取。
""",
            "learn-stream-auth": f"""
🔐 浏览器自动导入网络学堂登录态

用法:
  {name} learn-stream-auth [--browser auto|chrome|chromium|edge|firefox|brave]

说明:
  读取本机浏览器中 learn.tsinghua.edu.cn 的 Cookie，
  自动写入 learn-stream 配置（优先写 cookie_file），并尝试自动解析 CSRF。

常用参数:
  --open-login           先自动打开登录页
  --config <路径>         指定配置文件（默认 learn_stream/config.json）
  --base-url <URL>       默认 https://learn.tsinghua.edu.cn
""",
            "learn-stream-schedule": f"""
⏱️ 定时拉取管理

用法:
  {name} learn-stream-schedule start [--interval-minutes 360]
  {name} learn-stream-schedule stop
  {name} learn-stream-schedule status
  {name} learn-stream-schedule run-once

说明:
  使用 `learn_stream/config.json` 配置执行定时拉取；start 会在后台运行循环任务。
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
            print("\n可用命令列表:")
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
        ("📝 任务", [
            ("task", "提交任务（写入 agent 的 tasks/ 目录）"),
            ("learn-stream", "同步网络学堂作业/课件到本地目录"),
            ("learn-stream-setup", "交互式配置 learn-stream"),
            ("learn-stream-auth", "从浏览器自动导入 Cookie/CSRF"),
            ("learn-stream-schedule", "管理定时拉取（start/stop/status）"),
        ]),
        ("👷 Agent管理 (hire 统一入口)", [
            ("hire", "招募 agent: hire <name> <type> [dep_agent ...]"),
            ("fire", "解雇 agent"),
            ("workers", "列出已注册的 agent"),
            ("check", "实时查看 agent 日志输出"),
            ("boss", "hire boss 便捷写法（含 goal 参数）"),
            ("target", "自动选名的 hire boss 快捷方式"),
        ]),
        ("📚 技能", [
            ("skills", "列出所有已学技能"),
            ("learn", "学习新技能"),
            ("forget", "忘掉一个技能"),
            ("use", "使用技能（直接写入 tasks/）"),
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
        ("🧹 维护", [
            ("upgrade", "拉取最新代码并重新安装"),
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
    """无子命令时进入交互模式。"""
    # 工作区优先级：--workspace > SECRETARY_WORKSPACE 环境变量 > CWD
    if not initial_args.workspace and not os.environ.get("SECRETARY_WORKSPACE"):
        # 始终使用当前工作目录，不读取 kai base 持久化配置
        cfg.apply_workspace(Path.cwd().resolve())
    
    if initial_args.workspace:
        ws = Path(initial_args.workspace).resolve()
        cfg.apply_workspace(ws)

    name = _cli_name()
    # 彩色提示符：粗体青色名称 + 暗色箭头
    prompt = f"\033[1;36m{name}\033[0m\033[2m ›\033[0m "

    cfg.ensure_dirs()

    try:
        from secretary.agent_registry import initialize_registry
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass

    from secretary.agents import list_workers
    agents = list_workers()
    agent_summary = f"\033[1m{len(agents)}\033[0m 个 agent" if agents else "\033[2m无 agent\033[0m"
    print(f"\n\033[1;36m{name}\033[0m  \033[2m—\033[0m  {agent_summary}  \033[2m│  help 帮助  │  exit 退出\033[0m")

    # 每日更新检查
    try:
        hint = _check_for_updates()
        if hint:
            print(f"   {hint}")
    except Exception:
        pass

    # 后台静默启动空闲 agents
    try:
        _auto_start_agents(silent=True)
    except Exception:
        pass

    while True:
        try:
            line = input(prompt).strip()
        except KeyboardInterrupt:
            # Ctrl+C: 清空当前行，重新等待输入
            print()  # 换行，避免提示符粘在 ^C 后面
            continue
        except EOFError:
            print("\n\033[0m👋 退出")
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            print("\033[0m👋 退出")
            break

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
            print("  未知命令。输入 help 查看可用命令")
            continue
        if not getattr(args, "command", None):
            print("  输入 help 查看可用命令")
            continue

        # base / name / model / help 不需要 ensure_dirs
        if args.command in ("base", "name", "model", "help", "upgrade", "learn-stream"):
            handlers[args.command](args)
            continue

        cfg.ensure_dirs()

        # 刷新可用技能列表 (用户可能刚 learn 了新技能)
        _refresh_skill_names(skill_names)

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

任务 (统一写入 agent 的 tasks/ 目录):
  {name} task "你的任务描述"                  通过秘书分配
  {name} task "优化性能" --agent sen          直接写入 sen 的 tasks/
  {name} task "目标" --agent myboss           直接写入 boss 的 tasks/

Agent管理 (hire 统一入口):
  {name} hire alice                 👷 招募 worker
  {name} hire <n> secretary         🤖 招募 secretary
  {name} hire <n> boss <worker>     👔 招募 boss (依赖 worker 自动创建)
  {name} hire <n> --no-start        📋 仅注册，不启动扫描器
  {name} hire <n>                   🔄 重启已注册但空闲的 agent
  {name} fire alice                 🔥 解雇 agent
  {name} workers                    📋 列出已注册的 agent

技能:
  {name} skills                     📚 列出所有技能
  {name} <技能名>                   🎯 使用技能 (直接写入 tasks/)
  {name} learn "描述" my-skill      📖 学习新技能
  {name} forget my-skill            🧹 忘掉技能

内置技能: evolving | analysis | debug

后台:
  {name} boss <n> "目标" <worker>   👔 hire boss 便捷写法
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
        help="提交任务（写入指定 agent 的 tasks/ 目录）",
        description="提交任务描述。默认通过秘书分配；使用 --agent 直接写入指定 agent 的 tasks/ 目录。",
    )
    p.add_argument("request", nargs="+", help="任务描述")
    p.add_argument("--time", type=int, default=0, help=time_help)
    p.add_argument("--worker", "--agent", type=str, default=None, dest="worker",
                   help="直接写入指定 agent 的 tasks/ 目录（支持 worker/boss/secretary 等任意类型）")
    
    # ---- boss (hire boss 的便捷别名，支持直接传 goal) ----
    p = subparsers.add_parser("boss",
        help="👔 hire boss 的便捷写法（等价于 hire <name> boss <worker> -d <goal>）")
    p.add_argument("boss_name", help="Boss名称")
    p.add_argument("goal", help="持续目标描述（'task' 则从已有 goal.md 读取）")
    p.add_argument("worker_name", help="监控的worker名称")
    p.add_argument("max_executions", type=int, nargs="?", default=None,
                   help="最大执行次数（可选）")
    p.add_argument("--no-start", action="store_true",
                   help="仅创建目录和配置，不启动扫描器")
    

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
        help="统一招募 agent（worker/secretary/boss/recycler）",
        description=(
            "统一的 agent 创建入口。\n"
            "语法: hire <name> <type> [dep_agent ...] [-d desc] [--no-start]\n\n"
            "依赖 agent 的类型由主 agent 的逻辑自动确定：\n"
            "  hire myboss boss myworker   — myworker 自动按 worker 创建\n\n"
            "已存在但未运行的 agent 再次 hire 会启动其扫描器。\n"
            "任务通过 task --agent <name> 直接写入 agents/<name>/tasks/ 目录。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("worker_names", nargs="*", default=None,
                   help="<name> [type] [dep_agent ...] — 如 alice worker、myboss boss myworker")
    p.add_argument("-d", "--description", type=str, default="",
                   help="描述（对 boss 类型同时作为目标描述）")
    p.add_argument("--no-start", action="store_true",
                   help="仅注册和创建目录，不启动扫描器")

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

    # ---- target (hire boss 的快捷方式，自动选名) ----
    p = subparsers.add_parser("target",
        help="🎯 自动选名的 hire boss 快捷方式")
    p.add_argument("goal", nargs="+", help="持续目标描述")
    p.add_argument("--no-start", action="store_true",
                   help="仅创建目录和配置，不启动扫描器")

    # ---- learn-stream ----
    p = subparsers.add_parser(
        "learn-stream",
        help="🌐 同步网络学堂作业与课件到本地目录",
        description=(
            "从清华网络学堂拉取作业与课件，写入本地目录。\n"
            "需要已登录 Cookie + CSRF Token。"
        ),
    )
    p.add_argument("--output", required=True, help="输出目录")
    p.add_argument("--cookie", type=str, default=None, help="登录 Cookie（整段 header 值）")
    p.add_argument("--cookie-file", type=str, default=None, help="从文件读取 Cookie（优先于环境变量）")
    p.add_argument("--csrf", type=str, default=None, help="CSRF Token")
    p.add_argument("--semester", type=str, default=None, help="学期 ID（不传则自动取当前学期）")
    p.add_argument("--lang", type=str, default="zh", choices=["zh", "en"], help="课程接口语言，默认 zh")
    p.add_argument(
        "--only",
        type=str,
        default="all",
        choices=["all", "files", "homework"],
        help="同步范围：all/files/homework",
    )
    p.add_argument("--base-url", type=str, default="https://learn.tsinghua.edu.cn", help="网络学堂基地址")
    p.add_argument("--timeout", type=float, default=20.0, help="HTTP 超时时间（秒）")
    p.add_argument("--dry-run", action="store_true", help="只抓取索引，不下载文件")
    p.add_argument(
        "--no-homework-attachments",
        action="store_false",
        dest="homework_attachments",
        help="不解析并下载作业详情页附件",
    )

    # ---- learn-stream-setup ----
    subparsers.add_parser(
        "learn-stream-setup",
        help="🧭 交互式配置网络学堂输入流",
        description="通过交互问答配置 learn-stream 参数，并可选启动定时拉取。",
    )

    # ---- learn-stream-auth ----
    p = subparsers.add_parser(
        "learn-stream-auth",
        help="🔐 从浏览器自动导入网络学堂登录态",
        description="自动读取浏览器中的 learn.tsinghua.edu.cn Cookie，并尝试提取 CSRF。",
    )
    p.add_argument(
        "--browser",
        type=str,
        default="auto",
        choices=["auto", "chrome", "chromium", "edge", "firefox", "brave"],
        help="指定浏览器（默认 auto 自动尝试）",
    )
    p.add_argument(
        "--open-login",
        action="store_true",
        help="先自动打开网络学堂登录页",
    )
    p.add_argument(
        "--config",
        type=str,
        default=None,
        help="配置文件路径（默认: BASE_DIR/learn_stream/config.json）",
    )
    p.add_argument(
        "--base-url",
        type=str,
        default="https://learn.tsinghua.edu.cn",
        help="网络学堂登录页地址",
    )

    # ---- learn-stream-schedule ----
    p = subparsers.add_parser(
        "learn-stream-schedule",
        help="⏱️ 管理网络学堂定时拉取",
        description="管理 learn-stream 定时拉取任务（后台循环）。",
    )
    p.add_argument(
        "action",
        choices=["start", "stop", "status", "run-once", "loop"],
        help="动作: start/stop/status/run-once/loop",
    )
    p.add_argument(
        "--config",
        type=str,
        default=None,
        help="配置文件路径（默认: BASE_DIR/learn_stream/config.json）",
    )
    p.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="定时拉取间隔（分钟）；不传则读取配置",
    )
    p.add_argument(
        "--run-now",
        action="store_true",
        help="(loop 动作) 启动后立刻先执行一次",
    )
    p.add_argument(
        "--no-run-now",
        action="store_true",
        help="(start 动作) 启动后先等待一个周期再执行",
    )

    # ---- report ----
    # ---- help ----
    p = subparsers.add_parser("help", help="❓ 显示帮助信息")
    p.add_argument("command_name", nargs="?", default=None,
                   help="命令名称 (可选,显示特定命令的详细帮助)")

    # ---- check / clean-logs / clean-processes ----
    p = subparsers.add_parser("check", help="📺 查看 agent 日志（翻页浏览，q 退出）")
    p.add_argument("worker_name", help="agent 名称")
    p.add_argument("-f", "--follow", action="store_true", help="实时跟踪模式（类似 tail -f）")
    p.add_argument("--chat", action="store_true", help="查看对话内容（dialog/ 目录）而非系统日志")
    p = subparsers.add_parser("chat", help="💬 向 agent 的 session 直接发送消息")
    p.add_argument("agent_name", help="agent 名称")
    subparsers.add_parser("clean-logs", help="🧹 清理 logs/ 下的日志文件")
    subparsers.add_parser("clean-processes", help="🧹 清理泄露的 worker 进程记录")

    # ---- dashboard ----
    p = subparsers.add_parser("dashboard", help="🌐 打开 Web 仪表板 (localhost:12345)")
    p.add_argument("--port", type=int, default=12345, help="监听端口 (默认 12345)")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    # ---- upgrade ----
    subparsers.add_parser("upgrade", help="🔄 从远端拉取最新代码并重新安装")

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
        "chat": cmd_chat,
        "clean-logs": cmd_clean_logs,
        "clean-processes": cmd_clean_processes,
        "dashboard": cmd_dashboard,
        "upgrade": cmd_upgrade,
        "base": cmd_base,
        "name": cmd_name,
        "model": cmd_model,
        "learn-stream": cmd_learn_stream,
        "learn-stream-setup": cmd_learn_stream_setup,
        "learn-stream-auth": cmd_learn_stream_auth,
        "learn-stream-schedule": cmd_learn_stream_schedule,
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

    # 工作区优先级：--workspace > SECRETARY_WORKSPACE 环境变量 > CWD
    # 注意：kai base 的持久化配置不再自动生效，避免移动项目目录后使用旧路径导致混乱。
    # 若需固定工作区，请设置环境变量 SECRETARY_WORKSPACE 或每次使用 --workspace。
    if args.workspace:
        ws = Path(args.workspace).resolve()
        cfg.apply_workspace(ws)
    elif not os.environ.get("SECRETARY_WORKSPACE"):
        # 没有 --workspace 和环境变量时，始终使用当前工作目录
        cfg.apply_workspace(Path.cwd().resolve())
    
    # 初始化 agent 类型注册表（在启动时自动加载自定义类型）
    try:
        from secretary.agent_registry import initialize_registry
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass  # 如果初始化失败，不影响其他功能

    # base / name / model / help 命令不需要 ensure_dirs
    if args.command in ("base", "name", "model", "help", "upgrade", "learn-stream", "learn-stream-auth"):
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

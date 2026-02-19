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
  kai target 任务1 任务2  设定秘书全局目标
  kai target --clear   清空全局目标
  kai target           列出当前全局目标
"""
import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.settings import (
    get_cli_name, set_cli_name, get_base_dir, set_base_dir,
    get_model, set_model, get_language, load_settings,
)


def _cli_name() -> str:
    """获取当前 CLI 命令名 (用于帮助文本)"""
    return get_cli_name()


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
#  任务提交
# ============================================================

def _write_kai_task(request: str, min_time: int = 0) -> Path:
    """公用：将任务写入 kai 的 tasks 目录，由 kai 扫描器处理（run_secretary）。
    与 task 命令不指定 --worker 时行为一致。返回写入的文件路径。
    """
    cfg.KAI_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    task_file_name = f"task-{timestamp}.md"
    task_file = cfg.KAI_TASKS_DIR / task_file_name
    task_content = request
    if min_time > 0:
        task_content += f"\n\n<!-- min_time: {min_time} -->\n"
    task_file.write_text(task_content, encoding="utf-8")
    return task_file


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

    # 如果指定了 worker，直接写入该 worker 的 tasks 目录；否则交给下面写 kai tasks
    if worker_name:
        from secretary.agents import get_worker, register_worker, _worker_tasks_dir
        import secretary.config as cfg
        
        # 确保 worker 存在
        worker = get_worker(worker_name)
        if not worker:
            print(f"ℹ️  Worker '{worker_name}' 不存在，自动创建...")
            register_worker(worker_name, description=f"由任务分配创建")
            worker = get_worker(worker_name)
        
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
    from secretary.agents import list_workers, _worker_tasks_dir
    import subprocess
    
    # 收集所有 worker 的任务文件（用于检测新任务）
    before = {}
    for w in list_workers():
        wtd = _worker_tasks_dir(w["name"])
        if wtd.exists():
            for f in wtd.glob("*.md"):
                before[f"{w['name']}/{f.name}"] = f.stat().st_mtime
    
    print(f"\n📨 提交任务: {request}")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    print(f"   ⏳ 后台执行中，输出写入 {cfg.KAI_SECRETARY_LOG}")
    print(f"   使用 `{_cli_name()} check kai` 查看日志\n")

    # 后台执行，输出写到 kai 日志目录
    cfg.KAI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    secretary_log_file = cfg.KAI_SECRETARY_LOG
    
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
    request = " ".join(args.request)
    worker_name = getattr(args, "worker", None)
    # 如果指定了 worker，直接写入任务文件（前台执行）
    if worker_name:
        _submit_task(request, min_time=args.time, worker_name=worker_name)
    else:
        # 复用：将任务写入 kai 的 tasks/ 目录，由 kai 的扫描器处理
        task_file = _write_kai_task(request, min_time=args.time)
        print(f"\n📨 任务已提交到 kai")
        print(f"   ✅ 任务文件: {task_file}")
        if args.time > 0:
            print(f"   ⏱️ 最低执行时间: {args.time}s")
        print(f"   💡 使用 `{_cli_name()} check kai` 查看 kai 的处理日志")
        print(f"   💡 确保 kai 的扫描器正在运行（`{_cli_name()} start kai`）")


def _run_keep_monitor(goal: str, worker_name: str):
    """内部函数：执行 keep 监控循环（使用 agent_loop.run_loop，触发条件为 tasks+ongoing 均空）。"""
    import json
    from secretary.agents import get_worker, register_worker, _worker_tasks_dir, _worker_ongoing_dir
    from secretary.agent_loop import run_loop
    import secretary.config as cfg

    # 确保 worker 存在并启动
    worker = get_worker(worker_name)
    if not worker:
        print(f"ℹ️  Worker '{worker_name}' 不存在，自动创建并启动...")
        register_worker(worker_name, description=f"持续监控模式: {goal[:50]}")
        worker = get_worker(worker_name)
        print(f"🚀 自动启动 worker '{worker_name}'...")
        class StartArgs:
            def __init__(self):
                self.worker_names = [worker_name]
                self.once = False
        cmd_start(StartArgs())
    if worker.get("pid") and not _check_process_exists(worker["pid"]):
        print(f"⚠️  Worker '{worker_name}' 的进程不存在，重新启动...")
        class StartArgs:
            def __init__(self):
                self.worker_names = [worker_name]
                self.once = False
        cmd_start(StartArgs())
    elif not worker.get("pid"):
        print(f"⚠️  Worker '{worker_name}' 未运行，启动中...")
        class StartArgs:
            def __init__(self):
                self.worker_names = [worker_name]
                self.once = False
        cmd_start(StartArgs())

    goal_file = cfg.WORKERS_DIR / worker_name / "keep-goal.md"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text(f"# 持续目标\n\n{goal}\n", encoding="utf-8")

    scan_interval = 10
    print(f"\n📊 开始监控循环（每 {scan_interval} 秒检查一次）...")
    print(f"   按 Ctrl+C 退出\n")

    def _build_keep_request():
        completed_tasks_info = []
        if cfg.STATS_DIR.exists():
            for report_file in sorted(cfg.STATS_DIR.glob("*-stats.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                try:
                    stats_data = json.loads(report_file.read_text(encoding="utf-8"))
                    task_name = report_file.stem.replace("-stats", "")
                    summary = stats_data.get("summary", "") if isinstance(stats_data, dict) else ""
                    completed_tasks_info.append({"name": task_name, "summary": summary})
                except Exception:
                    pass
        if not completed_tasks_info and cfg.REPORT_DIR.exists():
            for report_file in sorted(cfg.REPORT_DIR.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
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
        completed_summary = ""
        if completed_tasks_info:
            completed_summary = "\n已完成的任务：\n"
            for i, task_info in enumerate(completed_tasks_info, 1):
                completed_summary += f"{i}. {task_info['name']}"
                if task_info.get('summary'):
                    s = task_info['summary']
                    completed_summary += f" - {s[:150] + '...' if len(s) > 150 else s}"
                completed_summary += "\n"
        return f"""【持续监控模式】当前任务队列为空，请基于以下信息决定下一步应该做什么：

持续目标：{goal}
{completed_summary}

请分析持续目标和已完成的工作，决定下一步应该做什么来推进目标，并生成一个具体的、可执行的任务分配给 worker '{worker_name}'。"""

    def trigger_fn():
        tasks_dir = _worker_tasks_dir(worker_name)
        ongoing_dir = _worker_ongoing_dir(worker_name)
        tasks = list(tasks_dir.glob("*.md")) if tasks_dir.exists() else []
        ongoing = list(ongoing_dir.glob("*.md")) if ongoing_dir.exists() else []
        if len(tasks) == 0 and len(ongoing) == 0:
            return [None]  # 一项占位，表示需要生成任务
        return []

    def process_fn(_item):
        print(f"\n📝 [{datetime.now().strftime('%H:%M:%S')}] 检测到任务队列为空，让秘书决定新任务...")
        request = _build_keep_request()
        _write_kai_task(request)
        print(f"   ✅ 已提交到 kai 任务队列，由秘书处理（需运行 `{_cli_name()} start kai`）")

    def on_idle():
        tasks_dir = _worker_tasks_dir(worker_name)
        ongoing_dir = _worker_ongoing_dir(worker_name)
        tasks = list(tasks_dir.glob("*.md")) if tasks_dir.exists() else []
        ongoing = list(ongoing_dir.glob("*.md")) if ongoing_dir.exists() else []
        status = f"待处理: {len(tasks)}, 执行中: {len(ongoing)}"
        print(f"   [{datetime.now().strftime('%H:%M:%S')}] {status}", end="\r")

    run_loop(
        trigger_fn=trigger_fn,
        process_fn=process_fn,
        interval_sec=scan_interval,
        once=False,
        label="keep",
        verbose=True,
        on_idle=on_idle,
    )
    print(f"\n\n👋 退出持续监控模式")
    print(f"   持续目标已保存: {goal_file}")


def cmd_keep(args):
    """持续监控模式：为指定 worker 持续生成任务以推进目标 - 后台执行"""
    import secretary.config as cfg
    import subprocess
    
    goal = " ".join(args.goal) if isinstance(args.goal, list) else args.goal
    worker_name = args.worker or cfg.DEFAULT_WORKER_NAME
    
    # 检查是否在后台模式（通过环境变量）
    if os.environ.get("KAI_KEEP_BACKGROUND") == "1":
        # 已经在后台，直接执行监控循环
        _run_keep_monitor(goal, worker_name)
        return
    
    # 后台执行，输出写到 kai 日志目录（keep 由 kai 驱动，与 worker 的 scanner.log 分开）
    cfg.KAI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    keep_log_file = cfg.KAI_KEEP_LOG
    
    print(f"\n🔄 启动持续监控模式（后台执行）")
    print(f"   目标: {goal}")
    print(f"   Worker: {worker_name}")
    print(f"   日志: {keep_log_file}")
    print(f"   使用 `{_cli_name()} check kai` 查看 kai 相关日志（scanner/keep 等）\n")
    
    # 构建命令
    sub_cmd = [sys.executable, "-m", "secretary.cli", "keep"] + args.goal
    if args.worker:
        sub_cmd.extend(["--worker", args.worker])
    
    # 设置环境变量
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["KAI_KEEP_BACKGROUND"] = "1"  # 标记为后台模式
    
    # 后台执行
    fh = open(keep_log_file, "a", encoding="utf-8")
    fh.write(f"# Keep mode started: {goal[:100]}\n")
    fh.write(f"# Worker: {worker_name}\n")
    fh.write(f"# Started: {datetime.now().isoformat()}\n\n")
    fh.flush()
    
    proc = subprocess.Popen(
        sub_cmd,
        stdout=fh,
        stderr=subprocess.STDOUT,
        cwd=str(cfg.BASE_DIR),
        env=env,
    )
    fh.close()
    
    print(f"✅ 持续监控模式已在后台启动 (PID={proc.pid})")
    print(f"   使用 `{_cli_name()} check kai` 查看输出（keep 日志: {cfg.KAI_KEEP_LOG.name}）")


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
        print(f"   💡 用 `{_cli_name()} start {cfg.DEFAULT_WORKER_NAME}` 启动工作者来执行")
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
    """招募工作者 (只注册，不启动扫描)，支持多个名字"""
    from secretary.agents import pick_random_name, register_worker, get_worker
    import secretary.config as cfg

    names = getattr(args, "worker_names", None) or []
    if not names:
        names = [pick_random_name()]
        print(f"🎲 随机招募: {names[0]}")
    description = args.description if hasattr(args, "description") else ""

    for worker_name in names:
        existing = get_worker(worker_name)
        if existing:
            print(f"ℹ️  Worker '{worker_name}' 已存在")
            print(f"   使用 `{_cli_name()} start {worker_name}` 启动扫描")
            continue
        register_worker(worker_name, description=description)
        print(f"✅ 已招募 worker: {worker_name}")
        print(f"   使用 `{_cli_name()} start {worker_name}` 启动扫描")


def cmd_start(args):
    """启动 worker 的扫描器 (开始处理任务) - 后台执行，支持多个名字"""
    from secretary.agents import get_worker, _worker_logs_dir, update_worker_status
    import secretary.config as cfg
    import subprocess
    import os

    names = getattr(args, "worker_names", None) or []
    if not names:
        names = [cfg.DEFAULT_WORKER_NAME]

    for worker_name in names:
        # 特殊处理：如果 worker_name 是 "kai"，启动 kai 的扫描器
        if worker_name.lower() == "kai":
            cfg.KAI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
            scanner_log_file = cfg.KAI_SCANNER_LOG

            sub_cmd = [sys.executable, "-m", "secretary.kai_scanner"]
            if args.once:
                sub_cmd.append("--once")
            sub_cmd.append("--verbose")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            fh = open(scanner_log_file, "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(
                sub_cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=cfg.BASE_DIR,
                env=env,
                bufsize=1,
            )
            print(f"✅ Kai 的扫描器已在后台启动 (PID={proc.pid})")
            print(f"   日志: {scanner_log_file}")
            print(f"   使用 `{_cli_name()} check kai` 查看输出")
            print(f"   使用 `{_cli_name()} stop kai` 停止")
            continue

        worker = get_worker(worker_name)
        if not worker:
            print(f"❌ Worker '{worker_name}' 不存在")
            print(f"   使用 `{_cli_name()} hire {worker_name}` 先招募 worker")
            continue

        if worker.get("pid") and _check_process_exists(worker["pid"]):
            print(f"ℹ️  Worker '{worker_name}' 已在运行 (PID={worker['pid']})")
            continue

        log_dir = _worker_logs_dir(worker_name)
        log_dir.mkdir(parents=True, exist_ok=True)
        scanner_log_file = log_dir / "scanner.log"

        sub_cmd = [sys.executable, "-m", "secretary.scanner", "--worker", worker_name]
        if args.once:
            sub_cmd.append("--once")
        sub_cmd.append("--quiet")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        fh = open(scanner_log_file, "a", encoding="utf-8", buffering=1)
        proc = subprocess.Popen(
            sub_cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=cfg.BASE_DIR,
            env=env,
            bufsize=1,
        )
        update_worker_status(worker_name, "busy", pid=proc.pid)
        print(f"✅ Worker '{worker_name}' 已在后台启动 (PID={proc.pid})")
        print(f"   日志: {scanner_log_file}")
        print(f"   使用 `{_cli_name()} check {worker_name}` 查看输出")
        print(f"   使用 `{_cli_name()} stop {worker_name}` 停止")


def cmd_fire(args):
    """解雇 (删除) 一个或多个命名工人"""
    from secretary.agents import get_worker, remove_worker

    for worker_name in args.worker_names:
        info = get_worker(worker_name)
        if not info:
            print(f"❌ 没有叫 {worker_name} 的工人")
            print(f"   用 `{_cli_name()} workers` 查看所有工人")
            continue

        if info.get("ongoing_count", 0) > 0:
            print(f"⚠️  {worker_name} 还有 {info['ongoing_count']} 个任务在执行中!")
            print(f"   建议先停止其进程再解雇")

        success = remove_worker(worker_name)
        if success:
            print(f"🔥 已解雇工人: {worker_name}")
            print(f"   已删除 {worker_name}/ 目录及注册信息")
        else:
            print(f"❌ 解雇失败: {worker_name}")


def cmd_workers(args):
    """列出所有已招募的工人"""
    from secretary.agents import list_workers

    workers = list_workers()
    name = _cli_name()

    if not workers:
        print(f"\n👷 还没有招募任何工人")
        print(f"   用 `{name} hire alice` 来招募一个叫 alice 的工人！")
        print(f"   用 `{name} hire` 随机招募一个工人")
        print(f"   用 `{name} start sen` 启动默认 worker")
        return

    print(f"\n👷 已招募的工人 ({len(workers)} 个):\n")
    for w in workers:
        status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
        pid_str = f"PID={w['pid']}" if w.get("pid") else ""
        completed = w.get("completed_tasks", 0)
        pending = w.get("pending_count", 0)
        ongoing = w.get("ongoing_count", 0)
        desc = w.get("description", "") or ""
        print(f"   {status_icon} {w['name']:15s}  完成: {completed:3d}  待处理: {pending}  执行中: {ongoing}  {pid_str}")
        if desc:
            print(f"      📝 {desc}")
        recent = w.get("recent_tasks", [])
        if recent:
            print(f"      📋 最近: {', '.join(recent[-3:])}")

    print(f"\n   招募: {name} hire <名字>  (只注册，不启动)")
    print(f"   启动: {name} start <名字>  (开始扫描任务)")
    print(f"   解雇: {name} fire <名字>")


def cmd_recycle(args):
    """启动回收者 - 后台执行，不保留日志"""
    from secretary.recycler import run_recycler
    import subprocess
    import os
    
    # 检查是否在后台模式
    if os.environ.get("KAI_RECYCLE_BACKGROUND") == "1":
        # 已经在后台，直接执行
        run_recycler(once=args.once, verbose=False)
        return
    
    # 后台执行，不保留日志（输出到 /dev/null 或 NUL）
    print(f"\n♻️ 启动回收者（后台执行）")
    if args.once:
        print(f"   模式: 只执行一次")
    else:
        print(f"   模式: 持续运行")
    print()
    
    # 构建命令
    sub_cmd = [sys.executable, "-m", "secretary.recycler"]
    if args.once:
        sub_cmd.append("--once")
    
    # 设置环境变量
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["KAI_RECYCLE_BACKGROUND"] = "1"
    
    # 后台执行，不保留日志
    if sys.platform == "win32":
        null_file = open(os.devnull, "w")
    else:
        null_file = open(os.devnull, "w")
    
    proc = subprocess.Popen(
        sub_cmd,
        stdout=null_file,
        stderr=subprocess.STDOUT,
        cwd=str(cfg.BASE_DIR),
        env=env,
    )
    null_file.close()
    
    print(f"✅ 回收者已在后台启动 (PID={proc.pid})")


def cmd_monitor(args):
    """启动实时监控面板；--text/--once 时输出文本状态并退出，否则尝试 TUI（无 TUI 时退化为文本）"""
    from secretary.dashboard import run_monitor
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
    print(f"\n📺 启动监控面板（前台，刷新间隔 {args.interval}s，Ctrl+C 退出）\n")
    run_monitor(refresh_interval=args.interval)


# ============================================================
#  控制命令
# ============================================================

def cmd_stop(args):
    """停止指定 worker 或 kai 的进程，支持多个名字"""
    from secretary.agents import get_worker, update_worker_status
    import secretary.config as cfg

    for worker_name in args.worker_names:
        # 特殊处理：停止 kai
        if worker_name.lower() == "kai":
            pid = None
            try:
                if sys.platform == "win32":
                    result = subprocess.run(
                        ["wmic", "process", "where", "commandline like '%kai_scanner%'", "get", "processid"],
                        capture_output=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        output = result.stdout.decode("gbk", errors="ignore")
                        for line in output.splitlines():
                            line = line.strip()
                            if line and line.isdigit():
                                pid = int(line)
                                break
                else:
                    result = subprocess.run(
                        ["ps", "aux"],
                        capture_output=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        output = result.stdout.decode("utf-8", errors="ignore")
                        for line in output.splitlines():
                            if "kai_scanner" in line:
                                parts = line.split()
                                if len(parts) > 1:
                                    try:
                                        pid = int(parts[1])
                                        break
                                    except ValueError:
                                        continue
            except Exception as e:
                print(f"   ⚠️  查找 kai 进程时出错: {e}")

            if not pid:
                print(f"ℹ️  Kai 的扫描器没有运行中的进程")
            else:
                print(f"\n🛑 停止 kai 的扫描器 (PID={pid})...")
                _stop_process(pid, "kai")
            continue

        worker = get_worker(worker_name)
        if not worker:
            print(f"❌ Worker '{worker_name}' 不存在")
            print(f"   使用 `{_cli_name()} workers` 查看所有 worker")
            continue

        pid = worker.get("pid")
        if not pid:
            print(f"ℹ️  Worker '{worker_name}' 没有运行中的进程")
            continue

        print(f"\n🛑 停止 worker '{worker_name}' (PID={pid})...")
        _stop_process(pid, worker_name)
        update_worker_status(worker_name, "idle", pid=None)
        print(f"   📝 已更新 worker '{worker_name}' 状态为 idle，PID 已清除")


def _stop_process(pid: int, name: str):
    """停止指定 PID 的进程"""
    try:
        if sys.platform == "win32":
            # Windows: 先检查进程是否存在
            check_result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=5,
            )
            
            # 检查进程是否存在
            process_exists = False
            if check_result.returncode == 0 and check_result.stdout:
                try:
                    output = check_result.stdout.decode("gbk", errors="ignore")
                    if str(pid) in output and "信息" not in output:
                        process_exists = True
                except:
                    # 如果解码失败，尝试直接检查
                    if str(pid).encode() in check_result.stdout:
                        process_exists = True
            
            if not process_exists:
                print(f"   ℹ️  进程 PID={pid} 已不存在")
            else:
                # 进程存在，强制杀死
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                
                # 再次检查进程是否还存在
                verify_result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    timeout=5,
                )
                
                still_exists = False
                if verify_result.returncode == 0 and verify_result.stdout:
                    try:
                        output = verify_result.stdout.decode("gbk", errors="ignore")
                        if str(pid) in output and "信息" not in output:
                            still_exists = True
                    except:
                        if str(pid).encode() in verify_result.stdout:
                            still_exists = True
                
                if not still_exists:
                    print(f"   ✅ 已停止 {name} (PID={pid})")
                else:
                    print(f"   ⚠️  无法停止进程 PID={pid}，进程仍在运行")
        else:
            # Unix/Linux: 使用 kill
            try:
                os.kill(pid, 15)  # SIGTERM
                print(f"   ✅ 已发送停止信号给 {name} (PID={pid})")
                # 等待一下，如果还没停止就强制杀死
                import time
                time.sleep(1)
                try:
                    os.kill(pid, 0)  # 检查进程是否还存在
                    os.kill(pid, 9)  # SIGKILL
                    print(f"   ✅ 已强制停止 {name} (PID={pid})")
                except ProcessLookupError:
                    pass  # 进程已停止
            except ProcessLookupError:
                print(f"   ℹ️  进程 PID={pid} 已不存在")
    except Exception as e:
        print(f"   ⚠️  停止进程时出错: {e}")
    
    # 尝试停止进程
    print(f"\n🛑 停止 worker '{worker_name}' (PID={pid})...")
    try:
        if sys.platform == "win32":
            # Windows: 先检查进程是否存在
            check_result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                timeout=5,
            )
            
            # 检查进程是否存在
            process_exists = False
            if check_result.returncode == 0 and check_result.stdout:
                try:
                    output = check_result.stdout.decode("gbk", errors="ignore")
                    if str(pid) in output and "信息" not in output:
                        process_exists = True
                except:
                    # 如果解码失败，尝试直接检查
                    if str(pid).encode() in check_result.stdout:
                        process_exists = True
            
            if not process_exists:
                print(f"   ℹ️  进程 PID={pid} 已不存在")
            else:
                # 进程存在，强制杀死
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                
                # 再次检查进程是否还存在
                verify_result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    timeout=5,
                )
                
                still_exists = False
                if verify_result.returncode == 0 and verify_result.stdout:
                    try:
                        output = verify_result.stdout.decode("gbk", errors="ignore")
                        if str(pid) in output and "信息" not in output:
                            still_exists = True
                    except:
                        if str(pid).encode() in verify_result.stdout:
                            still_exists = True
                
                if not still_exists:
                    print(f"   ✅ 已停止 worker '{worker_name}' (PID={pid})")
                    # 更新 worker 状态：清除 pid，设置为 idle
                    update_worker_status(worker_name, "idle", pid=None)
                    print(f"   📝 已更新 worker '{worker_name}' 状态为 idle，PID 已清除")
                else:
                    print(f"   ⚠️  无法停止进程 PID={pid}，进程仍在运行")
                    # 即使无法停止，也清除记录的 PID（可能进程已经换了）
                    update_worker_status(worker_name, "idle", pid=None)
                    print(f"   📝 已清除 worker '{worker_name}' 的 PID 记录")
        else:
            # Unix/Linux: 使用 kill
            try:
                os.kill(pid, 15)  # SIGTERM
                print(f"   ✅ 已发送停止信号给 worker '{worker_name}' (PID={pid})")
                # 等待一下，如果还没停止就强制杀死
                import time
                time.sleep(1)
                try:
                    os.kill(pid, 0)  # 检查进程是否还存在
                    os.kill(pid, 9)  # SIGKILL
                    print(f"   ✅ 已强制停止 worker '{worker_name}' (PID={pid})")
                except ProcessLookupError:
                    pass  # 进程已停止
                # 更新 worker 状态：清除 pid，设置为 idle
                update_worker_status(worker_name, "idle", pid=None)
                print(f"   📝 已更新 worker '{worker_name}' 状态为 idle，PID 已清除")
            except ProcessLookupError:
                print(f"   ℹ️  进程 PID={pid} 已不存在")
                # 即使进程不存在，也清除记录的 PID
                update_worker_status(worker_name, "idle", pid=None)
                print(f"   📝 已清除 worker '{worker_name}' 的 PID 记录")
    except Exception as e:
        print(f"   ⚠️  停止进程时出错: {e}")
        # 即使出错，也尝试清除 PID 记录
        try:
            update_worker_status(worker_name, "idle", pid=None)
            print(f"   📝 已清除 worker '{worker_name}' 的 PID 记录")
        except:
            pass


def cmd_check(args):
    """实时查看 worker 或秘书的输出（类似 tail -f）"""
    from secretary.agents import get_worker, _worker_logs_dir, update_worker_status
    import threading
    import time
    
    worker_name = getattr(args, "worker_name", None)
    if not worker_name:
        print("❌ 请指定要查看的对象: worker 名、kai 或 keep")
        print(f"   用法: {_cli_name()} check <worker_name|kai|keep>")
        print(f"   示例: {_cli_name()} check sen  |  {_cli_name()} check kai  |  {_cli_name()} check keep")
        return
    
    # 检查是否是查看 kai 相关日志
    if worker_name.lower() == "kai":
        log_file = cfg.KAI_SCANNER_LOG
        if not log_file.exists():
            print(f"❌ Kai 的 scanner 日志不存在: {log_file.name}")
            print(f"   路径: {log_file}")
            print(f"   使用 `{_cli_name()} start kai` 启动 kai 的扫描器；keep 日志: `{_cli_name()} check keep`")
            return
        print(f"\n📺 实时查看 kai 的 scanner 输出")
        print(f"   日志: {log_file}")
        print(f"   按 'q' 退出查看模式")
        print(f"   按 Ctrl+C 退出")
        print(f"{'='*60}\n")
    elif worker_name.lower() == "keep":
        log_file = cfg.KAI_KEEP_LOG
        if not log_file.exists():
            print(f"❌ Keep 日志不存在: {log_file.name}")
            print(f"   路径: {log_file}")
            print(f"   使用 `{_cli_name()} keep \"目标\"` 启动持续监控后会产生此日志")
            return
        print(f"\n📺 实时查看 keep 模式输出")
        print(f"   日志: {log_file}")
        print(f"   按 'q' 退出查看模式")
        print(f"   按 Ctrl+C 退出")
        print(f"{'='*60}\n")
    else:
        # 检查 worker 是否存在
        worker = get_worker(worker_name)
        if not worker:
            print(f"❌ Worker '{worker_name}' 不存在")
            print(f"   使用 `{_cli_name()} workers` 查看所有 worker")
            return
        
        # 检查 worker 是否在运行
        pid = worker.get("pid")
        pid_info = ""
        if pid and _check_process_exists(pid):
            pid_info = f" (PID={pid})"
        else:
            print(f"ℹ️  Worker '{worker_name}' 没有运行中的进程")
            print(f"   使用 `{_cli_name()} start {worker_name}` 启动 worker")
            # 即使没有运行，也允许查看日志
        
        # 使用固定的日志文件
        log_dir = _worker_logs_dir(worker_name)
        if not log_dir.exists():
            print(f"❌ Worker '{worker_name}' 的日志目录不存在")
            return
        
        log_file = log_dir / "scanner.log"
        if not log_file.exists():
            print(f"❌ Worker '{worker_name}' 没有找到日志文件 (scanner.log)")
            return
        
        print(f"\n📺 实时查看 worker '{worker_name}' 的输出{pid_info}")
        print(f"   日志文件: {log_file.name}")
        print(f"   按 'q' 退出查看模式（不打断 worker）")
        print(f"   按 Ctrl+C 打断 worker 执行")
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
    
    # 如果用户按了 Ctrl+C，停止 worker（仅当查看普通 worker 时；kai/keep 不关联 PID）
    if should_stop_worker.is_set() and worker_name.lower() not in ("kai", "keep"):
        print(f"\n\n🛑 正在停止 worker '{worker_name}' (PID={pid})...")
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
            
            # 更新 worker 状态
            update_worker_status(worker_name, "idle", pid=None)
            print(f"   ✅ Worker '{worker_name}' 已停止")
        except Exception as e:
            print(f"   ⚠️  停止 worker 时出错: {e}")
    else:
        if worker_name.lower() == "kai":
            print(f"\n\n👋 退出查看模式")
        else:
            print(f"\n\n👋 退出查看模式（worker '{worker_name}' 继续运行）")


def cmd_clean_logs(args):
    """清空 logs/ 目录下的日志文件"""
    removed = 0
    if cfg.LOGS_DIR.exists():
        for f in cfg.LOGS_DIR.iterdir():
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
    """设定或查看工作区目录"""
    name = _cli_name()

    if args.path is None:
        saved = get_base_dir()
        print(f"\n📁 {name} 工作区配置")
        if saved:
            print(f"   已设定: {saved}")
            p = Path(saved)
            print(f"   状态:   {'✅ 目录存在' if p.exists() else '❌ 目录不存在'}")
        else:
            print(f"   未设定 (使用当前目录 CWD)")
        print(f"   当前生效: {cfg.BASE_DIR}")
        print(f"\n   用法:")
        print(f"     {name} base .           设为当前目录")
        print(f"     {name} base /path/to    设为指定路径")
        print(f"     {name} base --clear     清除设定 (回到使用 CWD)")
        return

    if args.path == "--clear":
        set_base_dir("")
        print(f"   ✅ 已清除工作区设定，将使用当前目录 (CWD)")
        return

    new_path = Path(args.path).resolve()
    set_base_dir(str(new_path))
    print(f"\n   ✅ 工作区已设定: {new_path}")

    cfg.apply_base_dir(new_path)
    cfg.ensure_dirs()
    print(f"   📂 已创建目录结构 (tasks/, ongoing/, report/, skills/ ...)")
    print(f"\n   之后无论在哪里运行 {name}，都会操作这个目录。")
    print(f"   如需清除: {name} base --clear")


# ============================================================
#  name 命令 — 改名
# ============================================================

def cmd_model(args):
    """设置或查看默认模型"""
    from secretary.settings import get_model, set_model
    
    if args.model_name:
        # 设置模型
        set_model(args.model_name)
        print(f"✅ 已设置默认模型: {args.model_name}")
        print(f"   当前配置: {get_model()}")
    else:
        # 查看当前模型
        current = get_model()
        env_model = os.environ.get("CURSOR_MODEL")
        if env_model:
            print(f"📊 当前模型设置:")
            print(f"   配置文件: {current}")
            print(f"   环境变量 (CURSOR_MODEL): {env_model} (优先)")
            print(f"   实际使用: {env_model}")
        else:
            print(f"📊 当前模型: {current}")
            print(f"   使用 `{_cli_name()} model <模型名>` 来修改")
            print(f"   例如: {_cli_name()} model Auto")


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
    """设置或查看模型"""
    from secretary.settings import get_model, set_model
    name = _cli_name()
    
    if args.model_name is None:
        # 查看当前模型
        current_model = get_model()
        print(f"\n🤖 {name} 模型配置")
        print(f"   当前模型: {current_model}")
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
    """设定、列出或清空秘书的全局目标 (kai target / kai target --clear / kai target 任务1 任务2)"""
    from secretary.secretary_agent import get_goals, set_goals, clear_goals

    if getattr(args, "clear", False):
        clear_goals()
        print(f"\n🧹 已清空当前全局目标")
        print(f"   秘书后续做任务归类与分配时不再带有全局目标上下文")
        return

    goals_list = getattr(args, "goals", None) or []
    if not goals_list:
        goals = get_goals()
        name = _cli_name()
        if not goals:
            print(f"\n🎯 当前全局目标: (无)")
            print(f"   用法: {name} target 任务1 任务2  ...  设定目标")
            print(f"        {name} target --clear           清空目标")
            return
        print(f"\n🎯 当前全局目标 ({len(goals)} 个):\n")
        for i, g in enumerate(goals, 1):
            print(f"   {i}. {g}")
        print(f"\n   清空: {name} target --clear")
        return

    set_goals(goals_list)
    print(f"\n🎯 已设定全局目标 ({len(goals_list)} 个):")
    for i, g in enumerate(goals_list, 1):
        print(f"   {i}. {g}")
    print(f"\n   秘书在 kai task ... 时会看到这些目标并与之对齐。")


def cmd_report(args):
    """查看任务报告：worker report 或 all report"""
    worker_name = args.worker_name
    
    if not worker_name:
        print("❌ 请指定 worker 名称或 'all'")
        print("   用法: kai report alice   (查看 alice 的交互式报告)")
        print("         kai report all     (查看所有任务报告)")
        return
    
    if worker_name.lower() == "all":
        _print_all_reports()
    else:
        # 交互式报告界面
        from secretary.report_viewer import run_interactive_report
        run_interactive_report(worker_name)


def _print_worker_report(worker_name: str):
    """打印指定 worker 的任务报告"""
    from secretary.agents import list_workers, _worker_tasks_dir, _worker_ongoing_dir, get_worker
    
    # 检查 worker 是否存在
    worker_info = get_worker(worker_name)
    if not worker_info:
        print(f"❌ Worker '{worker_name}' 不存在")
        print(f"   使用 `{_cli_name()} workers` 查看所有 worker")
        return
    
    print(f"\n📋 {worker_name} 的任务报告")
    print(f"{'='*60}\n")
    
    # 1. 待处理任务
    tasks_dir = _worker_tasks_dir(worker_name)
    pending_tasks = sorted(tasks_dir.glob("*.md"), key=lambda p: p.stat().st_mtime) if tasks_dir.exists() else []
    
    print(f"📂 待处理任务 ({len(pending_tasks)} 个):")
    if pending_tasks:
        for task_file in pending_tasks:
            mtime = datetime.fromtimestamp(task_file.stat().st_mtime).strftime("%m-%d %H:%M")
            try:
                content = task_file.read_text(encoding="utf-8")
                # 提取任务标题（第一行或 # 标题）
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
                print(f"   • [{mtime}] {task_file.name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{mtime}] {task_file.name}")
    else:
        print("   (无)")
    
    # 2. 执行中任务
    ongoing_dir = _worker_ongoing_dir(worker_name)
    ongoing_tasks = sorted(ongoing_dir.glob("*.md"), key=lambda p: p.stat().st_mtime) if ongoing_dir.exists() else []
    
    print(f"\n⚙️  执行中任务 ({len(ongoing_tasks)} 个):")
    if ongoing_tasks:
        for task_file in ongoing_tasks:
            mtime = datetime.fromtimestamp(task_file.stat().st_mtime).strftime("%m-%d %H:%M")
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
                print(f"   • [{mtime}] {task_file.name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{mtime}] {task_file.name}")
    else:
        print("   (无)")
    
    # 3. 已完成报告（report/ 目录）
    reports = sorted(
        [r for r in cfg.REPORT_DIR.glob("*-report.md")],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    
    print(f"\n✅ 已完成报告 ({len(reports)} 个):")
    if reports:
        for report_file in reports[:10]:  # 只显示最近10个
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime).strftime("%m-%d %H:%M")
            task_name = report_file.stem.replace("-report", "")
            print(f"   • [{mtime}] {task_name}")
    else:
        print("   (无)")
    
    # 4. 统计信息
    print(f"\n📊 统计:")
    print(f"   - 已完成: {worker_info.get('completed_tasks', 0)} 个任务")
    print(f"   - 待处理: {len(pending_tasks)} 个")
    print(f"   - 执行中: {len(ongoing_tasks)} 个")
    
    print(f"\n{'='*60}\n")


def _print_all_reports():
    """打印所有任务的状态报告"""
    from secretary.agents import list_workers, _worker_tasks_dir, _worker_ongoing_dir
    
    print(f"\n📋 所有任务报告")
    print(f"{'='*60}\n")
    
    workers = list_workers()
    
    # 收集所有任务
    all_pending = []  # [(worker_name, task_file), ...]
    all_ongoing = []  # [(worker_name, task_file), ...]
    
    for w in workers:
        worker_name = w["name"]
        tasks_dir = _worker_tasks_dir(worker_name)
        ongoing_dir = _worker_ongoing_dir(worker_name)
        
        if tasks_dir.exists():
            for f in tasks_dir.glob("*.md"):
                all_pending.append((worker_name, f))
        
        if ongoing_dir.exists():
            for f in ongoing_dir.glob("*.md"):
                all_ongoing.append((worker_name, f))
    
    # 1. 待处理任务
    print(f"📂 待处理任务 (共 {len(all_pending)} 个):")
    if all_pending:
        for worker_name, task_file in sorted(all_pending, key=lambda x: x[1].stat().st_mtime):
            mtime = datetime.fromtimestamp(task_file.stat().st_mtime).strftime("%m-%d %H:%M")
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
                print(f"   • [{worker_name}] [{mtime}] {task_file.name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{worker_name}] [{mtime}] {task_file.name}")
    else:
        print("   (无)")
    
    # 2. 执行中任务
    print(f"\n⚙️  执行中任务 (共 {len(all_ongoing)} 个):")
    if all_ongoing:
        for worker_name, task_file in sorted(all_ongoing, key=lambda x: x[1].stat().st_mtime):
            mtime = datetime.fromtimestamp(task_file.stat().st_mtime).strftime("%m-%d %H:%M")
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
                print(f"   • [{worker_name}] [{mtime}] {task_file.name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{worker_name}] [{mtime}] {task_file.name}")
    else:
        print("   (无)")
    
    # 3. 已解决任务（solved-report/）
    solved_reports = sorted(
        cfg.SOLVED_DIR.glob("*-report.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ) if cfg.SOLVED_DIR.exists() else []
    
    print(f"\n✅ 已解决任务 (共 {len(solved_reports)} 个):")
    if solved_reports:
        for report_file in solved_reports[:20]:  # 显示最近20个
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime).strftime("%m-%d %H:%M")
            task_name = report_file.stem.replace("-report", "")
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
                print(f"   • [{mtime}] {task_name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{mtime}] {task_name}")
    else:
        print("   (无)")
    
    # 4. 未解决任务（unsolved-report/）
    unsolved_reports = sorted(
        cfg.UNSOLVED_DIR.glob("*-report.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ) if cfg.UNSOLVED_DIR.exists() else []
    
    print(f"\n❌ 未解决任务 (共 {len(unsolved_reports)} 个):")
    if unsolved_reports:
        for report_file in unsolved_reports[:20]:  # 显示最近20个
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime).strftime("%m-%d %H:%M")
            task_name = report_file.stem.replace("-report", "")
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
                print(f"   • [{mtime}] {task_name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
                
                # 尝试读取未解决原因
                reason_file = cfg.UNSOLVED_DIR / f"{task_name}-unsolved-reason.md"
                if reason_file.exists():
                    try:
                        reason = reason_file.read_text(encoding="utf-8").strip().splitlines()
                        if reason:
                            print(f"     原因: {reason[0][:60]}{'...' if len(reason[0]) > 60 else ''}")
                    except Exception:
                        pass
            except Exception:
                print(f"   • [{mtime}] {task_name}")
    else:
        print("   (无)")
    
    # 5. 待审查报告（report/）
    pending_reports = sorted(
        cfg.REPORT_DIR.glob("*-report.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    ) if cfg.REPORT_DIR.exists() else []
    
    print(f"\n📄 待审查报告 (共 {len(pending_reports)} 个):")
    if pending_reports:
        for report_file in pending_reports[:10]:  # 显示最近10个
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime).strftime("%m-%d %H:%M")
            task_name = report_file.stem.replace("-report", "")
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
                print(f"   • [{mtime}] {task_name}")
                print(f"     {title[:80]}{'...' if len(title) > 80 else ''}")
            except Exception:
                print(f"   • [{mtime}] {task_name}")
    else:
        print("   (无)")
    
    # 6. 统计汇总
    print(f"\n📊 统计汇总:")
    print(f"   - 待处理: {len(all_pending)} 个")
    print(f"   - 执行中: {len(all_ongoing)} 个")
    print(f"   - 已解决: {len(solved_reports)} 个")
    print(f"   - 未解决: {len(unsolved_reports)} 个")
    print(f"   - 待审查: {len(pending_reports)} 个")
    print(f"   - 总任务数: {len(all_pending) + len(all_ongoing) + len(solved_reports) + len(unsolved_reports)} 个")
    
    print(f"\n{'='*60}\n")


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
📝 任务提交命令

用法:
  {name} task "任务描述"
  {name} task "任务描述" --time 120
  {name} task "任务描述" --worker sen

参数:
  request          任务描述 (必需)
  --time, -t       最低执行时间(秒), Agent提前完成也会被要求继续完善
  --worker         直接分配给指定的agent,跳过秘书判断

说明:
  如果不指定worker,任务会写入 agents/kai/tasks/ 目录,由kai的扫描器处理。
  确保kai的扫描器正在运行 (`{name} start kai`),否则任务不会被处理。
  使用 `{name} check kai` 查看kai的处理日志。

示例:
  {name} task "实现一个HTTP服务器"
  {name} task "优化性能" --time 120
  {name} task "修复bug" --worker sen
""",
            "keep": f"""
🔄 持续监控模式

用法:
  {name} keep "持续目标"
  {name} keep "持续目标" --worker sen

说明:
  为指定worker持续生成任务以推进目标。当任务队列为空时,自动生成新任务。
  后台执行,输出写入worker的scanner.log。使用 `{name} check <worker_name>` 查看输出。

参数:
  goal             持续目标描述 (必需)
  --worker         指定的worker名称,默认为默认worker

示例:
  {name} keep "开发一个完整的Web应用" --worker sen
""",
            "use": f"""
🎯 使用技能

用法:
  {name} use <技能名>
  {name} use <技能名> --time 120
  {name} use evolving

说明:
  使用已学会的技能,直接写入worker的tasks目录,跳过秘书判断。

参数:
  skill_name       技能名称 (必需)
  --time           最低执行时间(秒)

示例:
  {name} use evolving
  {name} use analysis --time 60
""",
            "learn": f"""
📖 学习新技能

用法:
  {name} learn "任务描述" <技能名>

说明:
  学习一个新技能,保存为可复用的任务模板。

参数:
  description      任务描述 (必需)
  skill_name       技能名称 (必需)

示例:
  {name} learn "分析代码性能瓶颈" performance-analysis
  {name} learn "重构代码结构" refactor
""",
            "forget": f"""
🧹 忘记技能

用法:
  {name} forget <技能名>

说明:
  删除一个已学会的技能。

参数:
  skill_name       技能名称 (必需)

示例:
  {name} forget my-skill
""",
            "skills": f"""
📚 列出所有技能

用法:
  {name} skills

说明:
  显示所有已学会的技能,包括内置技能和自定义技能。

内置技能:
  - evolving: 代码演进
  - analysis: 代码分析
  - debug: 调试
""",
            "hire": f"""
👷 招募工作者

用法:
  {name} hire
  {name} hire <名字>
  {name} hire alice -d "负责前端开发"

说明:
  招募一个worker(只注册,不启动)。不指定名字则随机生成。

参数:
  worker_name      工人名称 (可选)
  -d, --description 工人描述

示例:
  {name} hire
  {name} hire alice
  {name} hire bob -d "后端开发专家"
""",
            "start": f"""
🚀 启动worker扫描器

用法:
  {name} start [worker_name]
  {name} start sen
  {name} start sen --once
  {name} start sen -q

说明:
  启动worker的扫描器,开始处理任务队列。

参数:
  worker_name      工人名称 (可选,默认为sen)
  --once           只执行一次扫描

说明:
  后台执行,输出写入workers/<worker_name>/logs/scanner.log。
  使用 `{name} check <worker_name>` 查看输出。

示例:
  {name} start sen
  {name} start alice --once
""",
            "fire": f"""
🔥 解雇工人

用法:
  {name} fire <worker_name>

说明:
  解雇(删除)一个worker及其所有数据。

参数:
  worker_name      要解雇的工人名称 (必需)

示例:
  {name} fire alice
""",
            "workers": f"""
👷 列出所有工人

用法:
  {name} workers

说明:
  显示所有已招募的worker及其状态、任务统计等信息。
""",
            "recycle": f"""
♻️ 启动回收者

用法:
  {name} recycle
  {name} recycle --once
  {name} recycle -q

说明:
  启动回收者,定期审查report/目录中的报告,决定任务是否完成。
  后台执行,不保留日志。

参数:
  --once           只执行一次
""",
            "monitor": f"""
📺 实时监控面板

用法:
  {name} monitor
  {name} monitor -i 5
  {name} monitor --text
  {name} monitor --once

说明:
  启动实时监控面板(TUI),显示系统状态、任务队列等信息。
  --text / --once 时输出与旧 status 等价的文本状态后退出；
  无 TUI 环境时自动退化为文本输出。

参数:
  -i, --interval   刷新间隔(秒),默认2秒
  --text           输出文本状态后退出
  --once           输出一次文本快照后退出

示例:
  {name} monitor
  {name} monitor -i 5
  {name} monitor --text
""",
            "stop": f"""
🛑 停止worker进程

用法:
  {name} stop <worker_name>

说明:
  停止指定worker的扫描进程。

参数:
  worker_name      要停止的worker名称 (必需)

示例:
  {name} stop sen
  {name} stop alice
""",
            "check": f"""
📺 实时查看 worker / kai / keep 的日志

用法:
  {name} check <worker_name>
  {name} check kai
  {name} check keep
  {name} check <worker_name> --tail 50

说明:
  实时 tail 后台进程的日志。kai = agents/kai/logs/scanner.log；keep = agents/kai/logs/keep.log；worker = agents/<name>/logs/scanner.log。

参数:
  worker_name      worker 名、kai 或 keep (必需)
  --tail           只显示最后 N 行

操作:
  - 按 'q' 退出查看（不打断进程）
  - 按 Ctrl+C 退出；仅当查看普通 worker 时会同时停止该 worker

示例:
  {name} check sen
  {name} check kai
  {name} check keep
  {name} check ykc --tail 100
""",
            "clean-logs": f"""
🧹 清理日志文件

用法:
  {name} clean-logs

说明:
  清空logs/目录下的所有日志文件。
""",
            "clean-processes": f"""
🧹 清理泄露的进程记录

用法:
  {name} clean-processes

说明:
  检查并清理无效的worker进程PID记录。
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
🎯 设定/列出/清空全局目标

用法:
  {name} target
  {name} target 任务1 任务2
  {name} target --clear

说明:
  设定秘书的全局目标。秘书在处理任务时会参考这些目标进行归类与分配。

参数:
  goals            任务描述列表 (可选)
  --clear          清空当前全局目标

示例:
  {name} target "完成登录模块" "优化性能"
  {name} target --clear
  {name} target
""",
            "report": f"""
📋 查看任务报告

用法:
  {name} report <worker_name>
  {name} report all

说明:
  查看指定worker的任务报告,或查看所有任务报告。

参数:
  worker_name      worker名称或'all' (必需)

示例:
  {name} report sen
  {name} report alice
  {name} report all
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
    
    # 显示通用帮助信息 (根据 language 输出中/英)
    from secretary.i18n import t
    print(f"""
{name} — {t('help_banner')}
{'='*60}

📖 {t('help_quick_start')}:
  1. {t('help_set_workspace')}:     {name} base .
  2. {t('help_submit_task')}:       {name} task "你的任务描述"
  3. {t('help_start_worker')}:     {name} start sen
  4. {t('help_view_status')}:       {name} monitor

{'='*60}
📋 {t('help_command_list')}:
""")
    
    _print_command_list(name)
    
    print(f"""
{'='*60}
💡 {t('help_tips')}:
  • 使用 '{name} help <命令名>' 查看特定命令的详细帮助
  • 使用 '{name} <命令名> --help' 查看命令参数帮助
  • 不输入任何命令进入交互模式
  • 在交互模式下输入 'exit' 退出

📚 {t('help_more')}:
  • 任务流程: task → 秘书分配 → worker处理 → report
  • 技能系统: 使用 learn 学习可复用任务模板
  • Worker管理: hire → start → (处理任务) → fire
  • 监控工具: monitor (TUI 或 kai monitor --text 文本快照)
""")

def _print_command_list(name: str):
    """打印命令列表"""
    commands = [
        ("📝 任务相关", [
            ("task", "提交任务 (经秘书Agent分类)"),
            ("keep", "持续监控模式,自动生成任务推进目标"),
        ]),
        ("📚 技能相关", [
            ("skills", "列出所有已学技能"),
            ("learn", "学习新技能"),
            ("forget", "忘掉一个技能"),
            ("use", "使用技能 (直接写入tasks/)"),
        ]),
        ("👷 Worker管理", [
            ("hire", "招募worker (只注册,不启动)"),
            ("start", "启动worker扫描器"),
            ("fire", "解雇worker"),
            ("workers", "列出所有worker"),
            ("stop", "停止worker进程"),
            ("check", "实时查看worker输出"),
        ]),
        ("♻️ 后台服务", [
            ("recycle", "启动回收者 (审查报告)"),
            ("monitor", "实时监控面板 (TUI)；--text/--once 文本快照"),
        ]),
        ("📊 状态与报告", [
            ("report", "查看任务报告"),
        ]),
        ("⚙️ 设置", [
            ("base", "设定/查看工作区目录"),
            ("name", "给CLI命令改名"),
            ("model", "设置或查看AI模型"),
            ("target", "设定/列出/清空全局目标"),
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
        print(f"\n{category}:")
        for cmd, desc in cmds:
            # 计算合适的对齐宽度
            cmd_width = max(len(cmd) for _, _ in cmds) + 2
            print(f"  {name} {cmd:<{cmd_width}} - {desc}")


# ============================================================
#  交互模式
# ============================================================

def _run_interactive_loop(parser, initial_args, handlers, skill_names):
    """无子命令时进入：支持短命令 task/stop/status、exit、monitor。"""
    if initial_args.workspace:
        ws = Path(initial_args.workspace).resolve()
        cfg.apply_base_dir(ws)

    name = _cli_name()
    prompt = f"{name}> "

    # 打印欢迎信息 + 首次状态栏
    print(f"\n🔄 {name} 交互模式 — 输入子命令，exit 退出，monitor 监控面板")
    try:
        from secretary.dashboard import print_status_line
        cfg.ensure_dirs()
        print_status_line()
    except Exception:
        pass
    
    # 自动启动功能已关闭
    # 如需启动 worker，请手动运行: kai start sen
    
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
            print()
            break
        if not line:
            continue
        if line.lower() == "exit":
            print(f"👋 退出 {name}\n")
            break
        if line.lower() == "bar":
            try:
                from secretary.dashboard import print_status_line
                print_status_line()
            except Exception as e:
                print(f"   ⚠️ {e}")
            continue

        parts = shlex.split(line)
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

    parser = argparse.ArgumentParser(
        prog=name,
        description=f"{name} — 基于 Agent 的自动化任务系统",
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
  {name} start sen                  🚀 启动 sen agent 的扫描器
  {name} start alice                🚀 启动 alice agent 的扫描器
  {name} start kai                  🤖 启动 kai 的扫描器 (处理 agents/kai/tasks/ 中的任务)
  {name} fire alice                 🔥 解雇 alice
  {name} workers                    📋 列出所有工人

技能:
  {name} skills                     📚 列出所有技能
  {name} <技能名>                   🎯 使用技能 (直接写入 tasks/)
  {name} learn "描述" my-skill      📖 学习新技能
  {name} forget my-skill            🧹 忘掉技能

内置技能: evolving | analysis | debug

后台:
  {name} hire [名字]                👷 招募工作者 (只注册)
  {name} start [名字]               🚀 启动 agent 扫描器 (开始处理任务); 使用 'kai' 启动 kai 的扫描器
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
  {name} target 任务1 任务2         🎯 设定秘书全局目标
  {name} target --clear             🎯 清空全局目标
  {name} target                     🎯 列出当前全局目标

监控与控制:
  {name} monitor                    📺 实时监控面板 (TUI)
  {name} monitor --text             📊 查看系统状态 (文本快照)
  {name} monitor -i 5               📺 监控面板，每 5 秒刷新
  {name} stop <worker>               🛑 停止指定 worker 的进程
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
    p = subparsers.add_parser("task", help="提交自定义任务 (经秘书Agent分类)")
    p.add_argument("request", nargs="+", help="任务描述")
    p.add_argument("--time", type=int, default=0, help=time_help)
    p.add_argument("--worker", type=str, default=None, help="直接分配给指定的 worker，跳过秘书判断")
    
    # ---- keep ----
    p = subparsers.add_parser("keep", help="🔄 持续监控模式：为指定 worker 持续生成任务以推进目标")
    p.add_argument("goal", nargs="+", help="持续目标描述")
    p.add_argument("--worker", type=str, default=None, help="指定的 worker 名称，默认为默认 worker")

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
    p = subparsers.add_parser("hire", help="👷 招募工作者 (只注册，不启动)")
    p.add_argument("worker_names", nargs="*", default=None,
                   help="工人名，可多个 (如 alice bob); 不填则随机取名一个")
    p.add_argument("-d", "--description", type=str, default="", help="工人描述")

    # ---- start ----
    p = subparsers.add_parser("start", help="🚀 启动 agent 扫描器 (开始处理任务)")
    p.add_argument("worker_names", nargs="*", default=None,
                   help="Agent名，可多个 (如 alice bob kai); 不填则启动默认 agent (sen)")
    p.add_argument("--once", action="store_true", help="只执行一次")

    # ---- fire ----
    p = subparsers.add_parser("fire", help="🔥 解雇一个或多个工人")
    p.add_argument("worker_names", nargs="+", help="要解雇的工人名，可多个 (如 alice bob)")

    # ---- workers ----
    subparsers.add_parser("workers", help="👷 列出所有已招募的工人")

    # ---- recycle ----
    p = subparsers.add_parser("recycle", help="♻️ 启动回收者")
    p.add_argument("--once", action="store_true", help="只执行一次")

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
    p = subparsers.add_parser("monitor", help="📺 实时监控面板 (TUI)；--text/--once 输出文本状态")
    p.add_argument("-i", "--interval", type=float, default=2.0,
                   help="刷新间隔(秒), 默认 2s")
    p.add_argument("--text", action="store_true", help="输出文本状态后退出（与旧 status 等价）")
    p.add_argument("--once", action="store_true", help="输出一次文本快照后退出")

    # ---- target ----
    p = subparsers.add_parser("target", help="🎯 设定/列出/清空秘书全局目标")
    p.add_argument("goals", nargs="*", help="任务描述 (如: 完成登录模块 优化性能)")
    p.add_argument("--clear", action="store_true", help="清空当前全局目标")

    # ---- report ----
    p = subparsers.add_parser("report", help="📋 查看任务报告 (worker report 或 all report)")
    p.add_argument("worker_name", nargs="?", default=None,
                   help="工人名 (如 alice) 或 'all' 查看所有任务")

    # ---- help ----
    p = subparsers.add_parser("help", help="❓ 显示帮助信息")
    p.add_argument("command_name", nargs="?", default=None,
                   help="命令名称 (可选,显示特定命令的详细帮助)")

    # ---- stop / check / clean-logs / clean-processes ----
    p = subparsers.add_parser("stop", help="🛑 停止指定 worker 的进程")
    p.add_argument("worker_names", nargs="+", help="要停止的 worker 名称，可多个 (如 sen bob)")
    p = subparsers.add_parser("check", help="📺 实时查看 worker/kai/keep 的日志输出")
    p.add_argument("worker_name", help="worker 名 (如 sen)、kai（scanner 日志）或 keep（keep 模式日志）")
    p.add_argument("--tail", type=int, default=0, help="只显示最后 N 行（默认显示所有内容）")
    subparsers.add_parser("clean-logs", help="🧹 清理 logs/ 下的日志文件")
    subparsers.add_parser("clean-processes", help="🧹 清理泄露的 worker 进程记录")

    handlers = {
        "task": cmd_task,
        "keep": cmd_keep,
        "use": cmd_use_skill,
        "learn": cmd_learn,
        "forget": cmd_forget,
        "skills": cmd_skills,
        "hire": cmd_hire,
        "start": cmd_start,
        "fire": cmd_fire,
        "workers": cmd_workers,
        "recycle": cmd_recycle,
        "monitor": cmd_monitor,
        "stop": cmd_stop,
        "check": cmd_check,
        "clean-logs": cmd_clean_logs,
        "clean-processes": cmd_clean_processes,
        "base": cmd_base,
        "name": cmd_name,
        "model": cmd_model,
        "target": cmd_target,
        "report": cmd_report,
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
        cfg.apply_base_dir(ws)

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
    main()

#!/usr/bin/env python3
"""
Secretary Agent System — CLI 入口 (小名: kai)

用法:
  kai task "实现一个HTTP服务器"
  kai evolving / analysis / debug        (内置技能)
  kai learn "任务描述" skill-name         (学技能)
  kai <skill-name>                       (使用技能)
  kai forget <skill-name>                (忘技能)
  kai skills                             (列出所有技能)
  kai hire / recycle                     (后台服务)
  kai status / stop / clean-logs
  kai base ./          设定工作区为当前目录
  kai name lily        给我改个名字叫 lily
"""
import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.settings import (
    get_cli_name, set_cli_name, get_base_dir, set_base_dir,
    load_settings,
)


def _cli_name() -> str:
    """获取当前 CLI 命令名 (用于帮助文本)"""
    return get_cli_name()


# ============================================================
#  任务提交
# ============================================================

def _submit_task(request: str, quiet: bool = False, min_time: int = 0):
    """公用: 通过秘书Agent提交任务，可选嵌入最低执行时间元数据"""
    from secretary.secretary_agent import run_secretary

    if not request.strip():
        print("❌ 请提供任务描述")
        sys.exit(1)

    before = {f.name: f.stat().st_mtime for f in cfg.TASKS_DIR.glob("*.md")} if cfg.TASKS_DIR.exists() else {}

    print(f"\n📨 提交任务: {request}")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    print()

    success = run_secretary(request, verbose=not quiet)
    if not success:
        sys.exit(1)

    effective_min_time = min_time or cfg.DEFAULT_MIN_TIME
    if effective_min_time > 0:
        after = {f.name: f.stat().st_mtime for f in cfg.TASKS_DIR.glob("*.md")} if cfg.TASKS_DIR.exists() else {}
        new_or_changed = [
            cfg.TASKS_DIR / name for name, mtime in after.items()
            if name not in before or mtime != before[name]
        ]
        for tf in new_or_changed:
            content = tf.read_text(encoding="utf-8")
            if "<!-- min_time:" not in content:
                tf.write_text(content.rstrip() + f"\n\n<!-- min_time: {effective_min_time} -->\n",
                              encoding="utf-8")
                if not quiet:
                    print(f"   ⏱️ 已嵌入 min_time={effective_min_time}s → {tf.name}")


def cmd_task(args):
    request = " ".join(args.request)
    _submit_task(request, quiet=args.quiet, min_time=args.time)


# ============================================================
#  技能系统
# ============================================================

def cmd_use_skill(args):
    """使用一个已学会的技能 — 直接写入 tasks/ (跳过秘书)"""
    from secretary.skills import invoke_skill, get_skill

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
        print(f"   ✅ 任务已写入: {task_file.name}")
        print(f"   💡 用 `{_cli_name()} hire` 启动工作者来执行")
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
    """招募工作者 (可选指定名字，不指定则随机取名)"""
    from secretary.scanner import run_scanner
    from secretary.workers import pick_random_name

    worker_name = getattr(args, "worker_name", None) or None
    if not worker_name:
        worker_name = pick_random_name()
        print(f"🎲 随机招募: {worker_name}")
    run_scanner(once=args.once, verbose=not args.quiet, worker_name=worker_name)


def cmd_fire(args):
    """解雇 (删除) 一个命名工人"""
    from secretary.workers import get_worker, remove_worker

    worker_name = args.worker_name
    info = get_worker(worker_name)
    if not info:
        print(f"❌ 没有叫 {worker_name} 的工人")
        print(f"   用 `{_cli_name()} workers` 查看所有工人")
        return

    # 检查是否有正在执行的任务
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
    from secretary.workers import list_workers

    workers = list_workers()
    name = _cli_name()

    if not workers:
        print(f"\n👷 还没有招募任何工人")
        print(f"   用 `{name} hire alice` 来招募一个叫 alice 的工人！")
        print(f"   用 `{name} hire` 启动通用工人 (不需要名字)")
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

    print(f"\n   招募: {name} hire <名字>")
    print(f"   解雇: {name} fire <名字>")
    print(f"   通用: {name} hire (无名字, 使用全局 tasks/ 目录)")


def cmd_recycle(args):
    from secretary.recycler import run_recycler
    run_recycler(once=args.once, verbose=not args.quiet)


def cmd_monitor(args):
    """启动实时监控面板"""
    from secretary.dashboard import run_monitor
    run_monitor(refresh_interval=args.interval)


# ============================================================
#  控制命令
# ============================================================

def cmd_stop(args):
    """停止所有后台进程，并清空 tasks/"""
    name = _cli_name()
    print(f"\n🛑 {name} stop...")

    try:
        if sys.platform != "win32":
            for pattern in [
                f"{name} hire", f"{name} recycle",
                "secretary hire", "secretary recycle",
                # 兼容旧版 scan
                f"{name} scan", "secretary scan",
            ]:
                r = subprocess.run(["pkill", "-f", pattern], capture_output=True, timeout=10)
                if r.returncode == 0:
                    print(f"   ✅ 已停止: {pattern}")
        else:
            print("   ℹ️ Windows: 请手动关闭运行中的进程")
    except Exception as e:
        print(f"   ⚠️ 停止进程时出错: {e}")

    removed = 0
    if cfg.TASKS_DIR.exists():
        for f in cfg.TASKS_DIR.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError as e:
                    print(f"   ⚠️ 删除失败 {f.name}: {e}")
    print(f"   📂 已删除 tasks/ 下 {removed} 个任务文件")

    # 清理 ongoing/ 下的 .lock 文件
    lock_removed = 0
    if cfg.ONGOING_DIR.exists():
        for f in cfg.ONGOING_DIR.glob("*.lock"):
            try:
                f.unlink()
                lock_removed += 1
            except OSError:
                pass

    # 清理命名工人目录下的 .lock 文件
    from secretary.workers import list_workers
    for w in list_workers():
        wdir = cfg.BASE_DIR / w["name"] / "ongoing"
        if wdir.exists():
            for f in wdir.glob("*.lock"):
                try:
                    f.unlink()
                    lock_removed += 1
                except OSError:
                    pass

    if lock_removed:
        print(f"   🔓 已清理 {lock_removed} 个过期 .lock 文件")
    print("✅ stop 完成\n")


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
    print(f"         {new_name} status")
    if old_name in ("kai", "secretary"):
        print(f"\n   💡 原来的 `{old_name}` 命令仍然可用")


# ============================================================
#  status 命令
# ============================================================

def cmd_status(args):
    name = _cli_name()
    print(f"\n📊 {name} 系统状态")
    print(f"   工作区: {cfg.BASE_DIR}\n")

    tasks = list(cfg.TASKS_DIR.glob("*.md"))
    print(f"📂 待处理 (tasks/): {len(tasks)} 个")
    for f in tasks:
        print(f"   • {f.name}")

    ongoing = list(cfg.ONGOING_DIR.glob("*.md"))
    print(f"\n⚙️  执行中 (ongoing/): {len(ongoing)} 个")
    for f in ongoing:
        print(f"   • {f.name}")

    reports = sorted(cfg.REPORT_DIR.glob("*-report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    stats_files = list(cfg.STATS_DIR.glob("*-stats.json"))
    stats_names = {f.stem.replace("-stats", "") for f in stats_files}
    print(f"\n📄 待审查 (report/): {len(reports)} 份报告")
    for f in reports[:10]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        task_name = f.stem.replace("-report", "")
        has_stats = "📊" if task_name in stats_names else "  "
        print(f"   {has_stats} [{mtime}] {f.name}")
    if len(reports) > 10:
        print(f"   ... 还有 {len(reports)-10} 个")

    stats_count = len(stats_files)
    print(f"\n📊 统计 (stats/): {stats_count} 份")

    solved = list(cfg.SOLVED_DIR.glob("*-report.md"))
    print(f"\n✅ 已解决 (solved-report/): {len(solved)} 份")
    for f in sorted(solved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
    if len(solved) > 5:
        print(f"   ... 还有 {len(solved)-5} 个")

    unsolved = list(cfg.UNSOLVED_DIR.glob("*-report.md"))
    print(f"\n❌ 未解决 (unsolved-report/): {len(unsolved)} 份")
    for f in sorted(unsolved, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
        print(f"   • [{mtime}] {f.name}")
        reason_file = cfg.UNSOLVED_DIR / f.name.replace("-report.md", "-unsolved-reason.md")
        if reason_file.exists():
            reason = reason_file.read_text(encoding="utf-8").strip().splitlines()
            if reason:
                print(f"     原因: {reason[0][:80]}")

    testcases = [t for t in cfg.TESTCASES_DIR.glob("*") if t.is_file()]
    print(f"\n🧪 测试样例 (testcases/): {len(testcases)} 个")
    for f in testcases[:10]:
        print(f"   • {f.name}")

    # 工人列表
    from secretary.workers import list_workers
    workers = list_workers()
    print(f"\n👷 工人: {len(workers)} 个")
    for w in workers:
        status_icon = {"idle": "💤", "busy": "⚙️", "offline": "📴"}.get(w.get("status", ""), "❓")
        pid_str = f"PID={w['pid']}" if w.get("pid") else ""
        completed = w.get("completed_tasks", 0)
        pending = w.get("pending_count", 0)
        ongoing = w.get("ongoing_count", 0)
        print(f"   {status_icon} {w['name']:15s}  完成:{completed:3d}  待处理:{pending}  执行中:{ongoing}  {pid_str}")

    # 技能列表
    from secretary.skills import list_skills
    skills = list_skills()
    print(f"\n📚 技能 (skills/): {len(skills)} 个")
    for s in skills[:10]:
        tag = "📦" if s["builtin"] else "🎓"
        print(f"   {tag} {s['name']}")
    if len(skills) > 10:
        print(f"   ... 还有 {len(skills)-10} 个")

    # 日志
    logs = list(cfg.LOGS_DIR.glob("*.log")) if cfg.LOGS_DIR.exists() else []
    print(f"\n📋 日志 (logs/): {len(logs)} 个")

    print(f"\n💡 工人:     {name} hire <名字> | {name} fire <名字> | {name} workers")
    print(f"💡 技能:     {name} skills | {name} <技能名> | {name} learn")
    print(f"💡 后台服务: hire (工作者) | recycle (回收者)")
    print(f"💡 设置:     {name} base <路径> | {name} name <新名字>")
    print(f"💡 清理:     {name} clean-logs")


# ============================================================
#  交互模式
# ============================================================

def _wait_bg_procs(bg_procs: list, name: str):
    """等待所有后台子进程结束 (最多 60s/进程)，并关闭日志文件句柄。"""
    alive = [(p, f, fh) for p, f, fh in bg_procs if p.poll() is None]
    if alive:
        print(f"\n   ⏳ 等待 {len(alive)} 个后台子进程结束...")
        for proc, log_path, fh in alive:
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                print(f"   ⚠️ PID={proc.pid} 超时，强制终止")
                proc.terminate()
            try:
                fh.write(f"\n# finished (rc={proc.returncode}): {datetime.now().isoformat()}\n")
                fh.close()
            except Exception:
                pass
    # 关闭所有已结束但还没清理的文件句柄
    for p, f, fh in bg_procs:
        try:
            if not fh.closed:
                fh.close()
        except Exception:
            pass
    bg_procs.clear()


def _run_interactive_loop(parser, initial_args, handlers, skill_names):
    """无子命令时进入：支持短命令 task/stop/status、exit、quiet、speak、monitor。"""
    if initial_args.workspace:
        ws = Path(initial_args.workspace).resolve()
        cfg.apply_base_dir(ws)

    name = _cli_name()
    quiet = False
    prompt = f"{name}> "
    bg_procs: list = []  # [(Popen, log_path, file_handle), ...]

    # 打印欢迎信息 + 首次状态栏
    print(f"\n🔄 {name} 交互模式 — 输入子命令，exit 退出，monitor 监控面板")
    try:
        from secretary.dashboard import print_status_line
        cfg.ensure_dirs()
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
            _wait_bg_procs(bg_procs, name)
            print()
            break
        if not line:
            continue
        if line.lower() == "exit":
            _wait_bg_procs(bg_procs, name)
            print(f"👋 退出 {name}\n")
            break
        if line.lower() == "quiet":
            quiet = True
            print(f"   🔇 quiet: 任务将后台执行，输出写入 logs/ 目录")
            continue
        if line.lower() == "speak":
            quiet = False
            print("   🔊 speak: 任务在前台执行，执行完再接收下一条命令")
            continue
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

        # 检测是否是技能名 (不在 handlers 里的单词)
        # 如果第一个 token 是已知技能，则包装成 use <skill> 命令
        first = parts[0]
        if first not in handlers and first in skill_names:
            parts = ["use", first] + parts[1:]

        try:
            args = parser.parse_args(parts)
        except SystemExit:
            print("   ❓ 未知命令或参数错误，请重试")
            continue
        if not getattr(args, "command", None):
            print("   ❓ 请输入子命令，如 task / stop / status / skills")
            continue

        # base / name 不需要 ensure_dirs
        if args.command in ("base", "name"):
            handlers[args.command](args)
            continue

        cfg.ensure_dirs()

        # 刷新可用技能列表 (用户可能刚 learn 了新技能)
        _refresh_skill_names(skill_names)

        # 仅 task / hire / recycle 进入执行流程（可被后台调度）
        can_execute_in_background = args.command in cfg.EXECUTABLE_COMMANDS

        if quiet and can_execute_in_background:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            log_file = cfg.LOGS_DIR / f"{args.command}-{ts}.log"

            sub_cmd = [sys.executable, "-m", "secretary.cli"]
            if initial_args.workspace:
                sub_cmd += ["-w", str(Path(initial_args.workspace).resolve())]
            sub_cmd += parts

            lf = open(log_file, "w", encoding="utf-8")
            lf.write(f"# {name} quiet-mode log\n")
            lf.write(f"# command: {' '.join(parts)}\n")
            lf.write(f"# subprocess: {' '.join(sub_cmd)}\n")
            lf.write(f"# started: {datetime.now().isoformat()}\n\n")
            lf.flush()

            proc = subprocess.Popen(
                sub_cmd,
                stdout=lf,
                stderr=lf,
                cwd=str(cfg.BASE_DIR),
            )
            bg_procs.append((proc, log_file, lf))
            # 清理已结束的进程
            new_bg = []
            for p, f, fh in bg_procs:
                if p.poll() is None:
                    new_bg.append((p, f, fh))
                else:
                    try:
                        fh.write(f"\n# finished (rc={p.returncode}): {datetime.now().isoformat()}\n")
                        fh.close()
                    except Exception:
                        pass
            bg_procs[:] = new_bg
            print(f"   ⏳ 后台子进程 PID={proc.pid}，日志: {log_file.name}")
        elif quiet and not can_execute_in_background:
            print(f"   ℹ️ {args.command} 不在可后台执行范围内，改为前台运行")
            try:
                handlers[args.command](args)
            except SystemExit as e:
                if e.code and e.code != 0:
                    print(f"   ⚠️ 命令退出码: {e.code}")
        else:
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
        description=f"{name} — 基于 Cursor Agent 的自动化任务系统",
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

工人管理:
  {name} hire                       👷 招募通用工人 (全局 tasks/)
  {name} hire alice                 👷 招募叫 alice 的工人
  {name} fire alice                 🔥 解雇 alice
  {name} workers                    📋 列出所有工人

技能:
  {name} skills                     📚 列出所有技能
  {name} <技能名>                   🎯 使用技能 (直接写入 tasks/)
  {name} learn "描述" my-skill      📖 学习新技能
  {name} forget my-skill            🧹 忘掉技能

内置技能: evolving | analysis | debug

后台:
  {name} hire [名字]                ⚙️ 招募工作者 (扫描执行任务)
  {name} recycle                    ♻️ 启动回收者 (每2分钟审查)
  {name} monitor                    📺 实时监控面板 (TUI)

设置:
  {name} base .                     📁 设定工作区为当前目录
  {name} base /path/to/project      📁 设定工作区为指定路径
  {name} base --clear               📁 清除设定 (使用 CWD)
  {name} name lily                  🏷️  改名叫 lily

监控与控制:
  {name} monitor                    📺 实时监控面板 (全屏 TUI)
  {name} status                     📊 查看系统状态 (文本)
  {name} stop                       🛑 停止所有进程 + 清空 tasks/
  {name} clean-logs                 🧹 清理日志文件
        """,
    )

    # ---- 全局参数 ----
    parser.add_argument(
        "-w", "--workspace",
        type=str, default=None,
        help="临时指定工作区 (不保存，仅本次生效)",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    time_help = "最低执行时间(秒)，Agent 提前完成也会被要求继续完善直到达到此时间"

    # ---- task ----
    p = subparsers.add_parser("task", help="提交自定义任务 (经秘书Agent分类)")
    p.add_argument("request", nargs="+", help="任务描述")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--time", type=int, default=0, help=time_help)

    # ---- use <skill> ----
    p = subparsers.add_parser("use", help="🎯 使用技能 (直接写入 tasks/)")
    p.add_argument("skill_name", help="技能名称")
    p.add_argument("-q", "--quiet", action="store_true")
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

    # ---- hire (原 scan) ----
    p = subparsers.add_parser("hire", help="⚙️ 招募工作者 (扫描并执行任务)")
    p.add_argument("worker_name", nargs="?", default=None,
                   help="工人名 (如 alice); 不填则启动通用工人")
    p.add_argument("--once", action="store_true", help="只执行一次")
    p.add_argument("-q", "--quiet", action="store_true")

    # ---- fire ----
    p = subparsers.add_parser("fire", help="🔥 解雇一个工人")
    p.add_argument("worker_name", help="要解雇的工人名 (如 alice)")

    # ---- workers ----
    subparsers.add_parser("workers", help="👷 列出所有已招募的工人")

    # ---- recycle ----
    p = subparsers.add_parser("recycle", help="♻️ 启动回收者")
    p.add_argument("--once", action="store_true", help="只执行一次")
    p.add_argument("-q", "--quiet", action="store_true")

    # ---- base ----
    p = subparsers.add_parser("base", help="📁 设定/查看工作区目录")
    p.add_argument("path", nargs="?", default=None,
                   help="工作区路径 (. = 当前目录, --clear = 清除)")

    # ---- name ----
    p = subparsers.add_parser("name", help="🏷️ 给我改个名字")
    p.add_argument("new_name", help="新命令名 (如 lily)")

    # ---- monitor ----
    p = subparsers.add_parser("monitor", help="📺 实时监控面板 (TUI)")
    p.add_argument("-i", "--interval", type=float, default=2.0,
                   help="刷新间隔(秒), 默认 2s")

    # ---- status / stop / clean-logs ----
    subparsers.add_parser("status", help="📊 查看系统状态")
    subparsers.add_parser("stop", help="🛑 停止所有进程 + 清空 tasks/")
    subparsers.add_parser("clean-logs", help="🧹 清理 logs/ 下的日志文件")

    handlers = {
        "task": cmd_task,
        "use": cmd_use_skill,
        "learn": cmd_learn,
        "forget": cmd_forget,
        "skills": cmd_skills,
        "hire": cmd_hire,
        "fire": cmd_fire,
        "workers": cmd_workers,
        "recycle": cmd_recycle,
        "monitor": cmd_monitor,
        "status": cmd_status,
        "stop": cmd_stop,
        "clean-logs": cmd_clean_logs,
        "base": cmd_base,
        "name": cmd_name,
    }

    args = parser.parse_args()

    # 无子命令时进入交互模式
    if not args.command:
        skill_names = _get_all_skill_names()
        _run_interactive_loop(parser, args, handlers, skill_names)
        return

    # --workspace 临时覆盖 (不保存)
    if args.workspace:
        ws = Path(args.workspace).resolve()
        cfg.apply_base_dir(ws)

    # base / name 命令不需要 ensure_dirs
    if args.command in ("base", "name"):
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

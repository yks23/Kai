"""
任务扫描器 — 后台主循环 (支持多实例并行 + 命名工人)

执行范围: 仅 execution_scope 为 task / hire / recycle 的任务会被执行；
  monitor 等其它类型不进入执行流程（见 config.EXECUTABLE_TASK_TYPES）。
  任务文件可通过 <!-- execution_scope: monitor --> 等标注类型，未标注时视为 task。

并发模型:
  每个 worker 有独立的目录 (workers/{name}/tasks 和 workers/{name}/ongoing)，
  因此不需要锁机制。每个 worker 的 scanner 只处理自己目录中的任务。

命名工人:
  `kai hire alice` 招募名为 alice 的工人。每个工人有专属目录:
    {BASE_DIR}/workers/alice/tasks/    — 秘书分配给 alice 的任务
    {BASE_DIR}/workers/alice/ongoing/  — alice 正在执行的任务
  报告统一写入 {BASE_DIR}/report/。
  未命名的 `kai hire` 使用默认 worker (sen)。

工作流程:
1. 持续扫描 tasks/ 和 ongoing/ 文件夹 (工人各扫各的目录)
2. 每轮处理一个任务
3. 首轮调用 Worker Agent（完整提示词，新会话）
4. Agent 自然停止后，检查 ongoing/ 中的文件是否还在
5. 文件还在 → 用 --continue 续轮调用（Agent 保持上下文记忆）
6. 文件被 Agent 删除 → 任务完成
7. Scanner 在 stats/ 中写入调用统计
"""
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

import re

import secretary.config as cfg
from secretary.config import EXECUTABLE_TASK_TYPES
from secretary.worker import run_worker_first_round, run_worker_continue, run_worker_refine
from secretary.agent_runner import RoundStats

# 确保输出实时刷新（用于后台运行时日志及时写入）
# 创建一个带自动刷新的 print 函数
_original_print = print
def print(*args, **kwargs):
    """重写 print 函数，默认 flush=True 确保实时输出"""
    if 'flush' not in kwargs:
        kwargs['flush'] = True
    _original_print(*args, **kwargs)

# 当前 scanner 进程 ID
_PID = os.getpid()

# 当前工人名 (None = 使用默认 worker)
_WORKER_NAME: str | None = None


# ============================================================
#  文件锁 — 多进程互斥
# ============================================================

# 锁机制已移除：每个 worker 有独立的目录，不需要锁


def _get_tasks_dir() -> Path:
    """获取当前工人的 tasks 目录"""
    worker_name = _WORKER_NAME or cfg.DEFAULT_WORKER_NAME
    return cfg.WORKERS_DIR / worker_name / "tasks"


def _get_ongoing_dir() -> Path:
    """获取当前工人的 ongoing 目录"""
    worker_name = _WORKER_NAME or cfg.DEFAULT_WORKER_NAME
    return cfg.WORKERS_DIR / worker_name / "ongoing"


# 锁机制已移除，不再需要清理锁文件


# ============================================================
#  统计数据
# ============================================================

@dataclass
class TaskStats:
    """一个任务的完整统计"""
    task_name: str
    start_time: str = ""                # 任务开始时间
    end_time: str = ""                  # 任务结束时间
    wall_clock_ms: int = 0              # 墙钟总用时(含轮间等待)
    total_rounds: int = 0
    total_duration_ms: int = 0          # Agent 进程累计用时
    total_api_duration_ms: int = 0      # API 调用累计用时
    total_tool_calls: int = 0
    all_files_changed: list[str] = field(default_factory=list)
    all_shell_commands: list[str] = field(default_factory=list)
    session_id: str = ""
    model: str = ""
    success: bool = False
    min_time: int = 0                   # 最低执行时间(秒), 0=不限制
    last_response: str = ""             # 最后一轮 Agent 的回复文本
    round_details: list[dict] = field(default_factory=list)
    conversation_log: list[dict] = field(default_factory=list)  # 完整对话日志 (每轮的原始输出)
    worker_pid: int = 0                 # 执行本任务的 scanner PID
    _wall_start: float = 0.0           # 内部: 墙钟起点

    def mark_start(self):
        """记录墙钟开始"""
        self._wall_start = time.time()
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.worker_pid = _PID

    def mark_end(self):
        """记录墙钟结束"""
        self.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self._wall_start > 0:
            self.wall_clock_ms = int((time.time() - self._wall_start) * 1000)

    def add_round(self, round_num: int, stats: RoundStats, success: bool,
                  raw_output: str = "", readable_output: str = ""):
        """合并一轮的统计数据，并保存对话记录"""
        self.total_rounds = round_num
        self.total_duration_ms += stats.duration_ms
        self.total_api_duration_ms += stats.duration_api_ms
        self.total_tool_calls += stats.tool_call_count

        for f in stats.files_changed:
            if f not in self.all_files_changed:
                self.all_files_changed.append(f)

        self.all_shell_commands.extend(stats.shell_commands)

        if stats.session_id:
            self.session_id = stats.session_id
        if stats.model:
            self.model = stats.model

        # 保留每轮最后的 assistant 回复，同时更新任务级别的 last_response
        last_text = stats.last_assistant_text or ""
        if last_text:
            self.last_response = last_text

        self.round_details.append({
            "round": round_num,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": stats.duration_ms,
            "api_duration_ms": stats.duration_api_ms,
            "tool_calls": stats.tool_call_count,
            "files_edited": stats.file_edits[:],
            "files_created": stats.file_creates[:],
            "shell_commands": stats.shell_commands[:],
            "success": success,
            "last_response": last_text,
        })

        # 保存对话日志
        self.conversation_log.append({
            "round": round_num,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "readable_output": readable_output,
            "raw_stream_json": raw_output,
        })

    @property
    def total_duration_sec(self) -> float:
        return self.total_duration_ms / 1000.0

    @property
    def wall_clock_sec(self) -> float:
        return self.wall_clock_ms / 1000.0


# ============================================================
#  统计报告
# ============================================================

def _write_scanner_report(task_stats: TaskStats):
    """
    将 scanner 的调用统计写入 worker 的 stats/ 文件夹

    生成两个文件:
      - {task_name}-stats.md  — 可读的 Markdown 统计报告
      - {task_name}-stats.json — 结构化数据 (数字统计 + 完整对话日志)
    """
    # 获取当前 worker 的 stats 目录
    from secretary.agents import _worker_stats_dir
    worker_name = _WORKER_NAME or cfg.DEFAULT_WORKER_NAME
    stats_dir = _worker_stats_dir(worker_name)
    stats_dir.mkdir(parents=True, exist_ok=True)
    
    # ---- Markdown 统计报告 ----
    md_path = stats_dir / f"{task_stats.task_name}-stats.md"

    lines = [
        f"# 📊 调用统计: {task_stats.task_name}\n",
        f"",
        f"| 项目 | 数据 |",
        f"|------|------|",
        f"| 状态 | {'✅ 完成' if task_stats.success else '❌ 失败'} |",
        f"| 总对话轮数 | {task_stats.total_rounds} 轮 |",
        f"| 墙钟总用时 | {task_stats.wall_clock_sec:.1f}s ({task_stats.wall_clock_ms}ms) |",
        f"| Agent 累计用时 | {task_stats.total_duration_sec:.1f}s ({task_stats.total_duration_ms}ms) |",
        f"| API 累计用时 | {task_stats.total_api_duration_ms}ms |",
        f"| Tool Calls 总数 | {task_stats.total_tool_calls} 次 |",
        f"| 涉及文件数 | {len(task_stats.all_files_changed)} 个 |",
        f"| Shell 命令数 | {len(task_stats.all_shell_commands)} 条 |",
        f"| 模型 | {task_stats.model or 'Auto'} |",
        f"| Session ID | `{task_stats.session_id}` |",
        f"| Worker PID | {task_stats.worker_pid} |",
        f"| 开始时间 | {task_stats.start_time} |",
        f"| 结束时间 | {task_stats.end_time} |",
    ]
    if task_stats.min_time > 0:
        lines.append(f"| 最低执行时间 | {task_stats.min_time}s |")
    lines.append("")

    # 执行者最后反馈
    if task_stats.last_response:
        lines.append("## 执行者最后反馈\n")
        last_resp = task_stats.last_response
        if len(last_resp) > 2000:
            last_resp = last_resp[:2000] + "\n\n... (已截断)"
        lines.append(f"> {last_resp}\n")
        lines.append("")

    # 涉及的文件
    if task_stats.all_files_changed:
        lines.append("## 涉及的文件\n")
        for f in task_stats.all_files_changed:
            lines.append(f"- `{f}`")
        lines.append("")

    # Shell 命令
    if task_stats.all_shell_commands:
        lines.append("## 执行的 Shell 命令\n")
        for cmd in task_stats.all_shell_commands:
            lines.append(f"- `{cmd}`")
        lines.append("")

    # 每轮详情
    lines.append("## 每轮详情\n")
    for rd in task_stats.round_details:
        status = "✅" if rd["success"] else "❌"
        round_type = "首轮 (新会话)" if rd["round"] == 1 else "续轮 (--resume)"
        lines.append(f"### 第 {rd['round']} 轮 {status} — {round_type}\n")
        lines.append(f"- 时间: {rd.get('start_time', 'N/A')}")
        lines.append(f"- 耗时: {rd['duration_ms']}ms (API: {rd['api_duration_ms']}ms)")
        lines.append(f"- Tool Calls: {rd['tool_calls']} 次")
        if rd["files_edited"]:
            lines.append(f"- 编辑文件: {', '.join('`' + f + '`' for f in rd['files_edited'])}")
        if rd["files_created"]:
            lines.append(f"- 创建文件: {', '.join('`' + f + '`' for f in rd['files_created'])}")
        if rd["shell_commands"]:
            lines.append(f"- Shell: {', '.join('`' + c + '`' for c in rd['shell_commands'])}")
        if rd.get("last_response"):
            resp_preview = rd["last_response"]
            if len(resp_preview) > 500:
                resp_preview = resp_preview[:500] + " ..."
            lines.append(f"- 最后反馈: {resp_preview}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    # ---- JSON 统计 + 完整对话日志 ----
    json_path = stats_dir / f"{task_stats.task_name}-stats.json"
    json_data = {
        # ---- 数字化统计 (顶部) ----
        "task_name": task_stats.task_name,
        "success": task_stats.success,
        "total_rounds": task_stats.total_rounds,
        "wall_clock_ms": task_stats.wall_clock_ms,
        "wall_clock_sec": round(task_stats.wall_clock_sec, 1),
        "total_duration_ms": task_stats.total_duration_ms,
        "total_duration_sec": round(task_stats.total_duration_sec, 1),
        "total_api_duration_ms": task_stats.total_api_duration_ms,
        "total_tool_calls": task_stats.total_tool_calls,
        "files_changed_count": len(task_stats.all_files_changed),
        "files_changed": task_stats.all_files_changed,
        "shell_commands_count": len(task_stats.all_shell_commands),
        "shell_commands": task_stats.all_shell_commands,
        "model": task_stats.model,
        "session_id": task_stats.session_id,
        "worker_pid": task_stats.worker_pid,
        "start_time": task_stats.start_time,
        "end_time": task_stats.end_time,
        "min_time": task_stats.min_time,
        "last_response": task_stats.last_response,
        # ---- 每轮统计详情 ----
        "round_details": task_stats.round_details,
        # ---- 完整对话日志 (最底部, 方便 debug) ----
        "conversation_log": task_stats.conversation_log,
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   📊 统计已写入 stats/: {md_path.name} + {json_path.name}")


# ============================================================
#  任务文件解析
# ============================================================

def _get_task_execution_scope(task_file: Path) -> str:
    """
    从任务文件中解析 execution_scope，用于判断是否需被 scanner 执行。
    约定: 文件内容中的 <!-- execution_scope: X -->，X 为 task/hire/recycle/monitor 等。
    若未标注，默认为 "task"（保持与旧任务兼容，会被执行）。
    """
    try:
        content = task_file.read_text(encoding="utf-8")
        m = re.search(r"<!--\s*execution_scope:\s*(\w+)\s*-->", content)
        if m:
            return m.group(1).strip().lower()
    except Exception:
        pass
    return "task"


def _is_executable_task(task_file: Path) -> bool:
    """仅 task、hire、recycle 类型的任务会被执行；monitor 等不进入执行流程。"""
    scope = _get_task_execution_scope(task_file)
    return scope in EXECUTABLE_TASK_TYPES


def scan_new_tasks() -> list[Path]:
    """扫描 tasks/ 中的 .md 文件，仅返回需要执行的任务（execution_scope 为 task/hire/recycle）。"""
    tasks_dir = _get_tasks_dir()
    if not tasks_dir.exists():
        return []
    all_md = list(tasks_dir.glob("*.md"))
    executable = [p for p in all_md if _is_executable_task(p)]
    return sorted(executable, key=lambda p: p.stat().st_mtime)


def move_to_ongoing(task_file: Path) -> Path | None:
    """将任务文件从 tasks/ 移动到 ongoing/，如果文件已不存在则返回 None"""
    ongoing_dir = _get_ongoing_dir()
    ongoing_dir.mkdir(parents=True, exist_ok=True)
    if not task_file.exists():
        print(f"   ⚠️ 文件已不存在，跳过: {task_file.name}")
        return None
    dest = ongoing_dir / task_file.name
    if dest.exists():
        stem = task_file.stem
        suffix = task_file.suffix
        ts = datetime.now().strftime("%H%M%S")
        dest = ongoing_dir / f"{stem}-{ts}{suffix}"
    try:
        shutil.move(str(task_file), str(dest))
    except FileNotFoundError:
        print(f"   ⚠️ 移动时文件消失，跳过: {task_file.name}")
        return None
    return dest


def _parse_min_time(task_file: Path) -> int:
    """从任务文件中解析 <!-- min_time: X --> 元数据，返回秒数 (默认 0)"""
    try:
        content = task_file.read_text(encoding="utf-8")
        m = re.search(r"<!--\s*min_time:\s*(\d+)\s*-->", content)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


# ============================================================
#  单任务处理
# ============================================================

def process_ongoing_task(ongoing_file: Path, verbose: bool = True):
    """
    持续调用 Worker Agent 直到它删除 ongoing/ 中的任务文件
    
    注意：verbose=True 时，所有输出（包括 agent 的对话过程）都会实时输出到 stdout/stderr
    在后台运行时，这些输出会被重定向到日志文件，并实时刷新。
    """
    """
    持续调用 Worker Agent 直到它删除 ongoing/ 中的任务文件

    第1轮: 全新会话 (完整提示词)
    第2轮+: --resume 续轮 (使用 session_id 精确恢复会话，Agent 有上一轮的完整记忆)

    如果任务文件中嵌有 <!-- min_time: X --> 元数据，则即使 Agent 提前完成
    (删除了任务文件)，也会通过 --resume 继续要求完善，直到累计墙钟时间
    达到 min_time 秒。

    完成后写入调用统计报告。
    """
    task_name = ongoing_file.stem
    round_num = 0

    # 解析最低执行时间
    min_time = _parse_min_time(ongoing_file)

    # 初始化统计
    task_stats = TaskStats(task_name=task_name, min_time=min_time)
    task_stats.mark_start()

    print(f"\n{'='*60}")
    print(f"⚙️  开始处理任务: {ongoing_file.name}  (Worker PID={_PID})")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    print(f"{'='*60}")

    task_deleted = False  # Agent 是否已经删除了任务文件

    # 当设定了 min_time 时，每轮至少给予 (剩余时间 + 缓冲) 的 timeout
    def _round_timeout_sec() -> int | None:
        if min_time <= 0:
            return None
        remaining = min_time - int(elapsed)
        if remaining <= 0:
            return None
        return remaining + 120  # 缓冲 120 秒

    try:
        while True:
            round_num += 1
            elapsed = time.time() - task_stats._wall_start
            round_timeout = _round_timeout_sec()

            if task_deleted:
                # === 完善阶段: 任务已完成但 min_time 未到 ===
                if elapsed >= min_time:
                    break
                remaining = min_time - elapsed
                print(f"\n--- 第 {round_num} 轮: 完善阶段 (--resume)"
                      f" | 已用 {elapsed:.0f}s / {min_time}s, 还需 {remaining:.0f}s ---")
                result = run_worker_refine(
                    elapsed_sec=elapsed,
                    min_time=min_time,
                    verbose=verbose,
                    timeout_sec=round_timeout,
                    session_id=task_stats.session_id,  # 使用保存的 session_id
                )
            elif round_num == 1:
                print(f"\n--- 第 1 轮: 首轮调用 (新会话) ---")
                result = run_worker_first_round(ongoing_file, verbose=verbose,
                                                timeout_sec=round_timeout)
            else:
                print(f"\n--- 第 {round_num} 轮: 续轮调用 (--resume {task_stats.session_id[:8] if task_stats.session_id else 'N/A'}...) ---")
                result = run_worker_continue(ongoing_file, verbose=verbose,
                                             timeout_sec=round_timeout,
                                             session_id=task_stats.session_id)  # 使用保存的 session_id

            # 记录本轮统计 + 对话日志
            task_stats.add_round(
                round_num, result.stats, result.success,
                raw_output=result.raw_output,
                readable_output=result.output,
            )

            # 检查: Agent 是否已经删除了任务文件
            if not task_deleted and not ongoing_file.exists():
                task_deleted = True
                elapsed = time.time() - task_stats._wall_start

                if min_time > 0 and elapsed < min_time:
                    remaining = min_time - elapsed
                    print(f"\n📋 任务文件已删除 (Agent认为完成)，但最低时间未到"
                          f" ({elapsed:.0f}s / {min_time}s)")
                    print(f"   ⏱️ 进入完善阶段，还需 {remaining:.0f}s ...")
                    time.sleep(cfg.WORKER_RETRY_INTERVAL)
                    continue
                else:
                    break  # 正常完成 (无 min_time 或已达标)

            if task_deleted:
                # 完善阶段轮结束，检查时间
                elapsed = time.time() - task_stats._wall_start
                if elapsed >= min_time:
                    break
                time.sleep(cfg.WORKER_RETRY_INTERVAL)
                continue

            # Agent 自然停止了但文件还在 → 还没完成，必须进入下一轮
            if result.success:
                print(f"   Agent 本轮正常结束，但任务文件仍存在 → 任务未完成")
            else:
                print(f"   ⚠️ Agent 本轮出错 (code={result.return_code})")
                print(f"   错误信息: {result.output[:200]}")
            if min_time > 0:
                print(f"   ⏱️ 最低执行时间未到 ({elapsed:.0f}s / {min_time}s)，将续轮直至时间用尽或任务完成")

            print(f"   {cfg.WORKER_RETRY_INTERVAL}s 后用 --resume 续轮...")
            time.sleep(cfg.WORKER_RETRY_INTERVAL)

        # 任务完成
        task_stats.success = True
        task_stats.mark_end()

        print(f"\n✅ 任务完成: {task_name}  (Worker PID={_PID})")
        print(f"   共执行 {round_num} 轮"
              f" | 墙钟用时 {task_stats.wall_clock_sec:.1f}s"
              f" | Agent用时 {task_stats.total_duration_sec:.1f}s"
              f" | Tool Calls {task_stats.total_tool_calls} 次"
              f" | 涉及 {len(task_stats.all_files_changed)} 个文件")
        if min_time > 0:
            print(f"   ⏱️ 最低执行时间: {min_time}s (实际: {task_stats.wall_clock_sec:.1f}s)")
        _print_report(task_name)
        _write_scanner_report(task_stats)

    except Exception as e:
        # 即使异常退出，也保存已有的统计数据
        task_stats.mark_end()
        print(f"\n⚠️ 任务 {task_name} 异常退出: {e}")
        print(f"   保存已有统计数据...")
        _write_scanner_report(task_stats)
        raise


def _print_report(task_name: str):
    """打印 Worker 报告文件路径"""
    expected = cfg.REPORT_DIR / f"{task_name}-report.md"
    if expected.exists():
        print(f"   📄 Worker报告: {expected}")
    else:
        reports = sorted(
            [r for r in cfg.REPORT_DIR.glob("*.md") if r.stem.endswith("-report")],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if reports:
            print(f"   📄 最新Worker报告: {reports[0]}")


# ============================================================
#  主扫描循环
# ============================================================

def _pick_one_ongoing() -> Path | None:
    """从 ongoing/ 中找一个可执行的任务"""
    ongoing_dir = _get_ongoing_dir()
    if not ongoing_dir.exists():
        return None
    candidates = [
        f for f in sorted(ongoing_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
        if _is_executable_task(f)
    ]
    return candidates[0] if candidates else None


def _pick_one_new() -> Path | None:
    """从 tasks/ 中找一个可执行的任务，移动到 ongoing/ 并返回路径"""
    new_tasks = scan_new_tasks()
    for task_file in new_tasks:
        ongoing_file = move_to_ongoing(task_file)
        if ongoing_file:
            return ongoing_file
    return None


def run_scanner(once: bool = False, verbose: bool = True, worker_name: str | None = None):
    """
    运行主扫描循环。

    每个 worker 有独立的目录，因此不需要锁机制。
    每个 scanner 只处理自己 worker 目录中的任务。

    - worker_name: 工人名 (如 "alice")。有名字时扫描 {name}/tasks/ 和 {name}/ongoing/;
                   无名字时扫描全局 tasks/ 和 ongoing/。
    - once=False（默认）: 持续运行，每 SCAN_INTERVAL 秒扫描一次。
    - once=True: 只执行一个周期后退出（用于测试或单次拉取）。
    """
    global _WORKER_NAME
    # 如果 worker_name 是 None，使用默认 worker
    if worker_name is None:
        worker_name = cfg.DEFAULT_WORKER_NAME
    _WORKER_NAME = worker_name
    
    # 确定实际使用的 worker（包括默认 worker）
    effective_worker = worker_name

    tasks_dir = _get_tasks_dir()
    ongoing_dir = _get_ongoing_dir()

    # 确保工人的目录存在
    tasks_dir.mkdir(parents=True, exist_ok=True)
    ongoing_dir.mkdir(parents=True, exist_ok=True)

    # 确保 worker 已注册（包括默认 worker）
    from secretary.agents import register_worker, update_worker_status
    register_worker(effective_worker, description="通用工人" if not worker_name else "")
    update_worker_status(effective_worker, "busy", pid=_PID)

    label = f"👷 {effective_worker}"

    # 获取 worker 的 stats 目录
    from secretary.agents import _worker_stats_dir
    worker_stats_dir = _worker_stats_dir(effective_worker)
    
    print("=" * 60)
    print(f"{label} 启动  (PID={_PID})")
    print(f"   监控目录: {tasks_dir}")
    print(f"   执行目录: {ongoing_dir}")
    print(f"   报告目录: {cfg.REPORT_DIR}")
    print(f"   统计目录: {worker_stats_dir}")
    print(f"   扫描间隔: {cfg.SCAN_INTERVAL}s")
    print(f"   模式: {'单次' if once else '持续运行（循环直到 Ctrl+C）'}")
    if worker_name:
        print(f"   工人名: {worker_name}")
    else:
        print(f"   💡 可启动多个 `kai hire` 或 `kai hire <name>` 并行处理任务")
    print("=" * 60)

    cycle = 0

    try:
        while True:
            cycle += 1
            try:
                # 1. 优先处理 ongoing/ 中已有的任务
                target = _pick_one_ongoing()
                if target:
                    print(f"\n📋 [{label} PID={_PID}] 处理任务: {target.name}")
                    process_ongoing_task(target, verbose=verbose)
                    from secretary.agents import record_task_completion
                    record_task_completion(effective_worker, target.stem)

                # 2. 如果 ongoing/ 没活了，从 tasks/ 拉新任务
                elif not once or cycle == 1:
                    new_target = _pick_one_new()
                    if new_target:
                        print(f"\n📋 [{label} PID={_PID}] 新任务: {new_target.name}")
                        process_ongoing_task(new_target, verbose=verbose)
                        from secretary.agents import record_task_completion
                        record_task_completion(effective_worker, new_target.stem)
                    else:
                        if verbose:
                            ts = datetime.now().strftime("%H:%M:%S")
                            print(f"💤 [{label} PID={_PID}] [{ts}] 没有新任务，{cfg.SCAN_INTERVAL}s 后再扫描...")

            except Exception as e:
                # 单周期内异常不退出：记录后继续下一轮
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"\n⚠️ [{label} PID={_PID}] [{ts}] 本周期异常（已忽略，继续下一轮）: {e}",
                      file=sys.stderr)
                if verbose:
                    traceback.print_exc(file=sys.stderr)

            if once:
                break

            time.sleep(cfg.SCAN_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n\n🛑 {label} 已停止 (PID={_PID}, 共 {cycle} 个周期)")
    finally:
        # 更新工人状态
        try:
            from secretary.agents import update_worker_status
            final_worker = worker_name or cfg.DEFAULT_WORKER_NAME
            update_worker_status(final_worker, "idle", pid=None)
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--worker", type=str, default=None, help="worker 名称")
    args = parser.parse_args()
    run_scanner(once=args.once, verbose=not args.quiet, worker_name=args.worker)

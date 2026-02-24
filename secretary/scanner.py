"""
统一的任务扫描器 — 所有 agent 使用相同的循环逻辑

所有 agent 都使用统一的目录结构：
- input_dir (tasks/): 输入目录，由其他 agent 或人类写入任务
- processing_dir (ongoing/): 处理目录，标识正在处理的任务
- output_dir (reports/): 输出目录，工作完成后的总结

触发逻辑根据 agent 类型不同：
- Secretary/Worker: 观察自己的 input_dir 是否有文件
- Boss: 观察自己的 input_dir（全局目标）或监控的 worker 的 output_dir 是否有新报告
- Recycler: 扫描所有 agent 的 output_dir 查找报告文件

会话管理：
- 第一轮：使用完整提示词（角色定义 + 任务）
- 后续轮次：使用 session_id 续轮，只发送新任务或上下文

注意：具体的 agent 类型定义已移至 secretary/agent_types/ 目录

执行范围: 仅 execution_scope 为 task / hire / recycle 的任务会被执行；
  monitor 等其它类型不进入执行流程（见 config.EXECUTABLE_TASK_TYPES）。
  任务文件可通过 <!-- execution_scope: monitor --> 等标注类型，未标注时视为 task。

工作流程:
1. 持续扫描 input_dir 文件夹（统一触发规则）
2. 如果有文件，根据配置移动到 processing_dir（如果需要）或直接处理
3. 根据配置的终止条件和提示词调用 Agent
4. 根据终止条件判断是否继续（单次执行 vs 直到文件删除）
5. 完成后写入统计
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
from secretary.agent_config import AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig, build_worker_config, build_boss_config, build_recycler_config
from secretary.agent_types.worker import run_worker_first_round, run_worker_continue, run_worker_refine
from secretary.agent_runner import RoundStats
from secretary.agent_loop import run_loop, load_prompt
from secretary.agent_types.secretary import run_secretary

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


# ============================================================
#  文件锁 — 多进程互斥
# ============================================================

# 锁机制已移除：每个 worker 有独立的目录，不需要锁


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

def _write_scanner_report(task_stats: TaskStats, stats_dir: Path):
    """
    将 scanner 的调用统计写入 stats/ 文件夹

    生成两个文件:
      - {task_name}-stats.md  — 可读的 Markdown 统计报告
      - {task_name}-stats.json — 结构化数据 (数字统计 + 完整对话日志)
    """
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


def _move_task_to_ongoing_dir(task_file: Path, ongoing_dir: Path) -> Path | None:
    """将任务文件移动到指定 ongoing 目录；用于统一扫描器按 role 指定目录。"""
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

def process_ongoing_task(ongoing_file: Path, verbose: bool = True, config: AgentConfig | None = None):
    """
    持续调用 Agent 直到它删除 ongoing/ 中的任务文件（或根据终止条件）
    
    使用配置中的提示词模板，支持统一的终止条件判断
    
    注意：verbose=True 时，所有输出（包括 agent 的对话过程）都会实时输出到 stdout/stderr
    在后台运行时，这些输出会被重定向到日志文件，并实时刷新。

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

    # 使用配置的标签，如果没有配置则使用默认
    label = config.label if config else f"👷 {task_name}"
    
    # 开始处理任务信息直接输出
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"[{ts}] ⚙️ 开始处理任务: {ongoing_file.name} (PID={_PID})")
    print(f"{'='*60}")
    print(f"   任务文件: {ongoing_file}")
    if ongoing_file.exists():
        file_size = ongoing_file.stat().st_size
        print(f"   文件大小: {file_size} 字节")
    if min_time > 0:
        print(f"   ⏱️ 最低执行时间: {min_time}s")
    if config:
        print(f"   Agent: {config.name} ({config.label})")
    
    # 任务开始信息已写入日志，这里不再打印（后台运行时会被丢弃）

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
                # 完善阶段信息已写入日志，这里不再打印
                result = run_worker_refine(
                    elapsed_sec=elapsed,
                    min_time=min_time,
                    verbose=verbose,
                    timeout_sec=round_timeout,
                    session_id=task_stats.session_id,  # 使用保存的 session_id
                    agent_name=config.name if config else None,
                    report_dir=config.output_dir if config else None,
                )
            elif round_num == 1:
                # 首轮调用信息已写入日志，这里不再打印
                report_dir = config.output_dir if config else None
                result = run_worker_first_round(ongoing_file, verbose=verbose,
                                                timeout_sec=round_timeout,
                                                report_dir=report_dir,
                                                agent_name=config.name if config else None)
            else:
                # 续轮调用信息已写入日志，这里不再打印
                report_dir = config.output_dir if config else None
                result = run_worker_continue(ongoing_file, verbose=verbose,
                    agent_name=config.name if config else None,
                    report_dir=config.output_dir if config else None,
                                             timeout_sec=round_timeout,
                                             session_id=task_stats.session_id)  # 使用保存的 session_id

            # 记录本轮统计 + 对话日志
            task_stats.add_round(
                round_num, result.stats, result.success,
                raw_output=result.raw_output,
                readable_output=result.output,
            )
            
            # 记录本轮信息直接输出
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if task_deleted:
                elapsed = time.time() - task_stats._wall_start
                remaining = min_time - elapsed if min_time > 0 else 0
                print(f"\n[{ts}] 🔄 第 {round_num} 轮: 完善阶段 (--resume)")
                print(f"   已用 {elapsed:.0f}s / {min_time}s, 还需 {remaining:.0f}s")
            elif round_num == 1:
                print(f"\n[{ts}] 🚀 第 1 轮: 首轮调用 (新会话)")
            else:
                print(f"\n[{ts}] 🔄 第 {round_num} 轮: 续轮调用 (--resume {task_stats.session_id[:8] if task_stats.session_id else 'N/A'}...)")
            
            if not result.success:
                print(f"   ⚠️ Agent 本轮出错 (code={result.return_code})")
                print(f"   错误信息: {result.output[:200]}")

            # 检查: Agent 是否已经删除了任务文件
            if not task_deleted and not ongoing_file.exists():
                task_deleted = True
                elapsed = time.time() - task_stats._wall_start

                # 记录任务文件删除直接输出
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{ts}] ✅ 任务文件已删除 (Agent认为完成)")
                if min_time > 0 and elapsed < min_time:
                    remaining = min_time - elapsed
                    print(f"   ⏱️ 但最低时间未到 ({elapsed:.0f}s / {min_time}s)，进入完善阶段，还需 {remaining:.0f}s")

                if min_time > 0 and elapsed < min_time:
                    remaining = min_time - elapsed
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
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = time.time() - task_stats._wall_start
            if result.success:
                print(f"\n[{ts}] ℹ️ Agent 本轮正常结束，但任务文件仍存在 → 任务未完成")
            else:
                print(f"\n[{ts}] ⚠️ Agent 本轮出错 (code={result.return_code})")
                print(f"   错误信息: {result.output[:200]}")
            if min_time > 0:
                print(f"   ⏱️ 最低执行时间未到 ({elapsed:.0f}s / {min_time}s)，将续轮直至时间用尽或任务完成")
            print(f"   {cfg.WORKER_RETRY_INTERVAL}s 后用 --resume 续轮...")
            time.sleep(cfg.WORKER_RETRY_INTERVAL)

        # 任务完成
        task_stats.success = True
        task_stats.mark_end()

        # 任务完成信息直接输出
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] ✅ 任务完成: {task_name} (PID={_PID})")
        print(f"   共执行 {round_num} 轮"
              f" | 墙钟用时 {task_stats.wall_clock_sec:.1f}s"
              f" | Agent用时 {task_stats.total_duration_sec:.1f}s"
              f" | Tool Calls {task_stats.total_tool_calls} 次"
              f" | 涉及 {len(task_stats.all_files_changed)} 个文件")
        if min_time > 0:
            print(f"   ⏱️ 最低执行时间: {min_time}s (实际: {task_stats.wall_clock_sec:.1f}s)")
        _print_report(task_name, config)
        # 使用配置的 stats_dir，无 config 时使用 ongoing 同级的 stats
        stats_dir = config.stats_dir if config else ongoing_file.parent / "stats"
        _write_scanner_report(task_stats, stats_dir)
        
        # 注意：memory的更新由agent自己决定，不在这里自动更新

    except Exception as e:
        # 即使异常退出，也保存已有的统计数据
        task_stats.mark_end()
        stats_dir = config.stats_dir if config else ongoing_file.parent / "stats"
        _write_scanner_report(task_stats, stats_dir)
        # 不抛出异常，让 scanner 循环继续处理下一个任务
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] ❌ 处理任务时发生异常: {ongoing_file.name} | 错误: {e}")
        traceback.print_exc()


def _print_report(task_name: str, config: AgentConfig | None = None):
    """打印报告文件路径（config 或 output_dir 为空时跳过）"""
    report_dir = config.output_dir if config else None
    if not report_dir:
        return
    expected = report_dir / f"{task_name}-report.md"
    if expected.exists():
        print(f"   📄 报告: {expected}")
    else:
        reports = sorted(
            [r for r in report_dir.glob("*.md") if r.stem.endswith("-report")],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if reports:
            print(f"   📄 最新报告: {reports[0]}")


# ============================================================
#  统一扫描器：统一的触发规则和处理逻辑
# ============================================================

def _get_trigger_debug_info(config: AgentConfig) -> str:
    """
    获取触发检查的详细信息（用于debug日志）
    返回字符串描述为什么触发或没有触发
    """
    trigger = config.trigger
    info_parts = []
    
    # 1. 自定义触发函数
    if trigger.custom_trigger_fn:
        try:
            result = trigger.custom_trigger_fn(config)
            if result:
                info_parts.append(f"自定义触发函数返回 {len(result)} 个文件")
            else:
                info_parts.append("自定义触发函数返回空（未触发）")
        except Exception as e:
            info_parts.append(f"自定义触发函数异常: {e}")
        return " | ".join(info_parts)
    
    # 2. 标准目录监视逻辑
    if not trigger.watch_dirs:
        return "无监视目录配置"
    
    info_parts.append(f"监视目录: {len(trigger.watch_dirs)} 个")
    info_parts.append(f"触发条件: {trigger.condition.value}")
    
    # 检查每个目录的状态
    all_satisfied = True
    for watch_dir in trigger.watch_dirs:
        if not watch_dir.exists():
            if trigger.condition == TriggerCondition.HAS_FILES:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: 目录不存在")
            else:
                info_parts.append(f"{watch_dir.name}: 目录不存在（视为空，满足条件）")
            continue
        
        md_files = list(watch_dir.glob("*.md"))
        file_count = len(md_files)
        has_files = file_count > 0
        
        if trigger.condition == TriggerCondition.HAS_FILES:
            if has_files:
                info_parts.append(f"{watch_dir.name}: {file_count} 个文件 ✓")
            else:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: 0 个文件 ✗")
        elif trigger.condition == TriggerCondition.IS_EMPTY:
            if has_files:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: {file_count} 个文件（不满足空条件）✗")
            else:
                info_parts.append(f"{watch_dir.name}: 空目录 ✓")
    
    if all_satisfied:
        # 条件满足，检查是否有可执行文件
        if trigger.condition == TriggerCondition.HAS_FILES:
            if config.use_ongoing and config.processing_dir.exists() and config.processing_dir in trigger.watch_dirs:
                ongoing_files = [f for f in config.processing_dir.glob("*.md") if _is_executable_task(f)]
                if ongoing_files:
                    info_parts.append(f"→ 触发: processing目录有 {len(ongoing_files)} 个可执行文件")
                    return " | ".join(info_parts)
            
            if config.input_dir in trigger.watch_dirs and config.input_dir.exists():
                all_md = list(config.input_dir.glob("*.md"))
                executable = [p for p in all_md if _is_executable_task(p)]
                non_executable = [p for p in all_md if not _is_executable_task(p)]
                
                # 详细记录文件信息
                if all_md:
                    file_details = []
                    for f in all_md[:5]:  # 最多显示5个文件
                        scope = _get_task_execution_scope(f)
                        is_exec = _is_executable_task(f)
                        file_details.append(f"{f.name}(scope={scope},exec={is_exec})")
                    if len(all_md) > 5:
                        file_details.append(f"...共{len(all_md)}个文件")
                    info_parts.append(f"文件列表: {', '.join(file_details)}")
                
                if executable:
                    info_parts.append(f"→ 触发: input目录有 {len(executable)} 个可执行文件")
                    return " | ".join(info_parts)
                else:
                    if non_executable:
                        non_exec_details = []
                        for f in non_executable[:3]:
                            scope = _get_task_execution_scope(f)
                            non_exec_details.append(f"{f.name}(scope={scope})")
                        info_parts.append(f"→ 未触发: input目录有 {len(all_md)} 个文件但无可执行文件 | 非可执行: {', '.join(non_exec_details)}")
                    else:
                        info_parts.append(f"→ 未触发: input目录有 {len(all_md)} 个文件但无可执行文件")
            else:
                info_parts.append("→ 未触发: 条件满足但未找到可执行文件")
        else:
            info_parts.append("→ 触发: 条件满足")
    else:
        info_parts.append("→ 未触发: 条件不满足")
    
    return " | ".join(info_parts)


def _unified_trigger(config: AgentConfig) -> list[Path]:
    """
    统一触发规则：根据TriggerConfig配置进行触发
    
    支持：
    1. 自定义触发函数（custom_trigger_fn）
    2. 标准目录监视（watch_dirs + condition）
    3. 虚拟触发文件（create_virtual_file）
    """
    trigger = config.trigger
    
    # 1. 如果提供了自定义触发函数，优先使用
    if trigger.custom_trigger_fn:
        return trigger.custom_trigger_fn(config)
    
    # 2. 标准目录监视逻辑
    if not trigger.watch_dirs:
        return []
    
    # 检查所有监视目录是否满足条件
    all_satisfied = True
    for watch_dir in trigger.watch_dirs:
        if not watch_dir.exists():
            if trigger.condition == TriggerCondition.HAS_FILES:
                all_satisfied = False
                break
            # IS_EMPTY: 目录不存在视为空，满足条件
            continue
        
        md_files = list(watch_dir.glob("*.md"))
        has_files = len(md_files) > 0
        
        if trigger.condition == TriggerCondition.HAS_FILES:
            if not has_files:
                all_satisfied = False
                break
        elif trigger.condition == TriggerCondition.IS_EMPTY:
            if has_files:
                all_satisfied = False
                break
    
    if not all_satisfied:
        return []
    
    # 3. 条件满足，返回触发文件
    if trigger.condition == TriggerCondition.HAS_FILES:
        # 有文件时触发：返回文件列表
        # 优先处理processing目录（如果存在且use_ongoing=True）
        if config.use_ongoing and config.processing_dir.exists() and config.processing_dir in trigger.watch_dirs:
            candidates = [
                f for f in sorted(config.processing_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)
                if _is_executable_task(f)
            ]
            if candidates:
                return [candidates[0]]

        # 从input目录取文件
        if config.input_dir in trigger.watch_dirs and config.input_dir.exists():
            all_md = list(config.input_dir.glob("*.md"))
            executable = [p for p in all_md if _is_executable_task(p)]
            
            # 文件检查详情直接输出（用于debug）
            if all_md:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{ts}] 📋 文件检查详情:")
                for f in all_md[:5]:  # 最多显示5个
                    scope = _get_task_execution_scope(f)
                    is_exec = _is_executable_task(f)
                    print(f"   - {f.name}: scope={scope}, executable={is_exec}")
                print(f"   可执行文件数: {len(executable)}/{len(all_md)}")
            
            if executable:
                # 按修改时间排序，返回最早的文件
                return [sorted(executable, key=lambda p: p.stat().st_mtime)[0]]
        
        # 从其他监视目录取文件
        result = []
        for watch_dir in trigger.watch_dirs:
            if watch_dir == config.input_dir or watch_dir == config.processing_dir:
                continue
            if watch_dir.exists():
                all_md = list(watch_dir.glob("*.md"))
                executable = [p for p in all_md if _is_executable_task(p)]
                if executable:
                    result.extend(executable)
        if result:
            return [sorted(result, key=lambda p: p.stat().st_mtime)[0]]
        
        return []
    
    elif trigger.condition == TriggerCondition.IS_EMPTY:
        # 为空时触发：创建虚拟触发文件（如果需要）
        if trigger.create_virtual_file:
            trigger_file = config.base_dir / trigger.virtual_file_name
            if not trigger_file.exists():
                trigger_file.touch()
            return [trigger_file]
        return []
    


def _get_agent_type(config: AgentConfig):
    """
    根据配置获取对应的 AgentType 实例
    
    使用注册表动态查找，支持内置类型和自定义类型。
    优先从 agent 注册信息中获取类型名称，如果不存在则根据提示词模板推断。
    """
    from secretary.agent_registry import get_agent_type, initialize_registry, list_agent_types
    from secretary.agents import get_worker
    import secretary.config as cfg
    
    # 确保注册表已初始化
    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass  # 如果初始化失败，继续尝试使用已注册的类型
    
    # 方法1: 从 agent 注册信息中获取类型名称
    worker_info = get_worker(config.name)
    if worker_info and worker_info.get("type"):
        type_name = worker_info["type"]
        agent_type = get_agent_type(type_name)
        if agent_type:
            return agent_type
    
    # 方法2: 根据提示词模板推断类型（向后兼容）
    prompt_to_type = {
        "worker_first_round.md": "worker",
        "secretary.md": "secretary",
        "boss.md": "boss",
        "recycler.md": "recycler",
    }
    
    type_name = prompt_to_type.get(config.first_round_prompt)
    if type_name:
        agent_type = get_agent_type(type_name)
        if agent_type:
            return agent_type
    
    # 方法3: 尝试直接使用提示词模板名称（去掉 .md 后缀）
    if config.first_round_prompt.endswith(".md"):
        type_name = config.first_round_prompt[:-3]
        # 处理特殊名称（如 worker_first_round -> worker）
        if type_name.startswith("worker_"):
            type_name = "worker"
        agent_type = get_agent_type(type_name)
        if agent_type:
            return agent_type
    
    # 方法4: 默认使用 worker（向后兼容）
    default_type = get_agent_type("worker")
    if default_type:
        return default_type
    
    # 如果所有方法都失败，抛出异常
    available_types = list_agent_types()
    raise ValueError(
        f"无法确定 agent 类型。"
        f"配置: name={config.name}, prompt={config.first_round_prompt}. "
        f"可用类型: {', '.join(available_types) if available_types else '无'}"
    )


def _process_one_unified(config: AgentConfig, file_path: Path, verbose: bool) -> None:
    """
    统一处理逻辑：使用集中化的 agent 类型定义
    """
    try:
        agent_type = _get_agent_type(config)
        agent_type.process_task(config, file_path, verbose=verbose)
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] ❌ 处理任务失败: {file_path.name} | 错误: {e}")
        traceback.print_exc()
        raise


# 旧的 _process_* 函数已被集中化的 agent 类型定义替代
# 现在使用 _process_one_unified -> _get_agent_type -> agent_type.process_task


def run_unified_scanner(config: AgentConfig, once: bool = False, verbose: bool = True) -> None:
    """
    统一扫描循环：所有 agent 使用相同的循环逻辑
    
    循环模式：检查触发条件 -> 执行动作 -> 休眠 -> 检查触发条件 -> ...
    
    通过配置区分终止条件和提示词。
    默认持续运行（once=False），除非明确指定 once=True（仅用于测试）。
    """
    # 确保目录存在
    config.input_dir.mkdir(parents=True, exist_ok=True)
    if config.use_ongoing:
        config.processing_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.log_file is not None:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
    config.stats_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)

    # Recycler需要额外的solved和unsolved目录
    if config.first_round_prompt == "recycler.md":
        recycler_dir = config.base_dir
        (recycler_dir / "solved").mkdir(parents=True, exist_ok=True)
        (recycler_dir / "unsolved").mkdir(parents=True, exist_ok=True)

    # 所有 agent 都注册并更新状态（现在所有 agent 都使用 UNTIL_FILE_DELETED）
    from secretary.agents import register_agent, update_worker_status
    agent_type = _get_agent_type(config)
    agent_type_name = agent_type.name if hasattr(agent_type, 'name') else "worker"
    # 确保 agent 已注册
    from secretary.agents import get_worker
    if not get_worker(config.name):
        register_agent(config.name, agent_type=agent_type_name, description="")
    update_worker_status(config.name, "busy", pid=_PID)

    label = config.label
    # 启动信息直接输出（会被重定向到日志文件）
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 60)
    print(f"[{ts}] {label} 启动 (PID={_PID})")
    print(f"   输入目录: {config.input_dir}")
    if config.use_ongoing:
        print(f"   处理目录: {config.processing_dir}")
    print(f"   输出目录: {config.output_dir}")
    print(f"   统计目录: {config.stats_dir}")
    print(f"   扫描间隔: {cfg.SCAN_INTERVAL}s")
    print(f"   模式: {'单次' if once else '持续运行（循环直到 Ctrl+C）'}")
    print(f"   终止条件: {config.termination.value}")
    print("=" * 60 + "\n")

    def trigger_fn():
        try:
            result = _unified_trigger(config)
        except Exception as e:
            # 触发检查时的异常直接输出
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 触发检查异常: {e}")
            traceback.print_exc()
            # 返回空列表，避免崩溃
            result = []
        
        # 每30秒记录一次触发检查状态（用于debug）
        import time
        current_time = time.time()
        if not hasattr(trigger_fn, '_last_log_time'):
            trigger_fn._last_log_time = 0
        
        should_log = False
        if result:
            should_log = True
        elif current_time - trigger_fn._last_log_time >= 30:
            should_log = True
            trigger_fn._last_log_time = current_time
        
        if should_log:
            trigger_info = _get_trigger_debug_info(config)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if result:
                print(f"\n[{ts}] 🔔 触发: {len(result)} 个文件 | {trigger_info}")
            else:
                print(f"\n[{ts}] 🔍 未触发: {trigger_info}")
            if result:
                trigger_fn._last_log_time = current_time
        
        return result

    def process_fn(file_path: Path):
        # 设置执行状态为 True
        from secretary.agents import set_agent_executing, increment_completed_tasks
        set_agent_executing(config.name, True)
        
        # 每次触发就增加已完成计数（触发函数的调用次数）
        increment_completed_tasks(config.name)
        
        # 触发处理信息直接输出
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] 🔔 触发处理: {file_path.name}")
        print(f"   文件路径: {file_path}")
        print(f"   文件存在: {file_path.exists()}")
        if file_path.exists():
            file_size = file_path.stat().st_size
            scope = _get_task_execution_scope(file_path)
            is_exec = _is_executable_task(file_path)
            print(f"   文件大小: {file_size} 字节")
            print(f"   execution_scope: {scope}, executable: {is_exec}")
        print(f"   终止条件: {config.termination.value}")
        
        try:
            _process_one_unified(config, file_path, verbose)
        except Exception as e:
            # 异常信息直接输出，但不抛出异常，让循环继续
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 处理任务异常: {file_path.name} | 错误: {e}")
            print(f"   异常类型: {type(e).__name__}")
            print(f"   文件路径: {file_path}")
            print(f"   完整异常信息:")
            traceback.print_exc()
        finally:
            # 处理完成（无论成功或失败）都清除执行状态
            set_agent_executing(config.name, False)

    def on_idle():
        # 空闲状态每30秒记录一次
        import time
        current_time = time.time()
        if not hasattr(on_idle, '_last_log_time'):
            on_idle._last_log_time = 0
        
        if current_time - on_idle._last_log_time >= 30:
            on_idle._last_log_time = current_time
            trigger_info = _get_trigger_debug_info(config)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] 🔍 触发检查: {trigger_info}")

    def on_exit():
        if config.termination == TerminationCondition.UNTIL_FILE_DELETED:
            try:
                from secretary.agents import update_worker_status
                update_worker_status(config.name, "idle", pid=None)
            except Exception:
                pass

    run_loop(
        trigger_fn=trigger_fn,
        process_fn=process_fn,
        interval_sec=cfg.SCAN_INTERVAL,
        once=once,
        label=label,
        verbose=verbose,
        on_idle=on_idle,
        on_exit=on_exit,
        log_file=str(config.log_file) if config.log_file else None,
    )


# ============================================================
#  入口函数：使用统一的配置系统
# ============================================================

def run_kai_scanner(once: bool = False, verbose: bool = False, secretary_name: str = "kai") -> None:
    """运行 Secretary 任务扫描器：扫描 agents/<name>/tasks/，每项调用 run_secretary，输出写入 <name>/logs。"""
    from secretary.agent_types import SecretaryAgent
    agent_type = SecretaryAgent()
    config = agent_type.build_config(cfg.BASE_DIR, secretary_name)
    run_unified_scanner(config, once=once, verbose=verbose)


def run_scanner(once: bool = False, verbose: bool = True, worker_name: str | None = None) -> None:
    """
    运行 Worker 扫描循环（使用统一的循环逻辑）。
    每轮最多处理一项：优先 ongoing/，否则从 tasks/ 拉新任务。
    """
    config = build_worker_config(cfg.BASE_DIR, worker_name or cfg.DEFAULT_WORKER_NAME)
    run_unified_scanner(config, once=once, verbose=verbose)


def run_boss_scanner(once: bool = False, verbose: bool = True, boss_name: str | None = None) -> None:
    """
    运行 Boss 扫描循环（使用统一的循环逻辑）。
    Boss监控指定worker的队列，在队列为空时生成新任务。
    """
    if not boss_name:
        raise ValueError("Boss名称不能为空")
    config = build_boss_config(cfg.BASE_DIR, boss_name)
    run_unified_scanner(config, once=once, verbose=verbose)


def run_recycler_scanner(once: bool = False, verbose: bool = True, recycler_name: str | None = None) -> None:
    """
    运行 Recycler 扫描循环（使用统一的循环逻辑）。
    Recycler扫描所有agent的reports目录，审查完成报告。
    """
    if not recycler_name:
        recycler_name = "recycler"
    config = build_recycler_config(cfg.BASE_DIR, recycler_name)
    run_unified_scanner(config, once=once, verbose=verbose)


if __name__ == "__main__":
    import argparse
    from secretary.agent_registry import get_agent_type, initialize_registry, list_agent_types
    
    parser = argparse.ArgumentParser(description="任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--worker", type=str, default=None, help="worker 名称（向后兼容）")
    parser.add_argument("--boss", type=str, default=None, help="boss 名称（向后兼容）")
    parser.add_argument("--recycler", type=str, default=None, help="recycler 名称（向后兼容）")
    parser.add_argument("--agent", type=str, default=None, help="agent 名称（通用参数）")
    parser.add_argument("--type", type=str, default=None, help="agent 类型（通用参数，如 worker, boss, recycler 或自定义类型）")
    args = parser.parse_args()
    
    # 确保注册表已初始化
    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass
    
    # 优先使用新的通用参数
    if args.agent and args.type:
        # 使用通用方式启动
        agent_type_instance = get_agent_type(args.type)
        if agent_type_instance is None:
            available_types = list_agent_types()
            print(f"❌ 未知的 agent 类型: {args.type}")
            if available_types:
                print(f"   可用类型: {', '.join(available_types)}")
            sys.exit(1)
        
        config = agent_type_instance.build_config(cfg.BASE_DIR, args.agent)
        run_unified_scanner(config, once=args.once, verbose=not args.quiet)
    elif args.boss:
        # 向后兼容：Boss
        run_boss_scanner(once=args.once, verbose=not args.quiet, boss_name=args.boss)
    elif args.recycler:
        # 向后兼容：Recycler
        run_recycler_scanner(once=args.once, verbose=not args.quiet, recycler_name=args.recycler)
    else:
        # 向后兼容：Worker（默认）
        run_scanner(once=args.once, verbose=not args.quiet, worker_name=args.worker)

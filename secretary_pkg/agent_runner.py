"""
Cursor Agent CLI 调用器

使用 --output-format stream-json 获取结构化输出，解析:
  - tool_call 事件 (文件编辑、shell 命令等)
  - result 事件 (duration_ms)
  - session_id
"""
import json
import subprocess
import sys
import os
import time
from dataclasses import dataclass, field

from secretary.config import CURSOR_BIN, DEFAULT_MODEL


@dataclass
class RoundStats:
    """单轮调用的统计信息"""
    duration_ms: int = 0              # 总耗时(毫秒)
    duration_api_ms: int = 0          # API耗时(毫秒)
    session_id: str = ""              # 会话ID
    model: str = ""                   # 使用的模型

    # tool call 统计
    file_edits: list[str] = field(default_factory=list)     # 编辑的文件列表
    file_creates: list[str] = field(default_factory=list)   # 创建的文件列表
    shell_commands: list[str] = field(default_factory=list) # 执行的shell命令
    tool_call_count: int = 0                                # tool call 总数

    last_assistant_text: str = ""     # 最后一条 assistant 回复文本

    @property
    def files_changed(self) -> list[str]:
        """所有涉及的文件 (去重)"""
        return list(set(self.file_edits + self.file_creates))

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000.0


@dataclass
class AgentResult:
    """Agent 执行结果"""
    success: bool
    output: str                # 可读的输出文本 (assistant 回复 + tool call 摘要)
    return_code: int
    duration: float
    stats: RoundStats = field(default_factory=RoundStats)
    raw_output: str = ""       # 完整的原始 stream-json 输出 (用于对话日志)


def _parse_stream_event(line: str, stats: RoundStats) -> str | None:
    """
    解析一行 stream-json 事件，更新统计，返回可读文本 (用于 verbose 输出)

    事件类型:
      system/init   — session_id, model
      tool_call     — started/completed, 包含文件编辑和 shell 命令
      assistant     — 模型的文本回复
      thinking      — 思考过程 (delta)
      result        — 最终结果, duration_ms
    """
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return line.strip()  # 非 JSON 行原样返回

    evt_type = evt.get("type", "")
    subtype = evt.get("subtype", "")

    # ---- init: 提取 session_id, model ----
    if evt_type == "system" and subtype == "init":
        stats.session_id = evt.get("session_id", "")
        stats.model = evt.get("model", "")
        return None

    # ---- tool_call completed: 统计 ----
    if evt_type == "tool_call" and subtype == "completed":
        stats.tool_call_count += 1
        tc = evt.get("tool_call", {})

        # shell 命令
        if "shellToolCall" in tc:
            cmd = tc["shellToolCall"].get("args", {}).get("command", "")
            if cmd:
                stats.shell_commands.append(cmd)
            return f"🔧 Shell: {cmd}"

        # 文件编辑
        if "editToolCall" in tc:
            fpath = tc["editToolCall"].get("args", {}).get("filePath", "")
            if fpath and fpath not in stats.file_edits:
                stats.file_edits.append(fpath)
            return f"✏️  Edit: {fpath}"

        # 文件创建/写入
        if "writeToolCall" in tc:
            fpath = tc["writeToolCall"].get("args", {}).get("filePath", "")
            if fpath and fpath not in stats.file_creates:
                stats.file_creates.append(fpath)
            return f"📝 Write: {fpath}"

        if "createFileToolCall" in tc:
            fpath = tc["createFileToolCall"].get("args", {}).get("filePath", "")
            if fpath and fpath not in stats.file_creates:
                stats.file_creates.append(fpath)
            return f"📝 Create: {fpath}"

        # 其他 tool call (如 readFile, grep 等) 只计数
        return None

    # ---- tool_call started: 忽略, 只看 completed ----
    if evt_type == "tool_call" and subtype == "started":
        return None

    # ---- assistant 文本输出 ----
    if evt_type == "assistant":
        msg = evt.get("message", {})
        content = msg.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        text = "".join(texts).strip()
        if text:
            stats.last_assistant_text = text  # 持续更新，最终保留最后一条
            return text
        return None

    # ---- result: 最终统计 ----
    if evt_type == "result":
        stats.duration_ms = evt.get("duration_ms", 0)
        stats.duration_api_ms = evt.get("duration_api_ms", 0)
        return None

    # ---- thinking: 忽略(太多 delta) ----
    if evt_type == "thinking":
        return None

    # ---- user message: 忽略 ----
    if evt_type == "user":
        return None

    return None


def run_agent(
    prompt: str,
    workspace: str = "",
    model: str = "",
    timeout: int | None = None,
    verbose: bool = True,
    continue_session: bool = False,
) -> AgentResult:
    """
    调用 Cursor Agent，使用 stream-json 获取结构化统计数据

    Returns:
        AgentResult (包含 stats: RoundStats)
    """
    cmd = [CURSOR_BIN, "agent", "--print", "--force", "--trust",
           "--output-format", "stream-json"]

    if continue_session:
        cmd.append("--continue")

    if workspace:
        cmd.extend(["--workspace", str(workspace)])

    effective_model = model or DEFAULT_MODEL
    if effective_model and effective_model.lower() != "auto":
        cmd.extend(["--model", effective_model])

    cmd.append(prompt)

    env = os.environ.copy()
    start = time.time()
    stats = RoundStats()

    if verbose:
        mode = "续轮 --continue" if continue_session else "首轮"
        print(f"  🤖 调用 Cursor Agent ({mode}) ...")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=workspace or None,
        )

        output_lines: list[str] = []
        raw_lines: list[str] = []
        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                raw_lines.append(line)
                readable = _parse_stream_event(line.strip(), stats)
                if readable:
                    output_lines.append(readable)
                    if verbose:
                        sys.stdout.write(f"  │ {readable}\n")
                        sys.stdout.flush()

        rc = proc.wait(timeout=timeout)
        dur = time.time() - start

        # 如果 stream-json 没给 duration_ms，用本地计时
        if stats.duration_ms == 0:
            stats.duration_ms = int(dur * 1000)

        full_output = "\n".join(output_lines)
        raw_full = "".join(raw_lines)  # 保留原始 stream-json 完整输出

        if verbose:
            print(f"  ├─ 耗时: {stats.duration_sec:.1f}s | Tool calls: {stats.tool_call_count}"
                  f" | 文件: {len(stats.files_changed)} | Shell: {len(stats.shell_commands)}")

        return AgentResult(
            success=(rc == 0),
            output=full_output,
            return_code=rc,
            duration=dur,
            stats=stats,
            raw_output=raw_full,
        )

    except subprocess.TimeoutExpired:
        proc.kill()
        return AgentResult(False, f"⏰ 超时 ({timeout}s)", -1, time.time() - start, stats)
    except FileNotFoundError:
        return AgentResult(False, f"❌ 找不到 cursor: {CURSOR_BIN}", -2, time.time() - start, stats)
    except Exception as e:
        return AgentResult(False, f"❌ 异常: {e}", -3, time.time() - start, stats)

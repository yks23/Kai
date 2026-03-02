"""
Agent CLI 调用器

使用 --output-format stream-json 获取结构化输出，解析:
  - tool_call 事件 (文件编辑、shell 命令等)
  - result 事件 (duration_ms)
  - session_id

实时输出时使用 log_formatter 美化 JSON 输出为易读的对话格式。
"""
import json
import subprocess
import sys
import os
import time
from dataclasses import dataclass, field

from secretary.config import DEFAULT_MODEL


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
    session_id: str = "",
    dialog_file=None,  # Path | None — 对话内容写入此文件，不写入 stdout/scanner.log
) -> AgentResult:
    """
    调用 Agent，使用 stream-json 获取结构化统计数据

    Args:
        prompt: 提示词
        workspace: 工作区路径
        model: 模型名称
        timeout: 超时时间（秒）
        verbose: 是否显示详细信息
        continue_session: 是否继续会话（使用 --continue，已废弃，优先使用 session_id）
        session_id: 会话ID，如果提供则使用 --resume <session_id> 精确恢复会话

    Returns:
        AgentResult (包含 stats: RoundStats)
    """
    # 使用 agent 命令
    from secretary.config import CURSOR_BIN, CURSOR_BIN_IS_PS
    
    # 在 Windows 上，如果通过 PowerShell 调用，需要特殊处理
    if CURSOR_BIN_IS_PS:
        # 通过 PowerShell 调用 agent，构建完整的命令字符串
        agent_cmd_parts = ["agent", "--print", "--force", "--trust", "--output-format", "stream-json"]
        
        # 优先使用 session_id 精确恢复会话
        if session_id:
            agent_cmd_parts.extend(["--resume", session_id])
        elif continue_session:
            # 如果没有 session_id，回退到 --continue
            agent_cmd_parts.append("--continue")
        
        if workspace:
            agent_cmd_parts.extend(["--workspace", str(workspace)])
        
        effective_model = model or DEFAULT_MODEL
        # 始终传递 --model 参数，包括 Auto
        if effective_model:
            agent_cmd_parts.extend(["--model", effective_model])
        
        agent_cmd_parts.append(prompt)
        
        # 构建 PowerShell 命令：powershell -Command "agent ..."
        # 需要正确转义引号
        agent_cmd_str = " ".join(f'"{arg}"' if ' ' in str(arg) or '"' in str(arg) else str(arg) for arg in agent_cmd_parts)
        cmd = [CURSOR_BIN, "-NoProfile", "-Command", agent_cmd_str]
    else:
        # 直接调用 agent 命令（Unix/Linux 或 agent.cmd）
        agent_bin = CURSOR_BIN
        cmd = [agent_bin]
        
        # 添加参数（这些参数对于非交互式调用很重要）
        cmd.extend(["--print", "--force", "--trust"])
        
        # output-format 用于获取结构化输出
        cmd.extend(["--output-format", "stream-json"])

        # 优先使用 session_id 精确恢复会话
        if session_id:
            cmd.extend(["--resume", session_id])
        elif continue_session:
            # 如果没有 session_id，回退到 --continue
            cmd.append("--continue")

        if workspace:
            cmd.extend(["--workspace", str(workspace)])

        effective_model = model or DEFAULT_MODEL
        # 始终传递 --model 参数，包括 Auto
        if effective_model:
            cmd.extend(["--model", effective_model])

        cmd.append(prompt)

    start = time.time()
    stats = RoundStats()

    if session_id:
        mode = f"续轮 {session_id[:8]}…"
    elif continue_session:
        mode = "续轮"
    else:
        mode = "首轮"

    # 将发出的提示词写入 dialog 文件（对话的"用户"侧）
    if dialog_file is not None:
        try:
            from pathlib import Path as _Path
            from datetime import datetime as _dt
            df = _Path(dialog_file)
            df.parent.mkdir(parents=True, exist_ok=True)
            ts = _dt.now().strftime("%H:%M:%S")
            with open(df, "a", encoding="utf-8") as _f:
                _f.write(f"\n{'─'*60}\n[{ts}] 提示词 ({mode})\n{'─'*60}\n")
                _f.write(prompt)
                _f.write("\n")
                _f.flush()
        except Exception:
            pass

    if verbose:
        print(f"  🤖 调用 Agent ({mode}) ...")
        # 打印完整命令（包括参数）
        cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
        print(f"  📝 完整命令: {cmd_str}")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=workspace or None,
        )

        output_lines: list[str] = []
        raw_lines: list[str] = []
        error_lines: list[str] = []  # 收集错误信息
        warning_count = 0
        json_lines: list[str] = []  # 收集JSON行用于格式化
        
        while True:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                raw_lines.append(line)
                stripped = line.strip()
                
                # 检查是否是错误信息（Error: 开头或包含 Error）
                if stripped.startswith("Error:") or (stripped and "Error:" in stripped):
                    error_lines.append(stripped)
                    if verbose:
                        sys.stdout.write(f"  ❌ {stripped}\n")
                        sys.stdout.flush()  # 实时刷新
                    continue
                
                # 过滤掉警告信息
                if stripped.startswith("Warning:") and "is not in the list of known options" in stripped:
                    warning_count += 1
                    if verbose:
                        sys.stdout.write(f"  ⚠️  {stripped}\n")
                        sys.stdout.flush()  # 实时刷新
                    continue
                
                # 尝试解析为JSON
                is_json = False
                try:
                    evt = json.loads(stripped)
                    is_json = True
                    json_lines.append(stripped)  # 收集JSON行用于格式化
                    # 解析 stream-json 事件并更新统计（但不输出，等待格式化）
                    _parse_stream_event(stripped, stats)
                except json.JSONDecodeError:
                    # 非JSON行：解析并实时输出
                    readable = _parse_stream_event(stripped, stats)
                    if readable:
                        output_lines.append(readable)
                    # 实时输出非JSON行
                    if verbose:
                        if readable:
                            sys.stdout.write(f"  │ {readable}\n")
                            sys.stdout.flush()
                        elif stripped and not stripped.startswith("Warning:"):
                            sys.stdout.write(f"  │ {stripped}\n")
                            sys.stdout.flush()

        rc = proc.wait(timeout=timeout)
        dur = time.time() - start

        # 如果 stream-json 没给 duration_ms，用本地计时
        if stats.duration_ms == 0:
            stats.duration_ms = int(dur * 1000)

        # 格式化对话内容：写入 dialog 文件（如有），否则打印到 stdout
        if json_lines:
            from secretary.log_formatter import format_stream_json_to_conversation
            from datetime import datetime
            json_content = "\n".join(json_lines)
            formatted = format_stream_json_to_conversation(json_content)
            if formatted:
                if dialog_file is not None:
                    # 写入对话文件，不输出到 scanner.log
                    try:
                        from pathlib import Path as _Path
                        df = _Path(dialog_file)
                        df.parent.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now().strftime("%H:%M:%S")
                        mode_label = f"续轮 {session_id[:8]}…" if session_id else ("续轮" if continue_session else "首轮")
                        with open(df, "a", encoding="utf-8") as _f:
                            _f.write(f"\n{'─'*60}\n[{ts}] {mode_label}\n{'─'*60}\n")
                            _f.write(formatted)
                            _f.write("\n")
                            _f.flush()
                    except Exception:
                        pass
                elif verbose:
                    # 无 dialog 文件时回退到 stdout（向后兼容）
                    print()
                    print(formatted)
                    print()

        full_output = "\n".join(output_lines)
        raw_full = "".join(raw_lines)  # 保留原始 stream-json 完整输出

        # 如果有错误信息，优先显示
        if error_lines:
            error_summary = "\n".join(error_lines)
            cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
            return AgentResult(
                False,
                f"❌ Agent 执行出错:\n"
                f"  命令: {cmd_str}\n"
                f"  返回码: {rc}\n"
                f"  错误信息: {error_summary}\n"
                f"  完整输出: {raw_full[:500]}",
                rc,
                dur,
                stats,
                raw_full,
            )

        if verbose:
            print(f"  ├─ 耗时: {stats.duration_sec:.1f}s | Tool calls: {stats.tool_call_count}"
                  f" | 文件: {len(stats.files_changed)} | Shell: {len(stats.shell_commands)}")
            if warning_count > 0:
                print(f"  ⚠️  检测到 {warning_count} 个参数警告（可能不影响功能）")

        # 检查是否有实际的有效输出
        has_valid_json = False
        for line in raw_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("Warning:"):
                try:
                    evt = json.loads(stripped)
                    if evt.get("type") in ("system", "assistant", "tool_call", "result"):
                        has_valid_json = True
                        break
                except json.JSONDecodeError:
                    pass
        
        has_valid_output = (
            stats.tool_call_count > 0 or 
            stats.last_assistant_text or 
            stats.session_id or
            has_valid_json
        )
        
        # 如果返回码非0，显示错误
        if rc != 0:
            cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
            return AgentResult(
                False,
                f"❌ Agent 执行失败 (返回码: {rc})\n"
                f"  命令: {cmd_str}\n"
                f"  完整输出: {raw_full[:800]}",
                rc,
                dur,
                stats,
                raw_full,
            )
        
        # 如果没有有效输出，但返回码是0，可能是参数不支持或需要交互式环境
        # 注意：警告信息不影响功能，只要返回码是0且有警告，说明命令可能执行了
        if rc == 0 and not has_valid_output and not full_output.strip():
            cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
            # 如果只有警告没有其他输出，可能是参数问题
            if warning_count > 0 and len(raw_lines) <= warning_count + 1:
                return AgentResult(
                    False,
                    f"⚠️ Agent 执行完成但没有有效输出。\n"
                    f"  命令: {cmd_str}\n"
                    f"  检测到 {warning_count} 个参数警告，这些参数可能不被当前版本支持。\n"
                    f"  完整输出: {raw_full[:500]}",
                    rc,
                    dur,
                    stats,
                    raw_full,
                )
            else:
                # 有其他输出但无法解析
                return AgentResult(
                    False,
                    f"⚠️ Agent 执行完成但没有可解析的输出。\n"
                    f"  命令: {cmd_str}\n"
                    f"  返回码: {rc}\n"
                    f"  完整输出: {raw_full[:500]}",
                    rc,
                    dur,
                    stats,
                    raw_full,
                )

        return AgentResult(
            success=(rc == 0 and has_valid_output),
            output=full_output,
            return_code=rc,
            duration=dur,
            stats=stats,
            raw_output=raw_full,
        )

    except subprocess.TimeoutExpired:
        proc.kill()
        error_msg = f"⏰ 超时 ({timeout}s)"
        if verbose:
            print(f"  ❌ {error_msg}")
        return AgentResult(False, error_msg, -1, time.time() - start, stats)
    except FileNotFoundError:
        error_msg = f"❌ 找不到 agent 命令: {agent_bin}\n  尝试设置环境变量 CURSOR_BIN 指定完整路径\n  例如: set CURSOR_BIN=agent.cmd 或 set CURSOR_BIN=C:\\path\\to\\agent.exe"
        if verbose:
            print(f"  ❌ {error_msg}")
        return AgentResult(False, error_msg, -2, time.time() - start, stats)
    except Exception as e:
        error_msg = f"❌ 调用 agent 时发生异常: {e}\n  命令: {' '.join(cmd)}"
        if verbose:
            print(f"  ❌ {error_msg}")
        return AgentResult(False, error_msg, -3, time.time() - start, stats)

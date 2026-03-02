"""
日志格式化器 — 将流式 JSON 转换为可读的对话格式

用于美化 scanner.log 中的输出，将原始的 stream-json 格式转换为易读的对话形式。
"""
import json


def format_stream_json_to_conversation(raw_json: str) -> str:
    """将流式 JSON 输出转换为可读的对话格式"""
    if not raw_json or not raw_json.strip():
        return ""

    lines: list[str] = []
    assistant_parts: list[str] = []
    tool_calls: list[str] = []

    _TOOL_ICONS = {
        "shellToolCall":      ("🔧", "command"),
        "editToolCall":       ("✏️ ", "filePath"),
        "writeToolCall":      ("📝", "filePath"),
        "createFileToolCall": ("📝", "filePath"),
        "readFileToolCall":   ("📖", "filePath"),
        "grepToolCall":       ("🔍", "pattern"),
        "globToolCall":       ("📂", "pattern"),
        "listDirToolCall":    ("📂", "dirPath"),
    }

    def _flush_tools():
        if not tool_calls:
            return
        lines.append(f"  ┌ 工具调用 ({len(tool_calls)})")
        for tc in tool_calls:
            lines.append(f"  │ {tc}")
        lines.append("  └")
        tool_calls.clear()

    def _flush_assistant():
        if not assistant_parts:
            return
        _flush_tools()
        text = "\n".join(assistant_parts).strip()
        if text:
            lines.append(f"\n💬 回复:\n{text}\n")
        assistant_parts.clear()

    for raw_line in raw_json.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            evt = json.loads(raw_line)
        except json.JSONDecodeError:
            if "Error:" in raw_line:
                lines.append(f"  ❌ {raw_line}")
            elif not raw_line.startswith("Warning:"):
                lines.append(raw_line)
            continue

        evt_type = evt.get("type", "")
        subtype = evt.get("subtype", "")

        if evt_type == "system" and subtype == "init":
            model = evt.get("model", "")
            sid = evt.get("session_id", "")
            parts = []
            if model:
                parts.append(f"模型: {model}")
            if sid:
                parts.append(f"会话: {sid[:12]}…")
            if parts:
                lines.append(f"🔧 {', '.join(parts)}")

        elif evt_type == "assistant":
            content = evt.get("message", {}).get("content", [])
            text = "".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
            if text:
                assistant_parts.append(text)

        elif evt_type == "tool_call" and subtype == "started":
            tc = evt.get("tool_call", {})
            for key, (icon, arg_name) in _TOOL_ICONS.items():
                if key in tc:
                    val = tc[key].get("args", {}).get(arg_name, "")
                    if val:
                        display = val if len(val) <= 80 else val[:77] + "…"
                        tool_calls.append(f"{icon} {display}")
                    break

        elif evt_type == "result":
            _flush_assistant()
            duration_ms = evt.get("duration_ms", 0)
            if duration_ms > 0:
                api_ms = evt.get("duration_api_ms", 0)
                extra = f" (API: {api_ms / 1000:.1f}s)" if api_ms else ""
                lines.append(f"⏱️  {duration_ms / 1000:.1f}s{extra}")

    _flush_assistant()

    result = "\n".join(lines)
    return result if result.strip() else ""


def format_conversation_log(conversation_log: list[dict]) -> str:
    """
    格式化完整的对话日志（多轮对话）
    
    Args:
        conversation_log: 对话日志列表，每个元素包含 round, timestamp, readable_output, raw_stream_json
    
    Returns:
        格式化的对话文本
    """
    if not conversation_log:
        return ""
    
    formatted_lines = []
    for entry in conversation_log:
        round_num = entry.get("round", 0)
        timestamp = entry.get("timestamp", "")
        raw_json = entry.get("raw_stream_json", "")
        
        # 格式化这一轮的对话
        formatted_lines.append(f"\n{'='*60}")
        formatted_lines.append(f"第 {round_num} 轮 - {timestamp}")
        formatted_lines.append(f"{'='*60}\n")
        
        # 如果有可读输出，先显示
        readable = entry.get("readable_output", "")
        if readable:
            formatted_lines.append(readable)
            formatted_lines.append("")
        
        # 格式化原始 JSON
        if raw_json:
            formatted = format_stream_json_to_conversation(raw_json)
            if formatted:
                formatted_lines.append(formatted)
    
    return "\n".join(formatted_lines)


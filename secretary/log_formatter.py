"""
日志格式化器 — 将流式 JSON 转换为可读的对话格式

用于美化 scanner.log 中的输出，将原始的 stream-json 格式转换为易读的对话形式。
"""
import json
import re
from typing import Optional


def format_stream_json_to_conversation(raw_json: str) -> str:
    """
    将流式 JSON 输出转换为可读的对话格式
    
    Args:
        raw_json: 原始的 stream-json 输出（多行 JSON，每行一个事件）
    
    Returns:
        格式化的对话文本
    """
    if not raw_json or not raw_json.strip():
        return ""
    
    lines = []
    current_assistant_text = []
    current_tool_calls = []
    
    for line in raw_json.splitlines():
        line = line.strip()
        if not line:
            continue
        
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            # 非 JSON 行，可能是错误信息或其他输出
            if line.startswith("Error:") or "Error:" in line:
                lines.append(f"❌ {line}")
            elif line.startswith("Warning:"):
                # 忽略警告
                continue
            else:
                # 其他输出原样保留
                lines.append(line)
            continue
        
        evt_type = evt.get("type", "")
        subtype = evt.get("subtype", "")
        
        # ---- system/init: 会话初始化 ----
        if evt_type == "system" and subtype == "init":
            session_id = evt.get("session_id", "")
            model = evt.get("model", "")
            if session_id or model:
                info = []
                if model:
                    info.append(f"模型: {model}")
                if session_id:
                    info.append(f"会话ID: {session_id[:16]}...")
                if info:
                    lines.append(f"🔧 初始化: {', '.join(info)}")
            continue
        
        # ---- assistant: 收集文本回复 ----
        if evt_type == "assistant":
            msg = evt.get("message", {})
            content = msg.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            text = "".join(texts).strip()
            if text:
                current_assistant_text.append(text)
            continue
        
        # ---- tool_call: 收集工具调用 ----
        if evt_type == "tool_call":
            if subtype == "started":
                tc = evt.get("tool_call", {})
                tool_info = None
                
                # Shell 命令
                if "shellToolCall" in tc:
                    cmd = tc["shellToolCall"].get("args", {}).get("command", "")
                    if cmd:
                        tool_info = f"🔧 执行命令: {cmd}"
                
                # 文件编辑
                elif "editToolCall" in tc:
                    fpath = tc["editToolCall"].get("args", {}).get("filePath", "")
                    if fpath:
                        tool_info = f"✏️  编辑文件: {fpath}"
                
                # 文件写入/创建
                elif "writeToolCall" in tc:
                    fpath = tc["writeToolCall"].get("args", {}).get("filePath", "")
                    if fpath:
                        tool_info = f"📝 写入文件: {fpath}"
                
                elif "createFileToolCall" in tc:
                    fpath = tc["createFileToolCall"].get("args", {}).get("filePath", "")
                    if fpath:
                        tool_info = f"📝 创建文件: {fpath}"
                
                # 文件读取
                elif "readFileToolCall" in tc:
                    fpath = tc["readFileToolCall"].get("args", {}).get("filePath", "")
                    if fpath:
                        tool_info = f"📖 读取文件: {fpath}"
                
                # 搜索
                elif "grepToolCall" in tc:
                    pattern = tc["grepToolCall"].get("args", {}).get("pattern", "")
                    if pattern:
                        tool_info = f"🔍 搜索: {pattern}"
                
                if tool_info:
                    current_tool_calls.append(tool_info)
            
            elif subtype == "completed":
                # 工具调用完成，已经在 started 时记录了
                pass
            continue
        
        # ---- result: 输出收集到的内容 ----
        if evt_type == "result":
            # 先输出工具调用
            if current_tool_calls:
                for tool_call in current_tool_calls:
                    lines.append(f"  {tool_call}")
                current_tool_calls = []
            
            # 再输出助手回复
            if current_assistant_text:
                assistant_text = "\n".join(current_assistant_text)
                lines.append(f"\n💬 助手回复:\n{assistant_text}\n")
                current_assistant_text = []
            
            # 输出统计信息
            duration_ms = evt.get("duration_ms", 0)
            duration_api_ms = evt.get("duration_api_ms", 0)
            if duration_ms > 0:
                duration_sec = duration_ms / 1000.0
                api_sec = duration_api_ms / 1000.0 if duration_api_ms > 0 else None
                if api_sec:
                    lines.append(f"⏱️  耗时: {duration_sec:.1f}s (API: {api_sec:.1f}s)")
                else:
                    lines.append(f"⏱️  耗时: {duration_sec:.1f}s")
            continue
        
        # ---- thinking: 忽略（太多 delta） ----
        if evt_type == "thinking":
            continue
        
        # ---- user: 忽略 ----
        if evt_type == "user":
            continue
    
    # 处理最后未输出的内容（即使没有 result 事件也要输出）
    if current_tool_calls:
        for tool_call in current_tool_calls:
            lines.append(f"  {tool_call}")
        current_tool_calls = []
    
    if current_assistant_text:
        assistant_text = "\n".join(current_assistant_text)
        lines.append(f"\n💬 助手回复:\n{assistant_text}\n")
        current_assistant_text = []
    
    result = "\n".join(lines)
    # 如果没有任何输出，返回空字符串
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


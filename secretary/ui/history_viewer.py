"""
对话历史查看器

用于格式化和显示 agent 的对话历史记录。
"""
import json
from pathlib import Path
from typing import Optional

import secretary.config as cfg
from secretary.log_formatter import format_stream_json_to_conversation


def get_all_conversations(agent_name: str) -> list[dict]:
    """
    获取指定 agent 的所有对话记录（从所有任务中合并）
    
    Args:
        agent_name: Agent 名称
        
    Returns:
        对话记录列表，按时间排序
    """
    stats_dir = cfg.AGENTS_DIR / agent_name / "stats"
    if not stats_dir.exists():
        return []
    
    all_conversations = []
    
    # 扫描所有 stats 文件
    stats_files = list(stats_dir.glob("*-stats.json"))
    for stats_file in stats_files:
        try:
            stats_data = load_stats_data(stats_file)
            if not stats_data:
                continue
            
            conversation_log = stats_data.get("conversation_log", [])
            # 为每条对话记录添加任务信息（用于区分）
            task_name = stats_data.get("task_name", stats_file.stem.replace("-stats", ""))
            for conv in conversation_log:
                conv_copy = conv.copy()
                conv_copy["task_name"] = task_name
                conv_copy["session_id"] = stats_data.get("session_id", "")
                all_conversations.append(conv_copy)
        except Exception:
            continue
    
    # 按时间戳排序
    all_conversations.sort(key=lambda x: x.get("timestamp", ""))
    
    return all_conversations


def get_latest_session_id(agent_name: str) -> Optional[str]:
    """
    获取指定 agent 的最新 session_id
    
    Args:
        agent_name: Agent 名称
        
    Returns:
        最新的 session_id，如果不存在则返回 None
    """
    stats_dir = cfg.AGENTS_DIR / agent_name / "stats"
    if not stats_dir.exists():
        return None
    
    stats_files = list(stats_dir.glob("*-stats.json"))
    if not stats_files:
        return None
    
    # 按修改时间排序，获取最新的
    latest_file = max(stats_files, key=lambda p: p.stat().st_mtime)
    stats_data = load_stats_data(latest_file)
    if stats_data:
        return stats_data.get("session_id", "")
    
    return None


def load_stats_data(stats_file: Path) -> Optional[dict]:
    """
    加载统计文件数据
    
    Args:
        stats_file: stats.json 文件路径
        
    Returns:
        统计数据字典，如果加载失败则返回 None
    """
    try:
        content = stats_file.read_text(encoding="utf-8")
        return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return None


def format_conversation_history(conversations: list[dict]) -> str:
    """
    格式化对话历史为易读的文本
    
    Args:
        conversations: 对话记录列表（从所有任务中合并）
        
    Returns:
        格式化后的对话历史文本
    """
    lines = []
    
    if not conversations:
        lines.append("📝 暂无对话记录")
        return "\n".join(lines)
    
    lines.append("=" * 80)
    lines.append(f"📋 Agent 对话历史 (共 {len(conversations)} 条记录)")
    lines.append("=" * 80)
    lines.append("")
    
    for i, conv_data in enumerate(conversations, 1):
        timestamp = conv_data.get("timestamp", "")
        readable_output = conv_data.get("readable_output", "")
        raw_output = conv_data.get("raw_output", conv_data.get("raw_stream_json", ""))
        task_name = conv_data.get("task_name", "")
        
        # 轮次标题
        lines.append("-" * 80)
        if task_name:
            lines.append(f"第 {i} 条 | 任务: {task_name}" + (f" | {timestamp}" if timestamp else ""))
        else:
            lines.append(f"第 {i} 条" + (f" | {timestamp}" if timestamp else ""))
        lines.append("-" * 80)
        lines.append("")
        
        # 如果有可读输出，优先使用
        if readable_output:
            lines.append("💬 Agent 回复:")
            lines.append("")
            # 按行分割，添加缩进
            for line in readable_output.splitlines():
                lines.append(f"  {line}")
            lines.append("")
        elif raw_output:
            # 否则解析原始输出
            formatted = format_stream_json_to_conversation(raw_output)
            if formatted:
                lines.append("💬 Agent 回复:")
                lines.append("")
                for line in formatted.splitlines():
                    lines.append(f"  {line}")
                lines.append("")
        
        # 工具调用信息
        files_edited = conv_data.get("files_edited", [])
        files_created = conv_data.get("files_created", [])
        shell_commands = conv_data.get("shell_commands", [])
        
        if files_edited or files_created or shell_commands:
            lines.append("🔧 工具调用:")
            if files_edited:
                for f in files_edited:
                    lines.append(f"  ✏️  编辑: {f}")
            if files_created:
                for f in files_created:
                    lines.append(f"  📄 创建: {f}")
            if shell_commands:
                for cmd in shell_commands:
                    lines.append(f"  💻 执行: {cmd}")
            lines.append("")
        
        lines.append("")
    
    return "\n".join(lines)


def display_history(agent_name: str) -> bool:
    """
    显示指定 agent 的完整对话历史（从所有任务中合并）
    
    Args:
        agent_name: Agent 名称
        
    Returns:
        是否成功显示历史
    """
    # 获取所有对话记录
    conversations = get_all_conversations(agent_name)
    
    if not conversations:
        print(f"❌ Agent '{agent_name}' 没有找到任何对话记录")
        return False
    
    # 格式化并显示
    formatted = format_conversation_history(conversations)
    print(formatted)
    
    return True


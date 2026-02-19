"""
CLI 输出多语言 (en/zh) 支持

用法:
  from secretary.i18n import t, get_language
  print(t("status_title"))   # 根据当前 language 返回中文或英文
  get_language()             # "en" | "zh"

扩展: 在 MESSAGES 中增加 key -> {"zh": "...", "en": "..."} 即可支持新字符串。
"""
from secretary.settings import get_language

# key -> { "zh": "中文", "en": "English" }
MESSAGES = {
    # status 命令
    "status_title": {"zh": "系统状态", "en": "System Status"},
    "status_workspace": {"zh": "工作区", "en": "Workspace"},
    "status_pending": {"zh": "待处理 (所有 worker)", "en": "Pending (all workers)"},
    "status_ongoing": {"zh": "执行中 (所有 worker)", "en": "Ongoing (all workers)"},
    "status_reports": {"zh": "待审查 (report/)", "en": "Pending review (report/)"},
    "status_stats": {"zh": "统计 (stats/)", "en": "Stats (stats/)"},
    "status_solved": {"zh": "已解决 (solved-report/)", "en": "Solved (solved-report/)"},
    "status_unsolved": {"zh": "未解决 (unsolved-report/)", "en": "Unsolved (unsolved-report/)"},
    "status_testcases": {"zh": "测试样例 (testcases/)", "en": "Test cases (testcases/)"},
    "status_workers": {"zh": "工人", "en": "Workers"},
    "status_skills": {"zh": "技能 (skills/)", "en": "Skills (skills/)"},
    "status_logs": {"zh": "日志 (logs/)", "en": "Logs (logs/)"},
    "status_count": {"zh": "个", "en": ""},
    "status_completed": {"zh": "完成", "en": "completed"},
    "status_pending_count": {"zh": "待处理", "en": "pending"},
    "status_ongoing_count": {"zh": "执行中", "en": "ongoing"},
    "status_summary": {"zh": "统计汇总", "en": "Summary"},
    "status_tips_workers": {"zh": "工人", "en": "Workers"},
    "status_tips_skills": {"zh": "技能", "en": "Skills"},
    "status_tips_services": {"zh": "后台服务", "en": "Services"},
    "status_tips_settings": {"zh": "设置", "en": "Settings"},
    "status_tips_cleanup": {"zh": "清理", "en": "Cleanup"},
    # help 主标题
    "help_banner": {"zh": "基于 Agent 的自动化任务系统", "en": "Agent-based task automation"},
    "help_quick_start": {"zh": "快速开始", "en": "Quick start"},
    "help_set_workspace": {"zh": "设定工作区", "en": "Set workspace"},
    "help_submit_task": {"zh": "提交任务", "en": "Submit task"},
    "help_start_worker": {"zh": "启动worker", "en": "Start worker"},
    "help_view_status": {"zh": "查看状态", "en": "View status"},
    "help_command_list": {"zh": "命令列表", "en": "Commands"},
    "help_tips": {"zh": "使用提示", "en": "Tips"},
    "help_more": {"zh": "更多信息", "en": "More"},
}


def t(key: str) -> str:
    """根据当前 language 返回 key 对应的 zh 或 en 文案；未知 key 返回 key 本身。"""
    lang = get_language()
    if key not in MESSAGES:
        return key
    return MESSAGES[key].get(lang) or MESSAGES[key].get("zh") or key

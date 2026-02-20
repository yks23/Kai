"""
UI 模块 — 统一的用户界面组件

包含监控面板、报告查看器等 UI 功能。
"""

from secretary.ui.dashboard import run_monitor, print_status_line
from secretary.ui.report_viewer import run_interactive_report

__all__ = [
    "run_monitor",
    "print_status_line",
    "run_interactive_report",
]


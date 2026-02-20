"""
提示词构建器 — 统一管理所有 agent 的提示词构建逻辑

提示词构建函数按角色分布在 secretary.agent_types 各模块中。
本文件重新导出，供向后兼容及统一入口使用。
"""
from pathlib import Path

# 从 agent_types 各模块重新导出
from secretary.agent_types.worker import (
    build_first_round_prompt as build_worker_first_round_prompt,
    build_continue_prompt as build_worker_continue_prompt,
    build_refine_prompt as build_worker_refine_prompt,
)
from secretary.agent_types.boss import build_boss_prompt
from secretary.agent_types.recycler import build_recycler_prompt
from secretary.agent_types.secretary import build_secretary_prompt

__all__ = [
    "build_worker_first_round_prompt",
    "build_worker_continue_prompt",
    "build_worker_refine_prompt",
    "build_boss_prompt",
    "build_recycler_prompt",
    "build_secretary_prompt",
]

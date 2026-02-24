"""
Agent 路径管理 — 统一的路径辅助函数

提供所有 agent 相关的路径访问接口，统一管理，便于维护和复用。
"""
from pathlib import Path

import secretary.config as cfg


class AgentPaths:
    """
    Agent 路径管理类
    
    提供统一的路径访问接口，避免在代码中重复定义路径逻辑。
    """
    
    def __init__(self, agent_name: str):
        """
        初始化 Agent 路径管理器
        
        Args:
            agent_name: agent 名称
        """
        self.agent_name = agent_name
        self.base_dir = cfg.AGENTS_DIR / agent_name
    
    @property
    def input_dir(self) -> Path:
        """input 目录（tasks/）"""
        return self.base_dir / "tasks"
    
    @property
    def processing_dir(self) -> Path:
        """processing 目录（ongoing/）"""
        return self.base_dir / "ongoing"
    
    @property
    def output_dir(self) -> Path:
        """output 目录（reports/）"""
        return self.base_dir / "reports"
    
    # 向后兼容的属性
    @property
    def tasks_dir(self) -> Path:
        """向后兼容：tasks_dir 指向 input_dir"""
        return self.input_dir
    
    @property
    def ongoing_dir(self) -> Path:
        """向后兼容：ongoing_dir 指向 processing_dir"""
        return self.processing_dir
    
    @property
    def assigned_dir(self) -> Path:
        """assigned/ 目录（secretary 类型使用）"""
        return self.base_dir / "assigned"
    
    @property
    def reports_dir(self) -> Path:
        """向后兼容：reports_dir 指向 output_dir"""
        return self.output_dir
    
    @property
    def logs_dir(self) -> Path:
        """logs/ 目录"""
        return self.base_dir / "logs"
    
    @property
    def stats_dir(self) -> Path:
        """stats/ 目录"""
        return self.base_dir / "stats"
    
    @property
    def memory_file(self) -> Path:
        """memory.md 文件"""
        return self.base_dir / "memory.md"
    
    @property
    def goals_file(self) -> Path:
        """goals.md 文件"""
        return self.base_dir / "goals.md"
    
    @property
    def config_file(self) -> Path:
        """config.md 文件（boss 类型使用）"""
        return self.base_dir / "config.md"
    
    @property
    def goal_file(self) -> Path:
        """goal.md 文件（boss 类型使用）"""
        return self.base_dir / "goal.md"


# ============================================================
#  向后兼容的函数接口（保持原有 API）
# ============================================================

def _worker_dir(worker_name: str) -> Path:
    """获取 worker 的基础目录：agents/<name>"""
    return cfg.AGENTS_DIR / worker_name


def _worker_tasks_dir(worker_name: str) -> Path:
    """获取 worker 的 tasks 目录：agents/<name>/tasks"""
    return AgentPaths(worker_name).input_dir


def _worker_assigned_dir(worker_name: str) -> Path:
    """获取 agent 的 assigned 目录：agents/<name>/assigned（秘书类型使用）"""
    return AgentPaths(worker_name).assigned_dir


def _worker_ongoing_dir(worker_name: str) -> Path:
    """获取 worker 的 ongoing 目录：agents/<name>/ongoing"""
    return AgentPaths(worker_name).processing_dir


def _worker_logs_dir(worker_name: str) -> Path:
    """获取 worker 的 logs 目录路径：agents/<name>/logs"""
    return AgentPaths(worker_name).logs_dir


def _worker_stats_dir(worker_name: str) -> Path:
    """获取 worker 的 stats 目录路径：agents/<name>/stats"""
    return AgentPaths(worker_name).stats_dir


def _worker_reports_dir(worker_name: str) -> Path:
    """获取 worker 的 reports 目录路径：agents/<name>/reports"""
    return AgentPaths(worker_name).output_dir


def _worker_memory_file(worker_name: str) -> Path:
    """获取 worker 的 memory.md 文件路径"""
    return AgentPaths(worker_name).memory_file


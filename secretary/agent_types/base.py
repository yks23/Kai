"""
Agent 类型基类

定义所有 agent 类型的通用接口和基础功能
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from secretary.agent_config import AgentConfig, TriggerConfig


class AgentType(ABC):
    """Agent 类型基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 类型名称"""
        pass
    
    @property
    @abstractmethod
    def label_template(self) -> str:
        """标签模板，例如 '👷 {name}'"""
        pass
    
    @property
    @abstractmethod
    def prompt_template(self) -> str:
        """首轮提示词模板文件名"""
        pass
    
    @abstractmethod
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """构建该类型的 AgentConfig"""
        pass
    
    @abstractmethod
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """处理任务文件"""
        pass


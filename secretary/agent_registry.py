"""
Agent 类型注册表系统

支持自动发现和注册 agent 类型，包括内置类型和用户自定义类型。
"""
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from secretary.agent_types.base import AgentType


class AgentTypeRegistry:
    """Agent 类型注册表，管理所有已注册的 agent 类型"""
    
    _types: Dict[str, AgentType] = {}
    _type_classes: Dict[str, Type[AgentType]] = {}
    
    @classmethod
    def register(cls, type_name: str, agent_type: AgentType) -> None:
        """
        注册 agent 类型实例
        
        Args:
            type_name: 类型名称（如 "worker", "secretary"）
            agent_type: AgentType 实例
        """
        if type_name in cls._types:
            # 已存在，可以选择覆盖或跳过
            # 这里选择覆盖，允许重新注册
            pass
        cls._types[type_name] = agent_type
    
    @classmethod
    def register_class(cls, type_name: str, agent_class: Type[AgentType]) -> None:
        """
        注册 agent 类型类（延迟实例化）
        
        Args:
            type_name: 类型名称
            agent_class: AgentType 子类
        """
        cls._type_classes[type_name] = agent_class
        # 同时创建实例并注册
        try:
            instance = agent_class()
            cls.register(type_name, instance)
        except Exception as e:
            # 如果实例化失败，至少保存类，后续可以重试
            pass
    
    @classmethod
    def get(cls, type_name: str) -> Optional[AgentType]:
        """
        获取 agent 类型实例
        
        Args:
            type_name: 类型名称
            
        Returns:
            AgentType 实例，如果不存在则返回 None
        """
        return cls._types.get(type_name)
    
    @classmethod
    def get_class(cls, type_name: str) -> Optional[Type[AgentType]]:
        """
        获取 agent 类型类
        
        Args:
            type_name: 类型名称
            
        Returns:
            AgentType 子类，如果不存在则返回 None
        """
        return cls._type_classes.get(type_name)
    
    @classmethod
    def list_types(cls) -> List[str]:
        """
        列出所有已注册的类型名称
        
        Returns:
            类型名称列表
        """
        return list(cls._types.keys())
    
    @classmethod
    def has_type(cls, type_name: str) -> bool:
        """
        检查类型是否已注册
        
        Args:
            type_name: 类型名称
            
        Returns:
            如果已注册返回 True，否则返回 False
        """
        return type_name in cls._types
    
    @classmethod
    def discover_from_directory(cls, directory: Path) -> List[str]:
        """
        从目录自动发现并注册 agent 类型
        
        扫描目录中的所有 .py 文件，查找继承 AgentType 的类并自动注册。
        
        Args:
            directory: 要扫描的目录路径
            
        Returns:
            发现的类型名称列表
        """
        if not directory.exists() or not directory.is_dir():
            return []
        
        discovered_types = []
        
        # 扫描所有 .py 文件
        for py_file in directory.glob("*.py"):
            # 跳过 __init__.py
            if py_file.name == "__init__.py":
                continue
            
            try:
                # 动态导入模块
                module_name = py_file.stem
                spec = importlib.util.spec_from_file_location(
                    f"custom_agent_{module_name}", py_file
                )
                if spec is None or spec.loader is None:
                    continue
                
                module = importlib.util.module_from_spec(spec)
                # 将模块添加到 sys.modules，避免重复导入
                full_module_name = f"secretary.custom_agents.{module_name}"
                sys.modules[full_module_name] = module
                spec.loader.exec_module(module)
                
                # 查找所有 AgentType 子类
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # 检查是否是 AgentType 的子类，且不是 AgentType 本身
                    if (issubclass(obj, AgentType) and 
                        obj is not AgentType and
                        obj.__module__ == module.__name__):
                        try:
                            # 创建实例并获取类型名称
                            instance = obj()
                            type_name = instance.name
                            
                            # 注册类型
                            cls.register_class(type_name, obj)
                            discovered_types.append(type_name)
                        except Exception as e:
                            # 如果实例化失败，记录但继续
                            import traceback
                            print(f"⚠️  发现 agent 类型 {name} 但实例化失败: {e}")
                            traceback.print_exc()
                            continue
            except Exception as e:
                # 导入失败，记录但继续处理其他文件
                import traceback
                print(f"⚠️  加载 {py_file.name} 失败: {e}")
                traceback.print_exc()
                continue
        
        return discovered_types
    
    @classmethod
    def _load_builtin_types(cls) -> None:
        """加载包内的内置类型（worker, secretary, boss, recycler）"""
        try:
            from secretary.agent_types import (
                WorkerAgent, SecretaryAgent, BossAgent, RecyclerAgent
            )
            
            # 注册内置类型
            builtin_types = [
                ("worker", WorkerAgent),
                ("secretary", SecretaryAgent),
                ("boss", BossAgent),
                ("recycler", RecyclerAgent),
            ]
            
            for type_name, agent_class in builtin_types:
                try:
                    instance = agent_class()
                    cls.register(type_name, instance)
                    cls._type_classes[type_name] = agent_class
                except Exception as e:
                    import traceback
                    print(f"⚠️  加载内置类型 {type_name} 失败: {e}")
                    traceback.print_exc()
        except Exception as e:
            import traceback
            print(f"⚠️  加载内置类型失败: {e}")
            traceback.print_exc()
    
    @classmethod
    def _load_custom_types(cls, custom_dir: Path) -> List[str]:
        """
        加载自定义类型
        
        Args:
            custom_dir: 自定义 agent 类型目录
            
        Returns:
            发现的类型名称列表
        """
        if not custom_dir.exists():
            return []
        
        return cls.discover_from_directory(custom_dir)
    
    @classmethod
    def initialize(cls, custom_agents_dir: Optional[Path] = None) -> None:
        """
        初始化注册表，加载所有类型
        
        Args:
            custom_agents_dir: 自定义 agent 类型目录，如果为 None 则使用默认路径
        """
        # 先加载内置类型
        cls._load_builtin_types()
        
        # 加载自定义类型
        if custom_agents_dir is None:
            # 使用默认路径
            try:
                import secretary.config as cfg
                custom_agents_dir = cfg.BASE_DIR / "custom_agents"
            except Exception:
                pass
        
        if custom_agents_dir:
            discovered = cls._load_custom_types(custom_agents_dir)
            if discovered:
                print(f"✅ 发现 {len(discovered)} 个自定义 agent 类型: {', '.join(discovered)}")


# 全局注册表实例
_registry = AgentTypeRegistry

# 便捷函数
def get_agent_type(type_name: str) -> Optional[AgentType]:
    """获取 agent 类型实例"""
    return _registry.get(type_name)


def register_agent_type(type_name: str, agent_type: AgentType) -> None:
    """注册 agent 类型"""
    _registry.register(type_name, agent_type)


def list_agent_types() -> List[str]:
    """列出所有已注册的类型名称"""
    return _registry.list_types()


def has_agent_type(type_name: str) -> bool:
    """检查类型是否已注册"""
    return _registry.has_type(type_name)


def initialize_registry(custom_agents_dir: Optional[Path] = None) -> None:
    """初始化注册表"""
    _registry.initialize(custom_agents_dir)


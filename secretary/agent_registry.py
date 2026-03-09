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
        except Exception:
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


# ─────────────────────────────────────────────────────────────
#  CustomAgentType — 从 agents.json 存储的自定义类型动态创建
# ─────────────────────────────────────────────────────────────

class CustomAgentType(AgentType):
    """
    用户自定义 agent 类型。

    目录结构和触发规则继承自 base_type (worker/secretary/boss/recycler)，
    提示词使用用户上传的 markdown 内容。
    """

    def __init__(self, type_info: dict):
        self._type_info = type_info
        self._name = type_info["name"]
        self._base_type_name = type_info.get("base_type", "worker")
        self._first_prompt_tpl = type_info.get("first_prompt", "")
        self._continue_prompt_tpl = type_info.get("continue_prompt", "")
        self._description = type_info.get("description", "")

    @property
    def name(self) -> str:
        return self._name

    @property
    def label_template(self) -> str:
        return "🔧 {name}"

    @property
    def prompt_template(self) -> str:
        return ""  # Not used — prompts come from stored content

    def _base_agent_type(self) -> AgentType:
        base = _registry.get(self._base_type_name)
        if not base:
            # Lazy-init if registry hasn't been loaded yet
            _registry._load_builtin_types()
            base = _registry.get(self._base_type_name) or _registry.get("worker")
        return base  # type: ignore

    def build_config(self, base_dir: Path, agent_name: str) -> "AgentConfig":
        """委托给 base_type 构建相同的目录结构 / 触发配置"""
        return self._base_agent_type().build_config(base_dir, agent_name)

    # ── 提示词构建 ──

    def _build_first(self, task_file: Path, config: "AgentConfig") -> str:
        from secretary.agents import build_known_agents_section
        tpl = self._first_prompt_tpl
        # 支持模板变量 — 与内置类型一致
        mapping = {
            "base_dir": str(config.base_dir.parent.parent if config.base_dir else ""),
            "task_file": str(task_file),
            "report_dir": str(config.output_dir) if config.output_dir else "",
            "report_filename": task_file.stem + "-report.md",
            "known_agents_section": build_known_agents_section(config.name),
        }
        try:
            return tpl.format_map(mapping)
        except (KeyError, IndexError):
            return tpl  # 如果模板变量不匹配，返回原始内容

    def _build_continue(self, task_file: Path, config: "AgentConfig") -> str:
        from secretary.agents import build_known_agents_section, known_agents_changed
        ka = build_known_agents_section(config.name) if known_agents_changed(config.name) else ""
        tpl = self._continue_prompt_tpl
        mapping = {
            "task_file": str(task_file),
            "report_dir": str(config.output_dir) if config.output_dir else "",
            "known_agents_section": ka,
        }
        try:
            return tpl.format_map(mapping)
        except (KeyError, IndexError):
            return tpl

    def process_task(self, config: "AgentConfig", task_file: Path, verbose: bool = True) -> None:
        from datetime import datetime as _dt
        from secretary.agent_types.base import prepare_dialog_file, run_agent_with_session

        ts = _dt.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] ▶ {task_file.name} ({config.name}) [custom:{self._name}]")

        dialog_file = prepare_dialog_file(config, task_file.stem)
        first = self._build_first(task_file, config)
        cont = self._build_continue(task_file, config)
        try:
            result = run_agent_with_session(
                config.name, first, cont,
                dialog_file=dialog_file, verbose=verbose,
            )
            ts = _dt.now().strftime("%H:%M:%S")
            status = "✅" if result.success else "❌"
            print(f"[{ts}] {status} {task_file.name} 完成 ({result.duration:.1f}s)")
        except Exception as e:
            import traceback
            ts = _dt.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] ❌ {task_file.name}: {e}")
            traceback.print_exc()


def _load_custom_types_from_registry():
    """从 agents.json 的 custom_types 段加载并注册所有自定义类型"""
    try:
        from secretary.agents import list_custom_types
        for ct in list_custom_types():
            instance = CustomAgentType(ct)
            _registry.register(ct["name"], instance)
    except Exception:
        pass


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
    # 也加载 agents.json 中存储的自定义类型
    _load_custom_types_from_registry()


def resolve_agent_type(agent_name: str) -> AgentType:
    """
    根据 agent 名称解析对应的 AgentType 实例。

    查找顺序：
      1. 从 agents.json 注册信息中获取 type 字段 → 注册表查找
      2. 如果是自定义类型但未加载，动态加载
      3. 回退到 "worker" 类型
      4. 都失败则抛出 ValueError
    """
    from secretary.agents import get_worker, get_custom_type

    worker_info = get_worker(agent_name)
    if worker_info and worker_info.get("type"):
        type_name = worker_info["type"]
        agent_type = _registry.get(type_name)
        if agent_type:
            return agent_type
        # 尝试动态加载自定义类型
        ct = get_custom_type(type_name)
        if ct:
            instance = CustomAgentType(ct)
            _registry.register(type_name, instance)
            return instance

    default = _registry.get("worker")
    if default:
        return default

    raise ValueError(f"无法解析 agent 类型: {agent_name}, 可用: {', '.join(_registry.list_types())}")


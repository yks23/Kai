"""
示例：自定义 Agent 类型

这是一个完整的示例，展示如何创建一个自定义 agent 类型。
这个示例创建了一个"审查者"agent，用于审查代码和文档。
"""
from pathlib import Path
from typing import List

from secretary.agent_types.base import AgentType
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_loop import load_prompt
from secretary.agent_runner import run_agent


class ReviewerAgent(AgentType):
    """
    审查者 Agent - 审查代码和文档
    
    特点：
    - 触发规则：tasks/ 目录有文件时触发
    - 终止条件：直到 ongoing/ 中的任务文件被删除
    - 处理逻辑：读取任务，调用 Agent 审查，生成报告
    """
    
    @property
    def name(self) -> str:
        """Agent 类型名称"""
        return "reviewer"
    
    @property
    def label_template(self) -> str:
        """标签模板"""
        return "🔍 {name}"
    
    @property
    def prompt_template(self) -> str:
        """首轮提示词模板文件名"""
        return "reviewer.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """
        构建 Reviewer 的配置
        
        Args:
            base_dir: 基础目录（通常是 BASE_DIR）
            agent_name: agent 名称
            
        Returns:
            AgentConfig 实例
        """
        reviewer_dir = base_dir / "agents" / agent_name
        
        return AgentConfig(
            name=agent_name,
            base_dir=reviewer_dir,
            # 统一的目录结构
            input_dir=reviewer_dir / "tasks",
            processing_dir=reviewer_dir / "ongoing",
            output_dir=reviewer_dir / "reports",
            # 其他目录
            logs_dir=reviewer_dir / "logs",
            stats_dir=reviewer_dir / "stats",
            # 触发配置：tasks/ 目录有文件时触发
            trigger=TriggerConfig(
                watch_dirs=[reviewer_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            # 终止条件：直到任务文件被删除
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            # 提示词模板
            first_round_prompt="reviewer.md",
            # 需要 ongoing 目录
            use_ongoing=True,
            # 日志文件
            log_file=reviewer_dir / "logs" / "scanner.log",
            # 标签
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理审查任务
        
        流程：
        1. 将任务文件从 tasks/ 移动到 ongoing/
        2. 读取任务内容
        3. 构建提示词
        4. 调用 Agent 进行审查
        5. Agent 完成后删除 ongoing/ 中的文件
        
        Args:
            config: Agent 配置
            task_file: 任务文件路径
            verbose: 是否显示详细信息
        """
        import shutil
        from datetime import datetime
        import traceback
        
        # 确保 processing 目录存在
        config.processing_dir.mkdir(parents=True, exist_ok=True)
        
        # 将任务文件移动到 processing 目录
        ongoing_file = config.processing_dir / task_file.name
        try:
            if task_file.exists():
                shutil.move(str(task_file), str(ongoing_file))
                if verbose:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"\n[{ts}] 📦 任务文件已移动到 processing/: {ongoing_file.name}")
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 移动任务文件到 processing/ 失败: {task_file.name} | 错误: {e}")
            traceback.print_exc()
            return
        
        # 读取任务内容
        try:
            task_content = ongoing_file.read_text(encoding="utf-8")
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 读取任务文件失败: {e}")
            traceback.print_exc()
            return
        
        # 构建提示词
        try:
            template = load_prompt("reviewer.md")
            prompt = template.format(
                task_content=task_content,
                report_dir=config.output_dir,
                report_filename=ongoing_file.stem + "-report.md",
            )
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 加载提示词模板失败: {e}")
            traceback.print_exc()
            return
        
        # 调用 Agent
        try:
            import secretary.config as cfg
            result = run_agent(
                prompt=prompt,
                workspace=str(cfg.WORKSPACE),
                verbose=verbose,
            )
            
            if not result.success:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{ts}] ⚠️ Agent 审查失败 (code={result.return_code})")
                print(f"   错误信息: {result.output[:200]}")
        except Exception as e:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{ts}] ❌ 调用 Agent 失败: {e}")
            traceback.print_exc()
        
        # 注意：Agent 应该自己删除 ongoing 文件表示任务完成
        # 如果 Agent 没有删除，这里可以选择保留或删除
        # 对于多轮对话的场景，应该等待 Agent 删除文件


"""
交互式聊天界面

用于与 agent 进行实时对话，使用 session_id 续轮。
"""
from pathlib import Path

import secretary.config as cfg
from secretary.agent_runner import run_agent, AgentResult
from secretary.settings import get_model
from secretary.ui.history_viewer import (
    get_all_conversations, get_latest_session_id
)


class ChatInterface:
    """交互式聊天界面"""
    
    def __init__(self, agent_name: str, session_id: str):
        """
        初始化聊天界面
        
        Args:
            agent_name: Agent 名称
            session_id: 会话ID
        """
        self.agent_name = agent_name
        self.session_id = session_id
        self.conversations = None
        
        # 加载历史数据（用于显示）
        self.conversations = get_all_conversations(agent_name)
    
    def display_history(self):
        """显示历史对话"""
        if not self.conversations:
            print("📝 暂无历史对话记录")
            return
        
        print("\n" + "=" * 80)
        print("📋 历史对话")
        print("=" * 80)
        print()
        
        # 只显示最近的几条对话（避免太长）
        recent_conversations = self.conversations[-10:]  # 显示最近10条
        
        for i, conv_data in enumerate(recent_conversations, 1):
            readable_output = conv_data.get("readable_output", "")
            timestamp = conv_data.get("timestamp", "")
            
            print(f"--- 第 {len(self.conversations) - len(recent_conversations) + i} 条" + (f" | {timestamp}" if timestamp else "") + " ---")
            if readable_output:
                # 显示可读输出（简化版）
                preview = readable_output[:300]
                if len(readable_output) > 300:
                    preview += "..."
                print(preview)
            print()
        
        if len(self.conversations) > 10:
            print(f"... (还有 {len(self.conversations) - 10} 条更早的对话)")
            print()
        
        print("=" * 80)
        print()
    
    def send_message(self, message: str) -> AgentResult:
        """
        发送消息给 agent
        
        Args:
            message: 用户输入的消息
            
        Returns:
            Agent 执行结果
        """
        # 获取 agent 类型并构建续轮提示词
        from secretary.agents import get_worker
        from secretary.agent_types import get_agent_type
        from secretary.prompt_builder import PromptContext
        
        worker = get_worker(self.agent_name)
        if not worker:
            # 如果找不到 agent，使用默认 worker 类型
            agent_type_name = "worker"
        else:
            agent_type_name = worker.get("type", "worker")
        
        # 获取 agent 类型
        agent_type = get_agent_type(agent_type_name)
        if not agent_type:
            # 回退到 worker
            from secretary.agent_types import WorkerAgent
            agent_type = WorkerAgent()
        
        # 构建配置（用于获取目录信息）
        config = agent_type.build_config(cfg.BASE_DIR, self.agent_name)
        
        # 构建续轮提示词上下文
        # 创建一个临时文件来存储用户输入
        from tempfile import NamedTemporaryFile
        import os
        with NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(message)
            temp_file = Path(f.name)
        
        try:
            context = PromptContext(
                agent_name=self.agent_name,
                agent_type=agent_type_name,
                base_dir=cfg.BASE_DIR,
                task_file=temp_file,
                task_content=message,
                output_dir=config.output_dir,
                report_dir=config.output_dir,
            )
            
            # 使用 agent 类型的 build_prompt 方法构建续轮提示词
            prompt = agent_type.build_prompt(context, is_first_round=False)
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_file)
            except Exception:
                pass
        
        # 执行 agent（续轮模式）
        result = run_agent(
            prompt=prompt,
            workspace=str(cfg.get_workspace()),
            model=get_model(),
            verbose=True,
            continue_session=False,  # 使用 session_id 而不是 continue_session
            session_id=self.session_id,
            timeout=None,
        )
        
        # 更新 session_id（如果返回了新的）
        if result.stats.session_id:
            self.session_id = result.stats.session_id
        
        return result
    
    def run(self):
        """运行交互循环"""
        # 显示历史
        self.display_history()
        
        print("💬 聊天模式已启动")
        print(f"   Agent: {self.agent_name}")
        print(f"   Session ID: {self.session_id[:16]}..." if self.session_id else "   Session ID: (无)")
        print("   输入消息后按 Enter 发送")
        print("   输入 'q' 或 'quit' 退出")
        print()
        
        while True:
            try:
                # 读取用户输入
                print("> ", end="", flush=True)
                user_input = input().strip()
                
                # 检查退出命令
                if user_input.lower() in ['q', 'quit', 'exit']:
                    print("\n👋 退出聊天模式")
                    break
                
                if not user_input:
                    continue
                
                # 发送消息
                print()
                print("🔄 正在发送消息...")
                print()
                
                result = self.send_message(user_input)
                
                # 显示回复
                print()
                print("=" * 80)
                print("💬 Agent 回复:")
                print("=" * 80)
                print()
                
                if result.success:
                    if result.output:
                        # 显示可读输出
                        for line in result.output.splitlines():
                            print(f"  {line}")
                    else:
                        print("  (无文本回复)")
                else:
                    print(f"  ❌ 错误: {result.output[:200]}")
                
                print()
                print("=" * 80)
                print()
                
            except KeyboardInterrupt:
                print("\n\n👋 退出聊天模式")
                break
            except EOFError:
                print("\n\n👋 退出聊天模式")
                break
            except Exception as e:
                print(f"\n❌ 发生错误: {e}")
                import traceback
                traceback.print_exc()
                print()


def start_chat(agent_name: str) -> bool:
    """
    启动聊天界面
    
    Args:
        agent_name: Agent 名称
        
    Returns:
        是否成功启动聊天
    """
    # 获取最新的 session_id
    session_id = get_latest_session_id(agent_name)
    
    if not session_id:
        print(f"❌ Agent '{agent_name}' 没有找到任何 session_id，无法进行续轮对话")
        print("   提示: 只有已完成或进行中的任务才有 session_id")
        return False
    
    # 启动聊天界面
    chat = ChatInterface(agent_name, session_id)
    chat.run()
    
    return True


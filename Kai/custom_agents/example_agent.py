"""
示例：自定义 Agent 类型

只需几行属性即可定义一个新的 agent 类型。
对应的提示词模板放在 Kai/custom_prompts/ 目录下。

hire 时通过 dep_names 关联其他 agent：
  kai hire myreviewer reviewer worker1 worker2
  → myreviewer 的提示词自动包含 worker1/worker2 的信息和调用方式

模板中用 {known_agents_section} 引用关联 agent 的列表。
"""
from secretary.agent_types.base import AgentType


class ReviewerAgent(AgentType):
    name = "reviewer"
    icon = "🔍"
    first_prompt = "reviewer.md"
    continue_prompt = "reviewer_continue.md"

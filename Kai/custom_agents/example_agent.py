"""
示例：自定义 Agent 类型

只需 4 行属性即可定义一个新的 agent 类型。
对应的提示词模板放在 Kai/custom_prompts/ 目录下。

用法:
  kai hire myreviewer reviewer
  kai task "审查 README.md" --agent myreviewer
"""
from secretary.agent_types.base import AgentType


class ReviewerAgent(AgentType):
    name = "reviewer"
    icon = "🔍"
    first_prompt = "reviewer.md"
    continue_prompt = "reviewer_continue.md"

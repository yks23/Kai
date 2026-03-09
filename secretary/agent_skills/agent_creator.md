# 技能: Agent创建者

> 允许 agent 根据用户需求生成自定义的智能体模板，并注册到系统中

## 技能说明

你可以根据用户需求，生成自定义的智能体类型模板，包括首轮提示词和续轮提示词，并注册到系统中供后续使用。

### 自定义类型存储位置
自定义类型会注册到 `agents.json` 的 `custom_types` 字段中

### 使用方法
在 Python 代码中调用：
```python
from secretary.command_system import write_command_file

# 1. 创建自定义 agent 类型
type_name = "my-custom-type"  # 类型名称（建议使用小写字母和连字符）
base_type = "worker"  # 基础类型：worker, secretary, boss, recycler
description = "这个自定义类型的描述"

# 首轮提示词内容
first_prompt_content = """# 自定义 Agent 首轮提示词

你是 {agent_name}，负责...

## 工作环境
- 工作区: {base_dir}
- 报告目录: {report_dir}

## 任务文件
{task_file}

{known_agents_section}

{skills_section}

## 工作流程
1. ...
2. ...
"""

# 续轮提示词内容
continue_prompt_content = """# 续轮提示词

继续处理任务：{task_file}

{known_agents_section}

请继续完成工作。
"""

# 2. 通过命令系统注册自定义类型
write_command_file("你的名字", "create_custom_type", {
    "name": type_name,
    "base_type": base_type,
    "description": description,
    "first_prompt": first_prompt_content,
    "continue_prompt": continue_prompt_content
})
```

### 提示词模板变量
在自定义提示词中可以使用以下变量：
- `{agent_name}` - Agent 名称
- `{base_dir}` - 基础目录路径
- `{task_file}` - 任务文件路径
- `{report_dir}` - 报告目录路径
- `{known_agents_section}` - 已知 agents 列表（自动注入）
- `{skills_section}` - 技能部分（如果 agent 有技能，自动注入）

### 注意事项
- 类型名称不能与内置类型（worker, secretary, boss, recycler）冲突
- 基础类型决定了 agent 的触发规则和目录结构
- 首轮提示词应该包含完整的角色定义和工作流程
- 续轮提示词应该简洁，只包含继续工作的指令
- 创建自定义类型后，可以在创建 agent 时选择该类型

### 高级用法：直接写入文件
如果命令系统不可用，也可以直接创建类型定义文件：
```python
from pathlib import Path
import secretary.config as cfg
import json
from datetime import datetime

type_name = "my-custom-type"
custom_types_file = cfg.AGENTS_DIR / "agents.json"

# 读取现有注册表
with open(custom_types_file, "r", encoding="utf-8") as f:
    registry = json.load(f)

# 添加自定义类型
if "custom_types" not in registry:
    registry["custom_types"] = {}

registry["custom_types"][type_name] = {
    "name": type_name,
    "base_type": "worker",
    "description": "描述",
    "first_prompt": first_prompt_content,
    "continue_prompt": continue_prompt_content,
    "created_at": datetime.now().isoformat()
}

# 保存
with open(custom_types_file, "w", encoding="utf-8") as f:
    json.dump(registry, f, ensure_ascii=False, indent=2)
```

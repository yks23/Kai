# 技能: 技能传授

> 允许 agent 将自己的技能和工作经验写成文档，创建新的可复用技能

## 技能说明

你可以将自己的技能和工作经验整理成文档，创建新的可复用技能，供其他 agent 学习使用。

### 技能文件位置
`{skills_dir}` 目录

### 使用方法
在 Python 代码中调用：
```python
from pathlib import Path
import secretary.config as cfg

# 创建技能文件
skill_name = "my-skill"  # 技能名称（建议使用小写字母和连字符）
skill_description = "这个技能的作用描述"  # 简短描述
skill_content = """
# 任务描述

这里是技能的详细内容，包括：
- 任务目标
- 执行步骤
- 注意事项
- 示例代码或模板
"""

# 写入技能文件
skills_dir = cfg.BASE_DIR / "skills"
skills_dir.mkdir(parents=True, exist_ok=True)
skill_file = skills_dir / f"{skill_name}.md"

skill_doc = f"""# 技能: {skill_name}

> {skill_description}

## 任务描述

{skill_content}
"""

skill_file.write_text(skill_doc, encoding="utf-8")
print(f"✅ 技能 '{skill_name}' 已创建: {skill_file}")
```

### 技能文件格式
技能文件应遵循以下格式：
```markdown
# 技能: <技能名称>

> <简短描述>

## 任务描述

<详细的技能内容，包括任务目标、执行步骤、注意事项等>
```

### 注意事项
- 技能名称应使用小写字母、数字和连字符（如 `my-skill`、`data-analysis`）
- 技能描述应该简洁明了，说明这个技能的主要用途
- 任务描述部分应该详细，包含足够的上下文信息，让其他 agent 能够理解和使用这个技能
- 创建技能后，可以通过 `kai <skill-name>` 命令使用该技能

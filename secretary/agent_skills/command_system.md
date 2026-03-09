# 技能: 命令系统

> 允许 agent 通过命令文件操作主进程，实现高级自动化功能

## 技能说明

你可以通过创建命令文件来操作主进程，实现更高级的自动化：

### 命令文件位置
`{commands_dir}` 目录

### 使用方法
在 Python 代码中调用：
```python
from secretary.command_system import write_command_file

# 创建新 agent
write_command_file("你的名字", "create_agent", {
    "name": "新agent名称",
    "type": "worker",  # 或 "secretary", "boss", "recycler"
    "description": "agent描述",
    "known_agents": ["你的名字"]  # 可选，新agent认识的agents
})

# 启动 agent 的 scanner
write_command_file("你的名字", "start_agent", {
    "name": "要启动的agent名称"
})

# 向其他 agent 发送任务
write_command_file("你的名字", "send_task", {
    "target": "目标agent名称",
    "content": "任务内容（markdown格式）"
})

# 链接两个 agents（让一个agent认识另一个）
write_command_file("你的名字", "link_agents", {
    "target": "要链接的agent名称"
})
```

**注意**：命令文件会被主进程定期扫描并执行（约每2秒）。命令执行后会被移动到 `processed/` 或 `failed/` 目录。


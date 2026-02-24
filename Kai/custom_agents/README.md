# 自定义 Agent 类型

本目录用于存放用户贡献的自定义 agent 类型。系统会自动扫描此目录，发现并注册所有继承 `AgentType` 的类。

## 目录结构

```
Kai/
├── custom_agents/          # 自定义 agent 类型目录
│   ├── README.md          # 本文件
│   ├── example_agent.py  # 示例代码
│   └── your_agent.py      # 你的自定义 agent
└── custom_prompts/        # 自定义提示词模板目录
    └── your_agent.md     # 对应的提示词模板
```

## 如何创建自定义 Agent 类型

### 1. 创建 Agent 类型文件

在 `custom_agents/` 目录下创建新的 Python 文件，例如 `reviewer.py`：

```python
from pathlib import Path
from secretary.agent_types.base import AgentType
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)

class ReviewerAgent(AgentType):
    """审查者 Agent - 审查代码和文档"""
    
    @property
    def name(self) -> str:
        return "reviewer"
    
    @property
    def label_template(self) -> str:
        return "🔍 {name}"
    
    @property
    def prompt_template(self) -> str:
        return "reviewer.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """构建 Reviewer 的配置"""
        reviewer_dir = base_dir / "agents" / agent_name
        return AgentConfig(
            name=agent_name,
            base_dir=reviewer_dir,
            input_dir=reviewer_dir / "tasks",
            processing_dir=reviewer_dir / "ongoing",
            output_dir=reviewer_dir / "reports",
            logs_dir=reviewer_dir / "logs",
            stats_dir=reviewer_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[reviewer_dir / "tasks"],
                condition=TriggerCondition.HAS_FILES,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,
            first_round_prompt="reviewer.md",
            use_ongoing=True,
            log_file=reviewer_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """处理审查任务"""
        import shutil
        from datetime import datetime
        from secretary.agent_runner import run_agent
        from secretary.agent_loop import load_prompt
        
        # 确保 processing 目录存在
        config.processing_dir.mkdir(parents=True, exist_ok=True)
        
        # 将任务文件移动到 processing 目录
        ongoing_file = config.processing_dir / task_file.name
        if task_file.exists():
            shutil.move(str(task_file), str(ongoing_file))
            if verbose:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{ts}] 📦 任务文件已移动到 processing/: {ongoing_file.name}")
        
        # 构建提示词
        template = load_prompt("reviewer.md")
        task_content = ongoing_file.read_text(encoding="utf-8")
        prompt = template.format(
            task_content=task_content,
            report_dir=config.output_dir,
        )
        
        # 调用 Agent
        result = run_agent(
            prompt=prompt,
            workspace=str(config.base_dir.parent.parent),  # WORKSPACE
            verbose=verbose,
        )
        
        # 处理结果...
        # 完成后删除 ongoing 文件
        if ongoing_file.exists():
            ongoing_file.unlink()
```

### 2. 创建提示词模板

在 `custom_prompts/` 目录下创建对应的提示词模板，例如 `reviewer.md`：

```markdown
你是一个代码审查专家。你的任务是审查代码和文档，提供建设性的反馈。

## 任务内容

{task_content}

## 输出要求

请将审查结果写入 `{report_dir}/review-report.md`。

审查报告应包含：
1. 代码质量评估
2. 潜在问题
3. 改进建议
4. 总体评分
```

### 3. 使用自定义 Agent

创建自定义 agent 后，系统会在启动时自动发现并注册。你可以使用以下命令创建该类型的 agent：

```bash
kai hire <name> reviewer
```

然后启动它：

```bash
kai start <name>
```

## AgentType 接口说明

所有自定义 agent 类型必须继承 `AgentType` 并实现以下抽象方法：

### 必需属性

- `name: str` - Agent 类型名称（用于注册和识别）
- `label_template: str` - 标签模板，例如 `"🔍 {name}"`
- `prompt_template: str` - 首轮提示词模板文件名（如 `"reviewer.md"`）

### 必需方法

- `build_config(base_dir: Path, agent_name: str) -> AgentConfig` - 构建该类型的 AgentConfig
- `process_task(config: AgentConfig, task_file: Path, verbose: bool = True) -> None` - 处理任务文件

## 配置选项

### TriggerConfig（触发配置）

- `watch_dirs: List[Path]` - 监视的目录列表
- `condition: TriggerCondition` - 触发条件：
  - `HAS_FILES` - 目录中有文件时触发
  - `IS_EMPTY` - 目录为空时触发
- `custom_trigger_fn: Callable` - 自定义触发函数（可选）

### TerminationCondition（终止条件）

- `UNTIL_FILE_DELETED` - 直到 processing 目录中的任务文件被删除（用于多轮对话）
- `SINGLE_RUN` - 单次执行后终止（用于一次性任务）

## 提示词模板位置

提示词模板的加载优先级：

1. `{WORKSPACE}/Kai/custom_prompts/` - 用户自定义（优先）
2. `secretary/prompts/` - 包内默认（回退）

## 示例

查看 `example_agent.py` 了解完整的示例代码。

## 注意事项

1. **类型名称唯一性**：确保 `name` 属性不与内置类型冲突（worker, secretary, boss, recycler）
2. **模块导入**：自定义 agent 类型不应依赖其他自定义模块，避免循环依赖
3. **错误处理**：在 `process_task` 中妥善处理异常，避免影响扫描循环
4. **目录结构**：遵循统一的目录结构（input_dir, processing_dir, output_dir）

## 调试

如果自定义 agent 类型未被发现，检查：

1. 文件是否在 `custom_agents/` 目录下
2. 类是否继承 `AgentType`
3. 是否实现了所有抽象方法
4. 查看启动时的输出，是否有错误信息

可以使用以下命令查看已注册的类型：

```python
from secretary.agent_registry import list_agent_types
print(list_agent_types())
```


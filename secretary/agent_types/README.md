# Agent 类型定义

本目录包含所有 agent 类型的集中化定义。每个 agent 类型在独立的模块中定义，包含其独特的配置、触发规则、处理逻辑等。

## 目录结构

```
agent_types/
├── __init__.py      # 导出所有 agent 类型
├── base.py          # AgentType 基类
├── worker.py        # Worker Agent 定义
├── secretary.py     # Secretary Agent 定义
├── boss.py          # Boss Agent 定义
└── recycler.py      # Recycler Agent 定义
```

## Agent 类型

### Worker Agent (`worker.py`)
- **职责**: 执行编程任务
- **触发规则**: `tasks/` 目录有文件时触发
- **终止条件**: 直到 `ongoing/` 中的任务文件被删除
- **处理逻辑**: 多轮对话，支持续轮和完善阶段
- **提示词**: `worker_first_round.md`, `worker_continue.md`, `worker_refine.md`

### Secretary Agent (`secretary.py`)
- **职责**: 任务分类、归并和分配
- **触发规则**: `tasks/` 目录有文件时触发
- **终止条件**: 单次执行后终止
- **处理逻辑**: 读取任务，调用 `run_secretary` 处理，移动到 `assigned/`
- **提示词**: `secretary.md`

### Boss Agent (`boss.py`)
- **职责**: 监控指定 worker 的任务队列，在队列为空时生成新任务
- **触发规则**: 监控的 worker 的 `tasks/` 和 `ongoing/` 都为空时触发（自定义触发函数）
- **终止条件**: 单次执行后终止
- **处理逻辑**: 调用 `run_boss` 生成任务并写入 worker 的 `tasks/` 目录
- **提示词**: `boss.md`

### Recycler Agent (`recycler.py`)
- **职责**: 审查 Worker 的完成报告，判断任务是否真正完成
- **触发规则**: 扫描所有 agent 的 `reports/` 目录，查找 `*-report.md` 文件（自定义触发函数）
- **终止条件**: 单次执行后终止
- **处理逻辑**: 调用 `process_report` 审查报告，移动到 `solved/` 或 `unsolved/`
- **提示词**: `recycler.md`

## 使用方式

### 构建配置
```python
from secretary.agent_types import WorkerAgent, SecretaryAgent, BossAgent, RecyclerAgent

# 构建 Worker 配置
worker_type = WorkerAgent()
config = worker_type.build_config(base_dir, "worker_name")

# 构建 Secretary 配置
secretary_type = SecretaryAgent()
config = secretary_type.build_config(base_dir, "secretary_name")
```

### 处理任务
```python
# 使用 agent 类型处理任务
agent_type = WorkerAgent()
agent_type.process_task(config, task_file, verbose=True)
```

## 扩展新的 Agent 类型

要添加新的 agent 类型：

1. 在 `agent_types/` 目录下创建新文件，例如 `new_agent.py`
2. 继承 `AgentType` 基类，实现所有抽象方法
3. 在 `__init__.py` 中导出新类型
4. 在 `agent_config.py` 中添加构建函数（可选，用于向后兼容）

示例：
```python
from secretary.agent_types.base import AgentType
from secretary.agent_config import AgentConfig

class NewAgent(AgentType):
    @property
    def name(self) -> str:
        return "new_agent"
    
    @property
    def label_template(self) -> str:
        return "🆕 {name}"
    
    @property
    def prompt_template(self) -> str:
        return "new_agent.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        # 构建配置...
        pass
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        # 处理任务...
        pass
```


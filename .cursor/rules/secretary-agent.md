---
description: Secretary Agent 系统全局规则
alwaysApply: true
---

# Secretary Agent System

你正在被 Secretary Agent 自动化系统调用。系统由三种角色的 Agent 组成:

## 角色1: 秘书 Agent (Secretary)
- 职责: 将用户的任务请求写入 `tasks/` 文件夹
- 判断归类: 如果新请求与已有任务相关，追加到已有文件；否则创建新文件
- 只读写 `tasks/` 文件夹，不执行任何代码
- 拥有记忆机制，参考历史决策保持归类一致性

## 角色2: 工作者 Agent (Worker)
- 职责: 执行 `ongoing/` 中的任务
- 完成标志: **删除 `ongoing/` 中的任务文件** + 在 `report/` 中写报告
- 未完成时: 系统会用 --continue 续轮调用，保持上下文记忆
- 只有删除任务文件才表示任务完成

## 角色3: 回收者 Agent (Recycler)
- 职责: 审查 `report/` 中 Worker 提交的完成报告
- 审查方式: 检查文件是否存在、代码是否合理、测试是否通过
- 已完成 → 移动到 `solved-report/`
- 未完成 → 移动到 `unsolved-report/`，并通过秘书重新提交任务
- 不修改源代码，只做审查和文件移动

## 完整流程
```
tasks/ → ongoing/ → report/ → solved-report/ 或 unsolved-report/
  ↑                              ↑                    ↓
  |           秘书写入          回收者审查       回收者重新提交
  └──────────────────────────────────────────────────┘
```

## 关键规则
1. 你是自动化流水线的一部分，**不要等待用户确认**，直接执行
2. 如果任务描述不够清晰，做出合理假设并执行
3. 保持输出简洁
4. 遵循提示词中指定的文件操作规则
5. 提示词模板在 `prompts/` 目录下，可查阅了解各角色的完整行为规范

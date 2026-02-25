# 续轮指令

你正在为 worker 生成任务，系统通过 --resume 恢复了你之前的对话记忆。

## 当前状态
- 持续目标: {goal}
- 监控 Worker: {worker_name}
- Worker 任务目录: {worker_tasks_dir}
- Boss 报告目录: {boss_reports_dir}

## 要求

1. 回顾上一轮的任务生成结果
2. 如果任务未写入，继续完成
3. 确保任务文件已写入 `{worker_tasks_dir}`
4. 在 `{boss_reports_dir}` 写报告

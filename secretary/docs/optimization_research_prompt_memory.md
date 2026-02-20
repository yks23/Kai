# 提示词与记忆系统优化方法 — 调研摘要

本文档为「优化提示词和记忆系统」的**网络调研阶段**产出，供后续方案设计与迭代任务引用。

---

## 一、提示词（Prompt）优化

### 1.1 来源与原则概览

| 主题 | 关键来源 | 链接 |
|------|----------|------|
| 官方提示工程 | OpenAI API 文档 | https://platform.openai.com/docs/guides/prompt-engineering |
| 综合技巧与论文 | Prompt Engineering Guide (DAIR) | https://www.promptingguide.ai/ |
| 推理与 CoT | OpenAI Cookbook | https://cookbook.openai.com/ (相关资源与论文) |

### 1.2 提示词工程要点

- **角色与指令层级**  
  - 使用 `developer` / `user` 等角色区分「系统规则」与「用户输入」，`developer` 指令优先于 `user`（OpenAI Model Spec: chain of command）。  
  - 高层次的「如何行为」放在 `instructions` 或 developer 消息中，具体任务放在 user 消息中。

- **结构化提示**  
  - 建议顺序：Context（背景/数据）→ Examples（示例）→ Instructions（规则与约束）→ Identity（身份、风格、目标）。  
  - 用 **Markdown 标题与列表** 划分区块，用 **XML 标签** 明确内容边界与元数据（如 `<product_review id="example-1">`），便于模型解析与缓存。

- **Few-shot 学习**  
  - 在 prompt 中提供少量输入-输出示例，引导模型在新任务上表现更好（Brown et al. 2020）。  
  - 要点：示例要**多样**、格式一致；复杂推理任务上单纯 few-shot 可能不足，需结合 CoT（PromptingGuide.ai）。

- **链式思考（Chain-of-Thought, CoT）**  
  - 显式要求「一步步推理」可显著提升算术、常识与符号推理（Wei et al. 2022）。  
  - **Zero-shot CoT**：在问题后加「Let's think step by step」即可激发推理步骤（Kojima et al. 2022）。  
  - 可与 few-shot 结合：在示例中展示完整推理链再让模型仿照输出。

- **角色与约束**  
  - 明确身份（Identity）、沟通风格与目标；在 Instructions 中写清「必须做 / 禁止做」、输出格式、工具使用方式。  
  - 编码类任务：定义 agent 角色、工具调用示例、测试与校验要求、Markdown 规范（OpenAI GPT-5 prompting guide）。

- **可复用与缓存**  
  - 使用可复用 prompt 模板与占位符（如 `{{customer_name}}`），便于版本管理与 A/B 测试。  
  - 将**固定、重复使用**的内容放在 prompt 前部，以利用 **prompt caching** 降低延迟与成本。

- **模型差异**  
  - **GPT 类**：适合更精确、步骤明确的指令。  
  - **Reasoning 类**：适合高层目标描述，少写细节指令。  
  - 生产环境建议固定 **model snapshot** 并配合 **evals** 监控 prompt 表现。

### 1.3 可直接用于方法设计的要点

1. 按「Context → Examples → Instructions → Identity」组织 system/developer 消息。  
2. 复杂推理任务优先使用 CoT（含 zero-shot「Let's think step by step」）或 few-shot + CoT。  
3. 用 XML/Markdown 划分区块，便于解析与缓存。  
4. 区分 developer 与 user 消息，把规则与约束放在高优先级角色中。  
5. 对 prompt 做版本管理与评估（evals），避免过度依赖单次测试。

---

## 二、记忆系统（Memory）优化

### 2.1 来源与分类

| 主题 | 关键来源 | 链接 |
|------|----------|------|
| RAG 综述 | Gao et al. 2023 综述 | https://arxiv.org/abs/2312.10997 |
| RAG 概念与用法 | PromptingGuide.ai | https://www.promptingguide.ai/techniques/rag |
| RAG 原始方法 | Meta AI (Lewis et al.) | https://ai.meta.com/blog/retrieval-augmented-generation-streamlining-the-creation-of-intelligent-natural-language-processing-models/ |

### 2.2 记忆架构与分类

- **长期 vs 短期**  
  - **短期**：当前对话轮次、本轮上下文（context window 内），易受长度限制。  
  - **长期**：跨会话、跨任务的信息，需通过外部存储（数据库、向量库）与检索注入到当前上下文。

- **RAG（检索增强生成）**  
  - 将「检索」与「生成」结合：用检索器从外部知识库取回相关文档，与用户输入一起作为 context 送入 LLM，从而增强事实一致性、可追溯性并缓解幻觉。  
  - 典型架构：**参数记忆**（模型权重）+ **非参数记忆**（如向量索引，Lewis et al.）。  
  - 适用于知识密集型任务、需要最新或领域知识的场景；知识可更新而无需重训模型。

- **RAG 范式演进（Gao et al. 综述）**  
  - **Naive RAG**：检索 → 拼接 → 生成。  
  - **Advanced RAG**：在检索前后加入预处理、重排、查询扩展等。  
  - **Modular RAG**：检索、生成、增强等模块可插拔与组合。

### 2.3 总结与截断策略

- **Context window 规划**：不同模型有不同 token 上限（从约 100k 到百万级），需在 prompt 中预留空间给检索结果与对话历史。  
- **总结与截断**：当历史超过窗口时，可对旧对话做**摘要**再保留最近 N 轮原文；或按时间/重要性**截断**旧消息，只保留关键信息。  
- **检索与优先级**：检索时按相关性排序；可结合业务规则（时间、来源、类型）做重排与过滤，优先注入高优先级记忆。

### 2.4 向量记忆与检索

- 用**向量数据库**存储对话片段、用户偏好、任务结果等，通过语义相似度检索。  
- 检索结果作为「上下文」拼进 prompt，实现长期记忆的按需注入。  
- 可与 RAG 共用同一套检索与索引设计（ chunk 策略、embedding 模型、top-k 等）。

### 2.5 可直接用于方法设计的要点

1. 明确区分「当前会话上下文」与「长期记忆」，长期记忆走检索/存储架构。  
2. 采用 RAG 思路：检索 → 排序/过滤 → 拼入 context → 生成；可迭代到 Advanced/Modular RAG。  
3. 设计总结与截断策略，保证在 context 限制内保留最重要信息。  
4. 记忆检索结果需与 prompt 结构配合（如放入 XML 区块），并考虑优先级与去重。  
5. 引用 RAG 综述（arXiv:2312.10997）中的检索/生成/增强三要素做模块化设计。

---

## 三、关键来源链接汇总

- **提示词**  
  - OpenAI Prompt Engineering: https://platform.openai.com/docs/guides/prompt-engineering  
  - OpenAI Model Spec (chain of command): https://model-spec.openai.com/  
  - PromptingGuide.ai: https://www.promptingguide.ai/  
  - Few-shot: https://www.promptingguide.ai/techniques/fewshot  
  - Chain-of-Thought: https://www.promptingguide.ai/techniques/cot  
  - OpenAI Cookbook (prompting, reasoning): https://cookbook.openai.com/

- **记忆与 RAG**  
  - RAG 综述: https://arxiv.org/abs/2312.10997  
  - PromptingGuide RAG: https://www.promptingguide.ai/techniques/rag  
  - Meta RAG 博客: https://ai.meta.com/blog/retrieval-augmented-generation-streamlining-the-creation-of-intelligent-natural-language-processing-models/

---

## 四、文档位置与使用说明

- **本文档路径**：`secretary/docs/optimization_research_prompt_memory.md`（相对于项目根目录 `C:\CODE\Kai`）。  
- **建议**：后续「方案与迭代」任务可直接引用本摘要中的分类、要点与链接，用于设计具体优化方案（如 secretary 提示词模板、记忆存储与检索策略）。

*调研完成日期：2026-02-20*

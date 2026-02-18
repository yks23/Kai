"""
秘书 Agent — 调用 Cursor Agent 来决定任务的归类和写入

工作逻辑:
  用户输入任务描述 → 调用 cursor agent → agent 读取 tasks/ 下现有文件
  → 决定: 归入已有文件 or 创建新文件 → 写入 tasks/ 文件夹
  → 将本次决策摘要追加到 secretary_memory.md (记忆)

记忆机制:
  secretary_memory.md 记录每次调用的摘要:
  - 用户请求了什么
  - 秘书做了什么决策 (归类/新建)
  - 涉及哪个文件
  下次调用时，这些历史会作为上下文塞进提示词，
  帮助秘书做出更一致的归类决策。

提示词模板:
  prompts/secretary.md
"""
import sys
from datetime import datetime
from pathlib import Path

from secretary.config import BASE_DIR, TASKS_DIR, PROMPTS_DIR, SECRETARY_MEMORY_FILE
from secretary.agent_runner import run_agent


def _load_prompt_template() -> str:
    """加载秘书提示词模板"""
    tpl_path = PROMPTS_DIR / "secretary.md"
    return tpl_path.read_text(encoding="utf-8")


def _load_memory() -> str:
    """加载秘书的历史记忆"""
    if SECRETARY_MEMORY_FILE.exists():
        content = SECRETARY_MEMORY_FILE.read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        if len(lines) > 150:
            header = lines[:2]
            recent = lines[-150:]
            content = "\n".join(header + ["", "...(更早的记录已省略)...", ""] + recent)
        return content
    return ""


def _append_memory(user_request: str, agent_output: str):
    """将本次调用的摘要追加到记忆文件"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not SECRETARY_MEMORY_FILE.exists():
        SECRETARY_MEMORY_FILE.write_text(
            "# 秘书Agent 记忆\n\n"
            "记录每次调用的决策历史，帮助后续调用做出更一致的归类判断。\n\n",
            encoding="utf-8",
        )

    output_lines = agent_output.strip().splitlines()
    summary_lines = [l for l in output_lines if l.strip()][-5:]
    summary = "\n".join(summary_lines) if summary_lines else "(无输出)"

    entry = (
        f"---\n"
        f"### [{now}]\n"
        f"- **请求**: {user_request[:200]}\n"
        f"- **决策**: {summary}\n"
        f"\n"
    )

    with open(SECRETARY_MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def build_secretary_prompt(user_request: str) -> str:
    """
    构建给秘书 Agent 的提示词 (模板 + 记忆 + 用户请求)
    """
    memory = _load_memory()
    memory_section = ""
    if memory:
        memory_section = (
            "\n## 你的历史记忆\n"
            "以下是你之前的决策记录，请参考这些历史来保持归类的一致性:\n\n"
            f"{memory}\n"
        )

    template = _load_prompt_template()
    return template.format(
        base_dir=BASE_DIR,
        tasks_dir=TASKS_DIR,
        memory_section=memory_section,
        user_request=user_request,
    )


def run_secretary(user_request: str, verbose: bool = True) -> bool:
    """
    运行秘书 Agent 处理用户请求

    Returns:
        是否成功
    """
    if verbose:
        print(f"📋 秘书 Agent 收到请求: {user_request}")
        has_memory = SECRETARY_MEMORY_FILE.exists()
        print(f"   记忆: {'✅ 已加载历史记忆' if has_memory else '🆕 首次调用，无历史记忆'}")
        print(f"   正在分析并归类...")

    prompt = build_secretary_prompt(user_request)

    result = run_agent(
        prompt=prompt,
        workspace=str(BASE_DIR),
        verbose=verbose,
    )

    if result.success:
        _append_memory(user_request, result.output)
        if verbose:
            print(f"\n✅ 秘书 Agent 完成 (耗时 {result.duration:.1f}s)")
            print(f"   📝 记忆已更新: {SECRETARY_MEMORY_FILE}")
    else:
        print(f"\n❌ 秘书 Agent 失败: {result.output[:300]}")

    return result.success


if __name__ == "__main__":
    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
        run_secretary(request)
    else:
        print("用法: python secretary.py <任务描述>")

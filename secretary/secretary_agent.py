"""
秘书 Agent — 调用 Agent 来决定任务的归类、分配和写入

工作逻辑:
  用户输入任务描述 → 调用 agent → agent 读取各工人 tasks/ 下现有文件
  → 决定: 归入已有文件 or 创建新文件 → 选择分配给哪个工人 → 写入对应 tasks/
  → 将本次决策摘要追加到 secretary_memory.md (记忆)

记忆机制:
  secretary_memory.md 记录每次调用的摘要:
  - 用户请求了什么
  - 秘书做了什么决策 (归类/新建/分配给了谁)
  - 涉及哪个文件
  下次调用时，这些历史会作为上下文塞进提示词，
  帮助秘书做出更一致的归类和分配决策。

工人分配:
  秘书根据工人的历史完成任务、当前负载、擅长方向来决定分配给谁。
  没有合适工人时写入全局 tasks/ 目录。

提示词模板:
  prompts/secretary.md
"""
import sys
from datetime import datetime
from pathlib import Path

import secretary.config as cfg
from secretary.agent_runner import run_agent


def _load_prompt_template() -> str:
    """加载秘书提示词模板"""
    tpl_path = cfg.PROMPTS_DIR / "secretary.md"
    return tpl_path.read_text(encoding="utf-8")


def _load_memory() -> str:
    """加载秘书的历史记忆"""
    if cfg.SECRETARY_MEMORY_FILE.exists():
        content = cfg.SECRETARY_MEMORY_FILE.read_text(encoding="utf-8")
        lines = content.strip().splitlines()
        if len(lines) > 150:
            header = lines[:2]
            recent = lines[-150:]
            content = "\n".join(header + ["", "...(更早的记录已省略)...", ""] + recent)
        return content
    return ""


def get_goals() -> list:
    """获取当前全局目标列表（供 CLI 列出）"""
    if not getattr(cfg, "SECRETARY_GOALS_FILE", None) or not cfg.SECRETARY_GOALS_FILE.exists():
        return []
    text = cfg.SECRETARY_GOALS_FILE.read_text(encoding="utf-8")
    goals = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 只把列表项 "- xxx" 视为目标，忽略说明行
        if line.startswith("- "):
            goals.append(line[2:].strip())
    return goals


def set_goals(goals: list) -> None:
    """将全局目标持久化到 secretary_goals.md（覆盖）"""
    path = getattr(cfg, "SECRETARY_GOALS_FILE", None)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not goals:
        if path.exists():
            path.unlink()
        return
    lines = ["# 当前全局目标\n", "以下目标在任务归类与分配时请与之对齐。\n\n"]
    for g in goals:
        g = (g or "").strip()
        if g:
            lines.append(f"- {g}\n")
    path.write_text("".join(lines), encoding="utf-8")


def clear_goals() -> None:
    """清空当前全局目标"""
    set_goals([])


def _load_goals() -> str:
    """加载全局目标文本（供注入到秘书提示词）"""
    goals = get_goals()
    if not goals:
        return ""
    return "\n".join(f"- {g}" for g in goals)


def _load_workers_info() -> str:
    """加载工人信息摘要 (供秘书 Agent 分配任务)"""
    try:
        from secretary.agents import build_workers_summary
        summary = build_workers_summary()
        return summary
    except Exception:
        return ""


def _load_skills_info() -> str:
    """加载技能信息摘要 (供秘书 Agent 了解系统能力)"""
    try:
        from secretary.skills import list_skills
        skills = list_skills()
        if not skills:
            return ""
        lines = []
        for s in skills:
            tag = "内置" if s["builtin"] else "已学"
            desc = s["description"] or "(无描述)"
            lines.append(f"- **{s['name']}** ({tag}): {desc}")
        return "\n".join(lines)
    except Exception:
        return ""


def _load_existing_tasks_summary() -> str:
    """
    扫描所有工人的任务目录，生成现有任务概览。
    让秘书不需要自己再去 ls，直接在提示词中就能看到全局视图。
    """
    lines = []

    # 各工人 tasks/
    try:
        from secretary.agents import list_workers, _worker_tasks_dir
        workers = list_workers()
        if not workers:
            return ""
        
        for w in workers:
            wt = _worker_tasks_dir(w["name"])
            if wt.exists():
                md_files = sorted(wt.glob("*.md"))
                if md_files:
                    lines.append(f"### 工人 {w['name']} 的队列 `{wt}` ({len(md_files)} 个)")
                    for f in md_files:
                        first_line = ""
                        try:
                            first_line = f.read_text(encoding="utf-8").strip().splitlines()[0][:100]
                        except Exception:
                            pass
                        lines.append(f"- `{f.name}`: {first_line}")
    except Exception:
        pass

    return "\n".join(lines) if lines else ""


def _append_memory(user_request: str, agent_output: str):
    """将本次调用的摘要追加到记忆文件"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not cfg.SECRETARY_MEMORY_FILE.exists():
        cfg.SECRETARY_MEMORY_FILE.write_text(
            "# 秘书Agent 记忆\n\n"
            "记录每次调用的决策历史，帮助后续调用做出更一致的归类和分配判断。\n\n",
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

    with open(cfg.SECRETARY_MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def build_secretary_prompt(user_request: str) -> str:
    """
    构建给秘书 Agent 的提示词
    包含: 模板 + 记忆 + 工人信息 + 技能 + 现有任务概览 + 用户请求
    """
    # 1. 历史记忆
    memory = _load_memory()
    memory_section = ""
    if memory:
        memory_section = (
            "\n## 你的历史记忆\n"
            "以下是你之前的决策记录，请参考这些历史来保持归类和分配的一致性:\n\n"
            f"{memory}\n"
        )

    # 2. 工人信息 (必须!) - 包含所有 worker 及其工作总结
    workers_info = _load_workers_info()
    workers_section = ""
    if workers_info:
        workers_section = (
            "\n## 已招募的工人及其工作总结\n"
            "以下是当前已招募的工人及其详细信息，**你必须**根据这些信息决定把任务分配给谁:\n"
            "每个工人的工作总结（memory.md）包含了他们的工作历史、擅长方向、当前状态等。\n"
            "请仔细阅读每个工人的工作总结，以便做出最合适的分配决策。\n\n"
            f"{workers_info}\n"
        )
    else:
        workers_section = (
            "\n## ⚠️ 错误：没有可用的工人\n"
            "**当前没有招募任何工人！**\n\n"
            "**你必须拒绝处理这个任务**，并明确告诉用户：\n"
            "- 需要先招募工人才能分配任务\n"
            "- 使用 `kai hire` 或 `kai hire <名字>` 来招募工人\n"
            "- 例如：`kai hire alice` 或 `kai hire` (随机取名)\n\n"
            "**不要创建任何任务文件，直接说明需要先招募工人。**\n"
        )

    # 3. 技能信息
    skills_info = _load_skills_info()
    skills_section = ""
    if skills_info:
        skills_section = (
            "\n## 系统已学技能\n"
            "用户可以通过技能快速创建任务，以下是当前的技能列表，供你参考上下文:\n\n"
            f"{skills_info}\n"
        )

    # 4. 现有任务概览 (让秘书直接看到，减少需要 ls 的次数)
    tasks_overview = _load_existing_tasks_summary()
    tasks_section = ""
    if tasks_overview:
        tasks_section = (
            "\n## 当前待处理任务概览\n"
            "以下是各队列中已有的任务文件，帮助你判断是否需要归入已有任务:\n\n"
            f"{tasks_overview}\n"
        )

    # 5. 当前全局目标 (kai target 设定，归类与分配时与之对齐)
    goals_text = _load_goals()
    goals_section = ""
    if goals_text:
        goals_section = (
            "\n## 当前全局目标\n"
            "用户已设定以下全局目标，请在任务归类与分配时优先考虑与之对齐:\n\n"
            f"{goals_text}\n"
        )

    template = _load_prompt_template()
    # 注意: 不再使用根目录的 tasks_dir，所有任务都分配到 worker 目录
    # 这里保留 tasks_dir 参数用于提示词模板兼容，但实际应该使用 worker 目录
    default_tasks_dir = cfg.WORKERS_DIR / cfg.DEFAULT_WORKER_NAME / "tasks"
    return template.format(
        base_dir=cfg.BASE_DIR,
        tasks_dir=str(default_tasks_dir),  # 提示词中显示默认 worker 的目录
        memory_section=memory_section,
        workers_section=workers_section,
        skills_section=skills_section,
        tasks_section=tasks_section,
        goals_section=goals_section,
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
        has_memory = cfg.SECRETARY_MEMORY_FILE.exists()
        print(f"   记忆: {'✅ 已加载历史记忆' if has_memory else '🆕 首次调用，无历史记忆'}")

        # 显示工人信息
        try:
            from secretary.agents import list_workers
            workers = list_workers()
            if workers:
                names = [w["name"] for w in workers]
                print(f"   工人: {', '.join(names)} (共 {len(workers)} 人)")
            else:
                print(f"   工人: 无 (任务写入通用 tasks/)")
        except Exception:
            pass

        # 显示技能信息
        try:
            from secretary.skills import list_skills
            skills = list_skills()
            if skills:
                print(f"   技能: {len(skills)} 个 ({', '.join(s['name'] for s in skills[:5])}{'...' if len(skills) > 5 else ''})")
        except Exception:
            pass

        print(f"   正在分析、归类并分配...")

    prompt = build_secretary_prompt(user_request)

    # 从设置中获取模型，如果没有设置则使用 Auto
    from secretary.settings import get_model
    model = get_model()
    
    result = run_agent(
        prompt=prompt,
        workspace=str(cfg.BASE_DIR),
        model=model,
        verbose=verbose,
    )

    if result.success:
        _append_memory(user_request, result.output)
        if verbose:
            print(f"\n✅ 秘书 Agent 完成 (耗时 {result.duration:.1f}s)")
            print(f"   📝 记忆已更新: {cfg.SECRETARY_MEMORY_FILE}")
    else:
        print(f"\n❌ 秘书 Agent 失败: {result.output[:300]}")

    return result.success


if __name__ == "__main__":
    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
        run_secretary(request)
    else:
        print("用法: python secretary.py <任务描述>")

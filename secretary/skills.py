"""
技能管理模块 — 让 kai 学会并复用任务模板

技能 = 一个 Markdown 文件，存放在 skills/ 目录下，包含任务描述。
  - 内置技能 (evolving / analysis / debug) 首次运行时自动初始化
  - 用户通过 `kai learn "描述" skill-name` 可以教会新技能
  - `kai <skill-name>` 直接把技能模板写入 worker (sen) 的 tasks/ (跳过秘书 agent)
  - `kai forget <skill-name>` 忘掉一个技能
  - `kai skills` 列出所有技能
"""
import shutil
from pathlib import Path

import secretary.config as cfg


# ============================================================
#  内置技能初始化
# ============================================================

def _builtin_skill_path(skill_name: str) -> Path:
    return cfg.SKILLS_DIR / f"{skill_name}.md"


def ensure_builtin_skills():
    """把 config.BUILTIN_SKILLS 中尚未存在的技能写入 skills/ 目录"""
    cfg.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for name, info in cfg.BUILTIN_SKILLS.items():
        fp = _builtin_skill_path(name)
        if not fp.exists():
            desc = info["description"]
            prompt = info["prompt"]
            content = (
                f"# 技能: {name}\n\n"
                f"> {desc}\n\n"
                f"## 任务描述\n\n"
                f"{prompt}\n\n"
                f"<!-- builtin: true -->\n"
            )
            fp.write_text(content, encoding="utf-8")


# ============================================================
#  技能 CRUD
# ============================================================

def list_skills() -> list[dict]:
    """
    列出所有技能，返回 [{"name": str, "description": str, "builtin": bool, "path": Path}, ...]
    """
    ensure_builtin_skills()
    skills = []
    if not cfg.SKILLS_DIR.exists():
        return skills
    for fp in sorted(cfg.SKILLS_DIR.glob("*.md")):
        name = fp.stem
        content = fp.read_text(encoding="utf-8")
        # 提取描述 (第一行 > 开头的引用)
        desc = ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("> "):
                desc = stripped[2:]
                break
        builtin = "<!-- builtin: true -->" in content
        skills.append({
            "name": name,
            "description": desc,
            "builtin": builtin,
            "path": fp,
        })
    return skills


def get_skill(skill_name: str) -> dict | None:
    """获取指定技能的信息 (不存在返回 None)"""
    fp = cfg.SKILLS_DIR / f"{skill_name}.md"
    if not fp.exists():
        return None
    content = fp.read_text(encoding="utf-8")
    desc = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("> "):
            desc = stripped[2:]
            break
    builtin = "<!-- builtin: true -->" in content
    return {
        "name": skill_name,
        "description": desc,
        "builtin": builtin,
        "path": fp,
    }


def get_skill_prompt(skill_name: str) -> str:
    """
    获取技能的任务描述文本 (用于写入 tasks/)
    对内置技能做模板变量替换 (testcases_dir, cli_name)
    """
    from secretary.settings import get_cli_name

    fp = cfg.SKILLS_DIR / f"{skill_name}.md"
    if not fp.exists():
        return ""
    content = fp.read_text(encoding="utf-8")

    # 提取 ## 任务描述 下面的内容
    prompt_lines = []
    in_prompt = False
    for line in content.splitlines():
        if line.strip().startswith("## 任务描述"):
            in_prompt = True
            continue
        if in_prompt:
            if line.strip().startswith("## ") or line.strip().startswith("<!-- "):
                break
            prompt_lines.append(line)
    prompt = "\n".join(prompt_lines).strip()

    # 模板替换
    prompt = prompt.replace("{testcases_dir}", str(cfg.TESTCASES_DIR))
    prompt = prompt.replace("{cli_name}", get_cli_name())

    return prompt


def learn_skill(skill_name: str, description: str) -> Path:
    """
    学会一个新技能 (保存到 skills/)
    返回技能文件路径
    """
    cfg.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    fp = cfg.SKILLS_DIR / f"{skill_name}.md"
    content = (
        f"# 技能: {skill_name}\n\n"
        f"> {description}\n\n"
        f"## 任务描述\n\n"
        f"{description}\n"
    )
    fp.write_text(content, encoding="utf-8")
    return fp


def forget_skill(skill_name: str) -> bool:
    """
    忘掉一个技能 (删除 skills/ 下的文件)
    返回是否成功
    """
    fp = cfg.SKILLS_DIR / f"{skill_name}.md"
    if fp.exists():
        fp.unlink()
        return True
    return False


def invoke_skill(skill_name: str, min_time: int = 0) -> Path | None:
    """
    使用一个技能: 把技能模板直接写入 worker 的 tasks/ (跳过秘书 agent)
    默认派发给 sen worker
    返回任务文件路径
    """
    from datetime import datetime
    from secretary.agents import _worker_tasks_dir, register_worker

    prompt = get_skill_prompt(skill_name)
    if not prompt:
        return None

    # 确保默认 worker (sen) 已注册
    register_worker(cfg.DEFAULT_WORKER_NAME, description="默认通用工人")
    
    # 使用默认 worker (sen) 的任务目录
    default_tasks_dir = _worker_tasks_dir(cfg.DEFAULT_WORKER_NAME)
    default_tasks_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    task_file = default_tasks_dir / f"{skill_name}-{ts}.md"

    # 使用标准的任务文件格式
    content = (
        f"# 任务: {skill_name}\n\n"
        f"## 描述\n"
        f"{prompt}\n\n"
        f"## 目标\n"
        f"完成 {skill_name} 技能定义的任务\n\n"
        f"## 工作区\n"
        f"待指定\n"
    )
    if min_time > 0:
        content += f"\n<!-- min_time: {min_time} -->\n"
    elif cfg.DEFAULT_MIN_TIME > 0:
        content += f"\n<!-- min_time: {cfg.DEFAULT_MIN_TIME} -->\n"

    task_file.write_text(content, encoding="utf-8")
    return task_file


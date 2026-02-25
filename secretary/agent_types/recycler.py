"""
Recycler Agent 类型定义与执行逻辑

Recycler 负责审查 Worker 的完成报告，判断任务是否真正完成，特点：
- 目录结构：统一的 input_dir (tasks/), processing_dir (ongoing/), output_dir (reports/)
- 触发规则：扫描所有 agent 的 output_dir 目录，查找 *-report.md 文件（自定义触发函数）
- 终止条件：持续运行（UNTIL_FILE_DELETED）
- 处理逻辑：调用 process_report 审查报告，移动到 solved/ 或 unsolved/
- 会话管理：每次都是新会话（单次执行）
"""
import shutil
from pathlib import Path
from typing import List

import secretary.config as cfg
from secretary.config import BASE_DIR, AGENTS_DIR, RECYCLER_INTERVAL
from secretary.agent_loop import load_prompt, run_loop
from secretary.agent_runner import run_agent
from secretary.agent_config import (
    AgentConfig, TerminationCondition, TriggerCondition, TriggerConfig
)
from secretary.agent_types.base import AgentType


# ============================================================
#  回收者执行逻辑（供 scanner 与类型内部使用）
# ============================================================

def _find_report_files() -> List[Path]:
    """从所有 agent 的 reports 目录中找到所有报告文件 (*-report.md)"""
    reports = []
    if not AGENTS_DIR.exists():
        return []
    for agent_dir in AGENTS_DIR.iterdir():
        if not agent_dir.is_dir() or agent_dir.name.startswith("."):
            continue
        reports_dir = agent_dir / "reports"
        if reports_dir.exists():
            agent_reports = [f for f in reports_dir.glob("*-report.md") if f.is_file()]
            reports.extend(agent_reports)
    return sorted(reports, key=lambda p: p.stat().st_mtime)


def _get_related_files(report_file: Path) -> List[Path]:
    """获取与报告关联的统计文件 (stats 目录下)"""
    base_name = report_file.stem.replace("-report", "")
    related = []
    parts = report_file.parts
    if "agents" in parts and "reports" in parts:
        try:
            agents_idx = parts.index("agents")
            if agents_idx + 1 < len(parts):
                agent_name = parts[agents_idx + 1]
                agent_dir = AGENTS_DIR / agent_name
                stats_dir = agent_dir / "stats"
                for suffix in ["-stats.md", "-stats.json"]:
                    f = stats_dir / f"{base_name}{suffix}"
                    if f.exists():
                        related.append(f)
        except (ValueError, IndexError):
            pass
    return related


def _get_recycler_dirs(recycler_name: str = "recycler") -> tuple[Path, Path]:
    """获取 recycler 的 solved 和 unsolved 目录"""
    recycler_dir = AGENTS_DIR / recycler_name
    solved_dir = recycler_dir / "solved"
    unsolved_dir = recycler_dir / "unsolved"
    solved_dir.mkdir(parents=True, exist_ok=True)
    unsolved_dir.mkdir(parents=True, exist_ok=True)
    return solved_dir, unsolved_dir


def build_recycler_prompt(report_file: Path, recycler_name: str = "recycler") -> str:
    """构建回收者 Agent 的提示词"""
    report_content = report_file.read_text(encoding="utf-8")
    task_name = report_file.stem.replace("-report", "")
    recycler_dir = AGENTS_DIR / recycler_name
    recycler_reports_dir = recycler_dir / "reports"
    stats_dir = None
    parts = report_file.parts
    if "agents" in parts and "reports" in parts:
        try:
            agents_idx = parts.index("agents")
            if agents_idx + 1 < len(parts):
                agent_name = parts[agents_idx + 1]
                stats_dir = AGENTS_DIR / agent_name / "stats"
        except (ValueError, IndexError):
            pass
    if stats_dir is None:
        stats_dir = AGENTS_DIR / recycler_name / "stats"
    stats_md = stats_dir / f"{task_name}-stats.md"
    stats_json = stats_dir / f"{task_name}-stats.json"
    stats_section = ""
    if stats_md.exists():
        stats_section = "## 执行统计数据\n\n---\n" + stats_md.read_text(encoding="utf-8") + "\n---\n"
    else:
        stats_section = "(无统计数据；此任务在统计功能上线前完成)\n"
    solved_dir, unsolved_dir = _get_recycler_dirs(recycler_name)
    reason_filename = f"{task_name}-unsolved-reason.md"
    from secretary.agents import _worker_memory_file
    memory_file_path = _worker_memory_file(recycler_name)
    template = load_prompt("recycler.md")
    return template.format(
        base_dir=BASE_DIR,
        report_file=report_file,
        report_content=report_content,
        stats_section=stats_section,
        solved_dir=solved_dir,
        unsolved_dir=unsolved_dir,
        memory_file_path=memory_file_path_section,
        reason_filename=reason_filename,
        recycler_reports_dir=recycler_reports_dir,
    )


def _move_related_stats(report_file: Path, dest_dir: Path):
    """确保 stats 中的关联文件也移到目标目录"""
    for f in _get_related_files(report_file):
        dest = dest_dir / f.name
        if not dest.exists():
            try:
                shutil.move(str(f), str(dest))
            except Exception:
                pass


def _ensure_unsolved_reason_record(task_name: str, unsolved_dir: Path | None = None, reason_content: str | None = None):
    """确保 unsolved 中对该任务有 *-unsolved-reason.md 记录"""
    if unsolved_dir is None:
        _, unsolved_dir = _get_recycler_dirs()
    unsolved_dir.mkdir(parents=True, exist_ok=True)
    reason_file = unsolved_dir / f"{task_name}-unsolved-reason.md"
    if reason_file.exists():
        return
    default = "# 未完成原因\n\n（回收者判定为未完成。）\n\n# 下一步改进方向\n\n请根据报告内容与实际情况，明确需要补充或修正的部分。\n"
    reason_file.write_text(reason_content or default, encoding="utf-8")


def _resubmit_task(task_name: str, report_content: str = "", verbose: bool = True):
    """调用秘书 Agent 重新提交未完成的任务"""
    _, unsolved_dir = _get_recycler_dirs()
    reason_file = unsolved_dir / f"{task_name}-unsolved-reason.md"
    reason = reason_file.read_text(encoding="utf-8").strip() if reason_file.exists() else ""
    parts = [f"之前的任务 `{task_name}` 经回收者审查判定为**未完成**，需要重新提交。\n"]
    if reason:
        parts.append(f"## 回收者的审查意见与改进方向\n\n{reason}\n")
    if report_content:
        trimmed = report_content[:2000] + "\n...(已截断)" if len(report_content) > 2000 else report_content
        parts.append(f"## 上一轮 Worker 的完成报告（供参考）\n\n{trimmed}\n")
    parts.append("## 要求\n请根据回收者的改进方向重新创建任务。\n")
    resubmit_request = "\n".join(parts)
    if verbose:
        print(f"   📨 重新提交任务: {task_name}")
    try:
        from secretary.agents import list_workers
        from secretary.cli import _write_kai_task, _select_secretary
        secretaries = [w for w in list_workers() if w.get("type") == "secretary"]
        if not secretaries:
            if verbose:
                print("   ⚠️ 没有可用的 secretary agent，无法重新提交任务")
            return
        secretary_name = secretaries[0]["name"] if len(secretaries) == 1 else _select_secretary(secretaries) or secretaries[0]["name"]
        _write_kai_task(resubmit_request, secretary_name=secretary_name)
    except Exception:
        if verbose:
            print("   ⚠️ 重新提交任务失败")
        raise


def _fallback_judgment(report_file: Path, agent_output: str, task_name: str,
                      report_content: str, verbose: bool, recycler_name: str = "recycler") -> bool:
    """当 Agent 没有移动文件时，根据输出文本做兜底判定"""
    is_solved = "已完成" in agent_output or "solved" in agent_output.lower()
    is_unsolved = "未完成" in agent_output or "unsolved" in agent_output.lower()
    related = _get_related_files(report_file)
    solved_dir, unsolved_dir = _get_recycler_dirs(recycler_name)
    if is_unsolved:
        dest = unsolved_dir / report_file.name
        unsolved_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(report_file), str(dest))
        for f in related:
            try:
                shutil.move(str(f), str(unsolved_dir / f.name))
            except Exception:
                pass
        _ensure_unsolved_reason_record(task_name, unsolved_dir=unsolved_dir)
        if verbose:
            print(f"   ℹ️ 兜底判定: 未完成 → {unsolved_dir.name}/")
        _resubmit_task(task_name, report_content=report_content, verbose=verbose)
        return True
    if is_solved:
        dest = solved_dir / report_file.name
        shutil.move(str(report_file), str(dest))
        for f in related:
            try:
                shutil.move(str(f), str(solved_dir / f.name))
            except Exception:
                pass
        if verbose:
            print(f"   ℹ️ 兜底判定: 已完成 → {solved_dir.name}/")
        return True
    if verbose:
        print("   ⚠️ 无法判断，保留在 report/ 中待下次审查")
    return False


def process_report(report_file: Path, recycler_config: AgentConfig | None = None, verbose: bool = True) -> bool:
    """对一份报告调用回收者 Agent 进行审查。返回 True=已处理，False=处理失败"""
    task_name = report_file.stem.replace("-report", "")
    recycler_name = recycler_config.name if recycler_config else "recycler"
    report_content = report_file.read_text(encoding="utf-8") if report_file.exists() else ""
    if verbose:
        print(f"\n🔍 回收者审查: {report_file.name}")
    prompt = build_recycler_prompt(report_file, recycler_name=recycler_name)
    result = run_agent(prompt=prompt, workspace=str(cfg.get_workspace()), verbose=verbose)
    if not result.success:
        print(f"   ❌ 回收者 Agent 调用失败: {result.output[:200]}")
        return False
    solved_dir, unsolved_dir = _get_recycler_dirs(recycler_name)
    report_gone = not report_file.exists()
    in_solved = (solved_dir / report_file.name).exists()
    in_unsolved = (unsolved_dir / report_file.name).exists()
    if in_solved:
        _move_related_stats(report_file, solved_dir)
        if verbose:
            print(f"   ✅ 判定: 已完成 → {solved_dir.name}/")
        return True
    if in_unsolved:
        _move_related_stats(report_file, unsolved_dir)
        _ensure_unsolved_reason_record(task_name, unsolved_dir=unsolved_dir)
        if verbose:
            print(f"   ✅ 判定: 未完成 → {unsolved_dir.name}/")
        _resubmit_task(task_name, report_content=report_content, verbose=verbose)
        return True
    if report_gone:
        if verbose:
            print("   ⚠️ 报告已被移动（Agent 已处理）")
        return True
    return _fallback_judgment(report_file, result.output, task_name, report_content, verbose, recycler_name)


def run_recycler(once: bool = False, verbose: bool = True, recycler_name: str = "recycler") -> None:
    """运行回收者主循环（供 CLI 调用）。"""
    from secretary.agent_registry import get_agent_type
    recycler_type = get_agent_type("recycler")
    config = recycler_type.build_config(cfg.BASE_DIR, recycler_name)

    def trigger_fn():
        return _find_report_files()

    def process_fn(report_path: Path):
        process_report(report_path, recycler_config=config, verbose=verbose)

    run_loop(
        trigger_fn,
        process_fn,
        interval_sec=float(RECYCLER_INTERVAL),
        once=once,
        label="recycler",
        verbose=verbose,
    )


# ============================================================
#  Agent 类型定义
# ============================================================

class RecyclerAgent(AgentType):
    """Recycler Agent 类型"""
    
    @property
    def name(self) -> str:
        return "recycler"
    
    @property
    def label_template(self) -> str:
        return "♻️ {name}"
    
    @property
    def prompt_template(self) -> str:
        return "recycler.md"
    
    def build_config(self, base_dir: Path, agent_name: str) -> AgentConfig:
        """
        构建 Recycler 的配置
        
        Recycler的触发规则：扫描所有agent的reports/目录，查找*-report.md文件
        """
        recycler_dir = base_dir / "agents" / agent_name
        
        def recycler_trigger_fn(config: AgentConfig) -> List[Path]:
            """Recycler的触发函数：扫描所有agent的reports目录"""
            return _find_report_files()
        
        return AgentConfig(
            name=agent_name,
            base_dir=recycler_dir,
            input_dir=recycler_dir / "tasks",
            processing_dir=recycler_dir / "ongoing",
            output_dir=recycler_dir / "reports",
            logs_dir=recycler_dir / "logs",
            stats_dir=recycler_dir / "stats",
            trigger=TriggerConfig(
                watch_dirs=[],  # Recycler不使用标准目录监视，使用自定义函数扫描所有reports
                condition=TriggerCondition.HAS_FILES,
                custom_trigger_fn=recycler_trigger_fn,
            ),
            termination=TerminationCondition.UNTIL_FILE_DELETED,  # Recycler持续运行，处理完报告后继续循环
            first_round_prompt="recycler.md",
            continue_prompt="recycler_continue.md",
            use_ongoing=False,
            log_file=recycler_dir / "logs" / "scanner.log",
            label=self.label_template.format(name=agent_name),
        )
    
    def process_task(self, config: AgentConfig, task_file: Path, verbose: bool = True) -> None:
        """
        处理 Recycler 任务
        
        流程：
        1. task_file 实际上是报告文件路径
        2. 调用 process_report 审查报告
        """
        # Recycler 的 task_file 实际上是报告文件
        process_report(task_file, recycler_config=config, verbose=verbose)


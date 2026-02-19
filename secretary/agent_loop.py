"""
Agent 循环框架 — 统一「触发 → 取项 → 处理一项 → 间隔」的扫描循环

各角色（Kai 扫描器、Worker 扫描器、回收者、Keep 等）只需实现 trigger_fn 与 process_fn，
由 run_loop 负责 while + sleep + once + 异常与 KeyboardInterrupt。
"""
import time
import traceback
from typing import Callable, Any, List

# 延迟导入避免与 config 等循环依赖
def load_prompt(template_name: str) -> str:
    """从 PROMPTS_DIR 加载提示词模板。template_name 如 'secretary.md', 'recycler.md'。"""
    import secretary.config as cfg
    path = cfg.PROMPTS_DIR / template_name
    return path.read_text(encoding="utf-8")


def run_loop(
    trigger_fn: Callable[[], List[Any]],
    process_fn: Callable[[Any], Any],
    interval_sec: float,
    once: bool = False,
    label: str = "agent",
    verbose: bool = True,
    on_exit: Callable[[], None] | None = None,
    on_idle: Callable[[], None] | None = None,
) -> None:
    """
    通用扫描循环：每轮调用 trigger_fn 取待处理项，对每项调用 process_fn，然后 sleep。

    - trigger_fn(): 返回待处理项列表，空列表表示本轮无工作。
    - process_fn(item): 处理单条；返回值未使用，仅便于打日志。
    - interval_sec: 每轮结束后的休眠秒数。
    - once: True 时执行一轮后退出（用于测试或单次拉取）。
    - label: 用于日志前缀。
    - verbose: 是否打印周期/异常等信息。
    - on_exit: 正常或 KeyboardInterrupt 退出时调用的回调（如 update_worker_status(idle)）。
    - on_idle: 当 trigger_fn 返回空列表时调用（可选，用于打印「无任务」等）。
    """
    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                items = trigger_fn()
                if not items and on_idle:
                    on_idle()
                for item in items:
                    process_fn(item)
                if once:
                    break
            except Exception as e:
                if verbose:
                    traceback.print_exc()
                # 单轮异常不退出，继续下一轮
            if once:
                break
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        if verbose:
            print(f"\n\n🛑 {label} 已停止 (共 {cycle} 个周期)")
    finally:
        if on_exit:
            try:
                on_exit()
            except Exception:
                pass

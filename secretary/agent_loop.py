"""
Agent 循环框架 — 统一「触发 → 取项 → 处理一项 → 间隔」的扫描循环

各角色（Study 扫描器、Worker 扫描器、回收者、Keep 等）只需实现 trigger_fn 与 process_fn，
由 run_loop 负责 while + sleep + once + 异常与 KeyboardInterrupt。
"""
import time
import traceback
from typing import Callable, Any, List

# 延迟导入避免与 config 等循环依赖
def load_prompt(template_name: str) -> str:
    """
    加载提示词模板，支持从多个位置加载。
    
    优先级：
    1. {WORKSPACE}/Study/custom_prompts/ (用户自定义)
    2. secretary/prompts/ (包内默认)
    
    Args:
        template_name: 模板文件名，如 'secretary_first.md', 'worker_first.md'
        
    Returns:
        模板内容字符串
        
    Raises:
        FileNotFoundError: 如果模板文件不存在
    """
    import secretary.config as cfg
    
    # 优先从自定义目录加载
    if cfg.CUSTOM_PROMPTS_DIR.exists():
        custom_path = cfg.CUSTOM_PROMPTS_DIR / template_name
        if custom_path.exists():
            return custom_path.read_text(encoding="utf-8")
    
    # 回退到包内默认目录
    default_path = cfg.PROMPTS_DIR / template_name
    if default_path.exists():
        return default_path.read_text(encoding="utf-8")
    
    # 都不存在，抛出异常
    raise FileNotFoundError(
        f"提示词模板 '{template_name}' 未找到。"
        f"已搜索: {cfg.CUSTOM_PROMPTS_DIR / template_name}, {default_path}"
    )


def run_loop(
    trigger_fn: Callable[[], List[Any]],
    process_fn: Callable[[Any], Any],
    interval_sec: float,
    once: bool = False,
    label: str = "agent",
    verbose: bool = True,
    on_exit: Callable[[], None] | None = None,
    on_idle: Callable[[], None] | None = None,
    log_file: str | None = None,
) -> None:
    """
    通用扫描循环：持续运行直到 KeyboardInterrupt 或 once=True
    
    循环模式：
      1. 检查触发条件 (trigger_fn)
      2. 执行动作 (process_fn) - 对每个触发项
      3. 休眠 (interval_sec)
      4. 重复步骤 1-3
    
    关键特性：
      - 异常不会导致循环退出，只会记录并继续下一轮
      - 只有 KeyboardInterrupt 或 once=True 才会退出
      - 每个 process_fn 的异常都被捕获，不会中断循环

    - trigger_fn(): 返回待处理项列表，空列表表示本轮无工作。
    - process_fn(item): 处理单条；返回值未使用，仅便于打日志。
    - interval_sec: 每轮结束后的休眠秒数。
    - once: True 时执行一轮后退出（仅用于测试或单次拉取）。
    - label: 用于日志前缀。
    - verbose: 是否打印周期/异常等信息。
    - on_exit: 正常或 KeyboardInterrupt 退出时调用的回调（如 update_worker_status(idle)）。
    - on_idle: 当 trigger_fn 返回空列表时调用（可选，用于打印「无任务」等）。
    - log_file: 可选的日志文件路径，用于写入错误信息。
    """
    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                # 1. 检查触发条件
                items = trigger_fn()
                if not items and on_idle:
                    on_idle()
                
                # 2. 执行动作（对每个触发项）
                for item in items:
                    try:
                        process_fn(item)
                    except Exception as e:
                        # process_fn 中的异常不会导致循环退出
                        if log_file:
                            try:
                                from datetime import datetime
                                from pathlib import Path
                                log_path = Path(log_file)
                                log_path.parent.mkdir(parents=True, exist_ok=True)
                                with open(log_path, "a", encoding="utf-8") as log_f:
                                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    log_f.write(f"\n[{ts}] ❌ 处理项异常 (周期 {cycle}): {e}\n")
                                    traceback.print_exc(file=log_f)
                                    log_f.flush()
                            except Exception:
                                pass
                        if verbose:
                            traceback.print_exc()
                        # 继续处理下一个 item
                        continue
                
                # 3. 如果 once=True，执行一轮后退出（仅用于测试）
                if once:
                    break
            except Exception as e:
                # trigger_fn 或其他外层异常也不会导致循环退出
                if log_file:
                    try:
                        from datetime import datetime
                        from pathlib import Path
                        log_path = Path(log_file)
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(log_path, "a", encoding="utf-8") as log_f:
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            log_f.write(f"\n[{ts}] ❌ 扫描循环异常 (周期 {cycle}): {e}\n")
                            traceback.print_exc(file=log_f)
                            log_f.flush()
                    except Exception:
                        pass  # 日志写入失败不影响处理
                
                if verbose:
                    traceback.print_exc()
                # 单轮异常不退出，继续下一轮
            
            # 4. 休眠后继续下一轮（除非 once=True）
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

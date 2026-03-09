"""
统一的任务扫描器 — 所有 agent 使用相同的循环逻辑

所有 agent 都使用统一的目录结构：
- input_dir (tasks/): 输入目录，由其他 agent 或人类写入任务
- output_dir (reports/): 输出目录，工作完成后的总结

触发逻辑根据 agent 类型不同：
- Secretary/Worker: 观察自己的 input_dir 是否有文件
- Boss: 观察自己的 input_dir（全局目标）或监控的 worker 的 output_dir 是否有新报告
- Recycler: 扫描所有 agent 的 output_dir 查找报告文件

会话管理：
- 首次调用（无 session）：使用完整提示词（角色定义 + 工作流）
- 后续调用（有 session）：使用简短续轮提示词，恢复已有会话

agent 自主管理文件：
- scanner 只负责触发，不移动任何文件
- agent 自行读取任务文件、完成工作、移动/删除文件、写 report

注意：具体的 agent 类型定义已移至 secretary/agent_types/ 目录

执行范围: 仅 execution_scope 为 task / hire / recycle 的任务会被执行；
  monitor 等其它类型不进入执行流程（见 config.EXECUTABLE_TASK_TYPES）。
  任务文件可通过 <!-- execution_scope: monitor --> 等标注类型，未标注时视为 task。

工作流程:
1. 持续扫描 input_dir 文件夹（统一触发规则）
2. 如果有文件，调用 agent_type.process_task（单次阻塞调用）
3. agent 自行完成所有工作后返回，scanner 继续下一轮扫描
"""
import os
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime

import re

import secretary.config as cfg
from secretary.config import EXECUTABLE_TASK_TYPES
from secretary.agent_config import AgentConfig, TerminationCondition, TriggerCondition
from secretary.agent_loop import run_loop

# 确保输出实时刷新（用于后台运行时日志及时写入）
# 创建一个带自动刷新的 print 函数
_original_print = print


def print(*args, **kwargs):
    """重写 print 函数，默认 flush=True 确保实时输出"""
    if 'flush' not in kwargs:
        kwargs['flush'] = True
    _original_print(*args, **kwargs)

# 当前 scanner 进程 ID
_PID = os.getpid()


# ============================================================
#  任务文件解析
# ============================================================

def _get_task_execution_scope(task_file: Path) -> str:
    """
    从任务文件中解析 execution_scope，用于判断是否需被 scanner 执行。
    约定: 文件内容中的 <!-- execution_scope: X -->，X 为 task/hire/recycle/monitor 等。
    若未标注，默认为 "task"（保持与旧任务兼容，会被执行）。
    """
    try:
        content = task_file.read_text(encoding="utf-8")
        m = re.search(r"<!--\s*execution_scope:\s*(\w+)\s*-->", content)
        if m:
            return m.group(1).strip().lower()
    except Exception:
        pass
    return "task"


def _is_executable_task(task_file: Path) -> bool:
    """仅 task、hire、recycle 类型的任务会被执行；monitor 等不进入执行流程。"""
    scope = _get_task_execution_scope(task_file)
    return scope in EXECUTABLE_TASK_TYPES


# ============================================================
#  统一扫描器：统一的触发规则和处理逻辑
# ============================================================

def _get_trigger_debug_info(config: AgentConfig) -> str:
    """
    获取触发检查的详细信息（用于debug日志）
    返回字符串描述为什么触发或没有触发
    """
    trigger = config.trigger
    info_parts = []
    
    # 1. 自定义触发函数
    if trigger.custom_trigger_fn:
        try:
            result = trigger.custom_trigger_fn(config)
            if result:
                info_parts.append(f"自定义触发函数返回 {len(result)} 个文件")
            else:
                info_parts.append("自定义触发函数返回空（未触发）")
        except Exception as e:
            info_parts.append(f"自定义触发函数异常: {e}")
        return " | ".join(info_parts)
    
    # 2. 标准目录监视逻辑
    if not trigger.watch_dirs:
        return "无监视目录配置"
    
    info_parts.append(f"监视目录: {len(trigger.watch_dirs)} 个")
    info_parts.append(f"触发条件: {trigger.condition.value}")
    
    # 检查每个目录的状态
    all_satisfied = True
    for watch_dir in trigger.watch_dirs:
        if not watch_dir.exists():
            if trigger.condition == TriggerCondition.HAS_FILES:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: 目录不存在")
            else:
                info_parts.append(f"{watch_dir.name}: 目录不存在（视为空，满足条件）")
            continue
        
        task_files = [f for f in watch_dir.iterdir() if f.is_file()] if watch_dir.exists() else []
        file_count = len(task_files)
        has_files = file_count > 0
        
        if trigger.condition == TriggerCondition.HAS_FILES:
            if has_files:
                info_parts.append(f"{watch_dir.name}: {file_count} 个文件 ✓")
            else:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: 0 个文件 ✗")
        elif trigger.condition == TriggerCondition.IS_EMPTY:
            if has_files:
                all_satisfied = False
                info_parts.append(f"{watch_dir.name}: {file_count} 个文件（不满足空条件）✗")
            else:
                info_parts.append(f"{watch_dir.name}: 空目录 ✓")
    
    if all_satisfied:
        # 条件满足，检查是否有可执行文件
        if trigger.condition == TriggerCondition.HAS_FILES:
            if config.use_ongoing and config.processing_dir.exists() and config.processing_dir in trigger.watch_dirs:
                ongoing_files = [f for f in config.processing_dir.iterdir() if f.is_file() and _is_executable_task(f)]
                if ongoing_files:
                    info_parts.append(f"→ 触发: processing目录有 {len(ongoing_files)} 个可执行文件")
                    return " | ".join(info_parts)
            
            if config.input_dir in trigger.watch_dirs and config.input_dir.exists():
                all_files = [f for f in config.input_dir.iterdir() if f.is_file()]
                executable = [p for p in all_files if _is_executable_task(p)]
                non_executable = [p for p in all_files if not _is_executable_task(p)]
                
                # 详细记录文件信息
                if all_files:
                    file_details = []
                    for f in all_files[:5]:  # 最多显示5个文件
                        scope = _get_task_execution_scope(f)
                        is_exec = _is_executable_task(f)
                        file_details.append(f"{f.name}(scope={scope},exec={is_exec})")
                    if len(all_files) > 5:
                        file_details.append(f"...共{len(all_files)}个文件")
                    info_parts.append(f"文件列表: {', '.join(file_details)}")
                
                if executable:
                    info_parts.append(f"→ 触发: input目录有 {len(executable)} 个可执行文件")
                    return " | ".join(info_parts)
                else:
                    if non_executable:
                        non_exec_details = []
                        for f in non_executable[:3]:
                            scope = _get_task_execution_scope(f)
                            non_exec_details.append(f"{f.name}(scope={scope})")
                        info_parts.append(f"→ 未触发: input目录有 {len(all_files)} 个文件但无可执行文件 | 非可执行: {', '.join(non_exec_details)}")
                    else:
                        info_parts.append(f"→ 未触发: input目录有 {len(all_files)} 个文件但无可执行文件")
            else:
                info_parts.append("→ 未触发: 条件满足但未找到可执行文件")
        else:
            info_parts.append("→ 触发: 条件满足")
    else:
        info_parts.append("→ 未触发: 条件不满足")
    
    return " | ".join(info_parts)


def _get_trigger_check_details(config: AgentConfig) -> str:
    """
    获取触发检查的详细信息，用于日志输出
    
    Returns:
        触发检查的详细描述字符串
    """
    trigger = config.trigger
    details = []
    
    if trigger.custom_trigger_fn:
        details.append("使用自定义触发函数")
        return " | ".join(details)
    
    if not trigger.watch_dirs:
        details.append("未配置监视目录")
        return " | ".join(details)
    
    condition_str = "有文件时" if trigger.condition == TriggerCondition.HAS_FILES else "为空时"
    details.append(f"条件: {condition_str}")
    
    # 检查每个监视目录的状态
    for watch_dir in trigger.watch_dirs:
        dir_name = watch_dir.name if watch_dir.name else str(watch_dir)
        if not watch_dir.exists():
            details.append(f"{dir_name}: 目录不存在")
            continue
        
        all_task_files = [f for f in watch_dir.iterdir() if f.is_file()]
        all_count = len(all_task_files)
        executable = [f for f in all_task_files if _is_executable_task(f)]
        exec_count = len(executable)
        
        if trigger.condition == TriggerCondition.HAS_FILES:
            if all_count == 0:
                details.append(f"{dir_name}: 无文件")
            elif exec_count == 0:
                details.append(f"{dir_name}: {all_count}个文件但无可执行文件")
                # 列出非可执行文件的原因
                non_exec = [f for f in md_files if not _is_executable_task(f)]
                if non_exec:
                    reasons = []
                    for f in non_exec[:3]:
                        scope = _get_task_execution_scope(f)
                        reasons.append(f"{f.name}(scope={scope})")
                    details.append(f"  非可执行: {', '.join(reasons)}")
            else:
                details.append(f"{dir_name}: {exec_count}/{all_count}个可执行文件")
        else:  # IS_EMPTY
            if all_count == 0:
                details.append(f"{dir_name}: 为空(满足条件)")
            else:
                details.append(f"{dir_name}: {all_count}个文件(不满足条件)")
    
    return " | ".join(details)


def _log_trigger_check_details(config: AgentConfig, result: list[Path]):
    """将触发检查的详细信息写入日志文件"""
    if not config.log_file:
        return
    
    try:
        details = _get_trigger_check_details(config)
        ts = datetime.now().strftime("%H:%M:%S")
        
        status = "✅ 已触发" if result else "⏸️ 未触发"
        if result:
            file_names = ", ".join(f.name for f in result[:3])
            details += f" | 触发文件: {file_names}"
        
        log_line = f"[{ts}] {status} | {details}\n"
        
        log_path = Path(config.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line)
            f.flush()
    except Exception:
        pass  # 日志写入失败不影响主流程


def _unified_trigger(config: AgentConfig) -> list[Path]:
    """
    统一触发规则：根据TriggerConfig配置进行触发
    
    支持：
    1. 自定义触发函数（custom_trigger_fn）
    2. 标准目录监视（watch_dirs + condition）
    3. 虚拟触发文件（create_virtual_file）
    """
    trigger = config.trigger
    
    # 1. 如果提供了自定义触发函数，优先使用
    if trigger.custom_trigger_fn:
        return trigger.custom_trigger_fn(config)
    
    # 2. 标准目录监视逻辑
    if not trigger.watch_dirs:
        return []
    
    # 检查所有监视目录是否满足条件
    all_satisfied = True
    for watch_dir in trigger.watch_dirs:
        if not watch_dir.exists():
            if trigger.condition == TriggerCondition.HAS_FILES:
                all_satisfied = False
                break
            # IS_EMPTY: 目录不存在视为空，满足条件
            continue
        
        dir_files = [f for f in watch_dir.iterdir() if f.is_file()]
        has_files = len(dir_files) > 0
        
        if trigger.condition == TriggerCondition.HAS_FILES:
            if not has_files:
                all_satisfied = False
                break
        elif trigger.condition == TriggerCondition.IS_EMPTY:
            if has_files:
                all_satisfied = False
                break
    
    if not all_satisfied:
        return []
    
    # 3. 条件满足，返回触发文件
    if trigger.condition == TriggerCondition.HAS_FILES:
        # 有文件时触发：返回文件列表
        # 优先处理processing目录（如果存在且use_ongoing=True）
        if config.use_ongoing and config.processing_dir.exists() and config.processing_dir in trigger.watch_dirs:
            candidates = [
                f for f in sorted(
                    (f for f in config.processing_dir.iterdir() if f.is_file()),
                    key=lambda p: p.stat().st_mtime,
                )
                if _is_executable_task(f)
            ]
            if candidates:
                return [candidates[0]]

        # 从input目录取文件
        if config.input_dir in trigger.watch_dirs and config.input_dir.exists():
            all_files = [f for f in config.input_dir.iterdir() if f.is_file()]
            executable = [p for p in all_files if _is_executable_task(p)]
            
            if executable:
                # 按修改时间排序，返回最早的文件
                return [sorted(executable, key=lambda p: p.stat().st_mtime)[0]]
        
        # 从其他监视目录取文件
        result = []
        for watch_dir in trigger.watch_dirs:
            if watch_dir == config.input_dir or watch_dir == config.processing_dir:
                continue
            if watch_dir.exists():
                all_files = [f for f in watch_dir.iterdir() if f.is_file()]
                executable = [p for p in all_files if _is_executable_task(p)]
                if executable:
                    result.extend(executable)
        if result:
            return [sorted(result, key=lambda p: p.stat().st_mtime)[0]]
        
        return []
    
    elif trigger.condition == TriggerCondition.IS_EMPTY:
        # 为空时触发：创建虚拟触发文件（如果需要）
        if trigger.create_virtual_file:
            trigger_file = config.base_dir / trigger.virtual_file_name
            if not trigger_file.exists():
                trigger_file.touch()
            return [trigger_file]
        return []
    


def _get_agent_type(config: AgentConfig):
    """根据配置获取对应的 AgentType 实例（委托给注册表）"""
    from secretary.agent_registry import resolve_agent_type, initialize_registry
    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass
    return resolve_agent_type(config.name)


def _process_chat_request(config: AgentConfig, verbose: bool = True) -> None:
    """
    处理来自 dashboard 的 chat 请求。
    读取 chat_request.json，执行 chat，写入 chat_response.json，然后删除请求文件。
    """
    import json
    from secretary.agents import load_agent_session_id, save_agent_session_id
    from secretary.agent_runner import run_agent
    from secretary.settings import get_model
    from datetime import datetime as _dt
    
    chat_request_file = config.base_dir / "chat_request.json"
    chat_response_file = config.base_dir / "chat_response.json"
    
    if not chat_request_file.exists():
        return
    
    try:
        # 读取请求
        req_data = json.loads(chat_request_file.read_text(encoding="utf-8"))
        message = req_data.get("message", "").strip()
        if not message:
            response = {"ok": False, "error": "empty message", "output": ""}
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            if verbose:
                print(f"[{ts}] 💬 处理 chat 请求: {message[:60]}...")
            
            session_id = load_agent_session_id(config.name)
            
            # 确保 dialog 文件存在
            dialog_dir = config.dialog_dir if config.dialog_dir else config.base_dir / "dialog"
            dialog_dir.mkdir(parents=True, exist_ok=True)
            dialog_file = dialog_dir / "dialog.md"
            
            # 写入用户消息到 dialog
            ts_str = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(dialog_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'─'*60}\n[{ts_str}] 用户 (web)\n{'─'*60}\n")
                f.write(message + "\n")
            
            # 执行 chat
            try:
                result = run_agent(
                    prompt=message,
                    workspace=str(cfg.get_workspace()),
                    model=get_model(),
                    verbose=False,
                    session_id=session_id,
                    dialog_file=dialog_file,
                )
                if result.stats.session_id:
                    save_agent_session_id(config.name, result.stats.session_id)
                
                response = {
                    "ok": result.success,
                    "output": result.output,
                    "session_id": result.stats.session_id,
                }
            except Exception as e:
                import traceback
                error_msg = str(e)
                traceback_str = traceback.format_exc()
                if verbose:
                    print(f"[{ts}] ❌ Chat 执行失败: {error_msg}")
                response = {
                    "ok": False,
                    "error": error_msg,
                    "traceback": traceback_str,
                    "output": "",
                }
        
        # 写入响应
        chat_response_file.write_text(
            json.dumps(response, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        # 删除请求文件（表示已处理）
        chat_request_file.unlink()
        
        if verbose:
            ts = datetime.now().strftime("%H:%M:%S")
            status = "✅" if response.get("ok") else "❌"
            print(f"[{ts}] {status} Chat 完成")
            
    except Exception as e:
        import traceback
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] ❌ 处理 chat 请求异常: {e}")
        traceback.print_exc()
        # 写入错误响应
        try:
            chat_response_file.write_text(
                json.dumps({
                    "ok": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "output": "",
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            chat_request_file.unlink()
        except:
            pass


def _process_one_unified(config: AgentConfig, file_path: Path, verbose: bool) -> None:
    """
    统一处理逻辑：使用集中化的 agent 类型定义
    """
    try:
        agent_type = _get_agent_type(config)
        agent_type.process_task(config, file_path, verbose=verbose)
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{ts}] ❌ 处理任务失败: {file_path.name} | 错误: {e}")
        traceback.print_exc()
        raise


# 旧的 _process_* 函数已被集中化的 agent 类型定义替代
# 现在使用 _process_one_unified -> _get_agent_type -> agent_type.process_task


def run_unified_scanner(config: AgentConfig, once: bool = False, verbose: bool = True) -> None:
    """
    统一扫描循环：所有 agent 使用相同的循环逻辑
    
    循环模式：检查触发条件 -> 执行动作 -> 休眠 -> 检查触发条件 -> ...
    
    通过配置区分终止条件和提示词。
    默认持续运行（once=False），除非明确指定 once=True（仅用于测试）。
    """
    # 确保目录存在
    config.input_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.log_file is not None:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
    config.stats_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    if config.dialog_dir is not None:
        config.dialog_dir.mkdir(parents=True, exist_ok=True)

    # Recycler需要额外的solved和unsolved目录
    if config.first_round_prompt == "recycler_first.md":
        recycler_dir = config.base_dir
        (recycler_dir / "solved").mkdir(parents=True, exist_ok=True)
        (recycler_dir / "unsolved").mkdir(parents=True, exist_ok=True)

    # 所有 agent 都注册并更新状态（现在所有 agent 都使用 UNTIL_FILE_DELETED）
    from secretary.agents import register_agent, update_worker_status
    agent_type = _get_agent_type(config)
    agent_type_name = agent_type.name if hasattr(agent_type, 'name') else "worker"
    # 确保 agent 已注册
    from secretary.agents import get_worker
    if not get_worker(config.name):
        register_agent(config.name, agent_type=agent_type_name, description="")
    update_worker_status(config.name, "busy", pid=_PID)

    label = config.label
    ts = datetime.now().strftime("%H:%M:%S")
    mode = "单次" if once else f"持续 (间隔 {cfg.SCAN_INTERVAL}s)"
    print(f"\n[{ts}] {label} 启动 PID={_PID} | {mode}")
    print(f"   tasks: {config.input_dir.name}/ → reports: {config.output_dir.name}/")
    
    # 打印触发条件
    trigger = config.trigger
    trigger_info = []
    
    if trigger.custom_trigger_fn:
        trigger_info.append("自定义触发函数")
    else:
        if trigger.watch_dirs:
            watch_paths = [str(d.resolve()) for d in trigger.watch_dirs]
            trigger_info.append(f"监视: {', '.join(watch_paths)}")
        
        condition_str = "有文件时" if trigger.condition == TriggerCondition.HAS_FILES else "为空时"
        trigger_info.append(f"条件: {condition_str}")
        
        if trigger.create_virtual_file:
            trigger_info.append(f"虚拟文件: {trigger.virtual_file_name}")
    
    if trigger_info:
        print(f"   触发: {' | '.join(trigger_info)}")

    # 检查监视目录是否存在，不存在则打印警告（帮助用户发现 kai base 配置错误）
    if not trigger.custom_trigger_fn and trigger.watch_dirs:
        for watch_dir in trigger.watch_dirs:
            if not watch_dir.exists():
                print(f"   ⚠️ 监视目录不存在: {watch_dir}")
                print(f"      请检查 'kai base' 配置是否正确 (当前 WORKSPACE={cfg.WORKSPACE})")

    # 打印 known agents（排除自身）
    try:
        from secretary.agents import list_workers
        all_agents = list_workers()
        other_agents = [a for a in all_agents if a.get('name') != config.name]
        if other_agents:
            agent_list = [f"{a.get('name', '?')}({a.get('type', '?')})" for a in other_agents]
            print(f"   known agents: {', '.join(agent_list)}")
        else:
            print("   known agents: (none)")
    except Exception:
        pass

    # 用于记录上次触发检查日志的时间
    last_trigger_log_time = [time.time()]  # 使用列表以便在闭包中修改
    TRIGGER_LOG_INTERVAL = 30  # 每 30 秒输出一次

    def trigger_fn():
        # 优先检查 chat 请求（阻塞正常扫描，优先处理 chat）
        chat_request_file = config.base_dir / "chat_request.json"
        if chat_request_file.exists():
            try:
                import json
                req_data = json.loads(chat_request_file.read_text(encoding="utf-8"))
                # 返回一个特殊的"chat任务"标记，process_fn 会识别并处理
                return [Path("__CHAT_REQUEST__")]
            except Exception as e:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] ⚠️ 读取 chat 请求失败: {e}")
                # 删除损坏的请求文件
                try:
                    chat_request_file.unlink()
                except:
                    pass
        
        try:
            result = _unified_trigger(config)
            
            # 每 30 秒输出一次触发检查的详细结果
            current_time = time.time()
            if current_time - last_trigger_log_time[0] >= TRIGGER_LOG_INTERVAL:
                last_trigger_log_time[0] = current_time
                _log_trigger_check_details(config, result)
        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ❌ 触发检查异常: {e}")
            traceback.print_exc()
            result = []

        if result:
            ts = datetime.now().strftime("%H:%M:%S")
            names = ", ".join(f.name for f in result[:3])
            print(f"\n[{ts}] 🔔 触发: {names}")

        return result

    def process_fn(file_path: Path):
        from secretary.agents import set_agent_executing, increment_completed_tasks
        
        # 检查是否是 chat 请求
        if str(file_path) == "__CHAT_REQUEST__":
            _process_chat_request(config, verbose)
            return
        
        set_agent_executing(config.name, True)
        increment_completed_tasks(config.name)

        ts = datetime.now().strftime("%H:%M:%S")
        size = file_path.stat().st_size if file_path.exists() else 0
        print(f"[{ts}] ▶ 处理: {file_path.name} ({size}B)")

        try:
            _process_one_unified(config, file_path, verbose)
        except Exception as e:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] ❌ {file_path.name}: {e}")
            traceback.print_exc()
        finally:
            set_agent_executing(config.name, False)

    def on_idle():
        pass  # 空闲时不打印，减少日志噪音

    def on_exit():
        if config.termination == TerminationCondition.UNTIL_FILE_DELETED:
            try:
                from secretary.agents import update_worker_status
                update_worker_status(config.name, "idle", pid=None)
            except Exception:
                pass

    run_loop(
        trigger_fn=trigger_fn,
        process_fn=process_fn,
        interval_sec=cfg.SCAN_INTERVAL,
        once=once,
        label=label,
        verbose=verbose,
        on_idle=on_idle,
        on_exit=on_exit,
        log_file=str(config.log_file) if config.log_file else None,
    )


# ============================================================
#  入口函数：使用统一的配置系统
# ============================================================

def run_kai_scanner(once: bool = False, verbose: bool = False, secretary_name: str = "kai") -> None:
    """运行 Secretary 任务扫描器：扫描 agents/<name>/tasks/，每项调用 run_secretary，输出写入 <name>/logs。"""
    from secretary.agent_types import SecretaryAgent
    agent_type = SecretaryAgent()
    config = agent_type.build_config(cfg.BASE_DIR, secretary_name)
    run_unified_scanner(config, once=once, verbose=verbose)


def _build_config_for(type_name: str, agent_name: str) -> AgentConfig:
    """通过注册表构建 AgentConfig，取代原来的 build_*_config wrappers"""
    from secretary.agent_registry import get_agent_type
    agent_type = get_agent_type(type_name)
    if not agent_type:
        raise ValueError(f"未知 agent 类型: {type_name}")
    return agent_type.build_config(cfg.BASE_DIR, agent_name)


def run_scanner(once: bool = False, verbose: bool = True, worker_name: str | None = None) -> None:
    """运行 Worker 扫描循环"""
    config = _build_config_for("worker", worker_name or cfg.DEFAULT_WORKER_NAME)
    run_unified_scanner(config, once=once, verbose=verbose)


def run_boss_scanner(once: bool = False, verbose: bool = True, boss_name: str | None = None) -> None:
    """运行 Boss 扫描循环"""
    if not boss_name:
        raise ValueError("Boss名称不能为空")
    config = _build_config_for("boss", boss_name)
    run_unified_scanner(config, once=once, verbose=verbose)


def run_recycler_scanner(once: bool = False, verbose: bool = True, recycler_name: str | None = None) -> None:
    """运行 Recycler 扫描循环"""
    config = _build_config_for("recycler", recycler_name or "recycler")
    run_unified_scanner(config, once=once, verbose=verbose)


if __name__ == "__main__":
    import argparse
    from secretary.agent_registry import get_agent_type, initialize_registry, list_agent_types
    
    parser = argparse.ArgumentParser(description="任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--worker", type=str, default=None, help="worker 名称（向后兼容）")
    parser.add_argument("--boss", type=str, default=None, help="boss 名称（向后兼容）")
    parser.add_argument("--recycler", type=str, default=None, help="recycler 名称（向后兼容）")
    parser.add_argument("--agent", type=str, default=None, help="agent 名称（通用参数）")
    parser.add_argument("--type", type=str, default=None, help="agent 类型（通用参数，如 worker, boss, recycler 或自定义类型）")
    args = parser.parse_args()
    
    # 确保注册表已初始化
    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass
    
    # 优先使用新的通用参数
    if args.agent and args.type:
        # 使用通用方式启动
        agent_type_instance = get_agent_type(args.type)
        if agent_type_instance is None:
            available_types = list_agent_types()
            print(f"❌ 未知的 agent 类型: {args.type}")
            if available_types:
                print(f"   可用类型: {', '.join(available_types)}")
            sys.exit(1)
        
        config = agent_type_instance.build_config(cfg.BASE_DIR, args.agent)
        run_unified_scanner(config, once=args.once, verbose=not args.quiet)
    elif args.boss:
        # 向后兼容：Boss
        run_boss_scanner(once=args.once, verbose=not args.quiet, boss_name=args.boss)
    elif args.recycler:
        # 向后兼容：Recycler
        run_recycler_scanner(once=args.once, verbose=not args.quiet, recycler_name=args.recycler)
    else:
        # 向后兼容：Worker（默认）
        run_scanner(once=args.once, verbose=not args.quiet, worker_name=args.worker)

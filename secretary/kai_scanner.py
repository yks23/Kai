"""
Kai (秘书) 任务扫描器 — 后台主循环

工作流程:
1. 持续扫描 agents/kai/tasks/ 目录
2. 发现文件时：
   - 读取文件内容（用户请求）
   - 移动文件到 agents/kai/assigned/（保留历史）
   - 调用 run_secretary() 处理任务
   - 将输出写入 agents/kai/logs/scanner.log
3. 循环执行
"""
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime

import secretary.config as cfg
from secretary.secretary_agent import run_secretary

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


def run_kai_scanner(once: bool = False, verbose: bool = False):
    """
    运行 kai 的扫描器主循环
    
    参数:
    - once: 只执行一个周期后退出（用于测试或单次拉取）
    - verbose: 是否输出详细信息
    """
    tasks_dir = cfg.KAI_TASKS_DIR
    assigned_dir = cfg.KAI_ASSIGNED_DIR
    logs_dir = cfg.KAI_LOGS_DIR
    
    # 确保目录存在
    tasks_dir.mkdir(parents=True, exist_ok=True)
    assigned_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # 日志文件
    log_file = logs_dir / "scanner.log"
    
    label = "🤖 kai"
    
    print("=" * 60)
    print(f"{label} 启动  (PID={_PID})")
    print(f"   任务目录: {tasks_dir}")
    print(f"   已分配目录: {assigned_dir}")
    print(f"   日志文件: {log_file}")
    print(f"   扫描间隔: {cfg.SCAN_INTERVAL}s")
    print(f"   模式: {'单次' if once else '持续运行（循环直到 Ctrl+C）'}")
    print("=" * 60)
    
    cycle = 0
    
    try:
        while True:
            cycle += 1
            try:
                # 扫描 tasks/ 目录
                task_files = list(tasks_dir.glob("*.md"))
                
                if task_files:
                    # 处理第一个任务文件
                    task_file = task_files[0]
                    
                    if verbose:
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"\n📋 [{label} PID={_PID}] [{ts}] 发现任务: {task_file.name}")
                    
                    # 读取任务内容
                    try:
                        request = task_file.read_text(encoding="utf-8").strip()
                    except Exception as e:
                        print(f"⚠️ [{label} PID={_PID}] 读取任务文件失败: {e}", file=sys.stderr)
                        # 移动到 assigned/ 但标记为错误
                        error_file = assigned_dir / f"error-{task_file.name}"
                        shutil.move(str(task_file), str(error_file))
                        continue
                    
                    # 移动文件到 assigned/
                    assigned_file = assigned_dir / task_file.name
                    try:
                        shutil.move(str(task_file), str(assigned_file))
                    except Exception as e:
                        print(f"⚠️ [{label} PID={_PID}] 移动任务文件失败: {e}", file=sys.stderr)
                        continue
                    
                    # 调用 run_secretary() 处理任务
                    # 输出重定向到日志文件，并实时刷新
                    try:
                        # 创建一个带 flush 的文件包装类
                        class FlushFile:
                            def __init__(self, file):
                                self.file = file
                            
                            def write(self, s):
                                self.file.write(s)
                                self.file.flush()  # 实时刷新
                            
                            def flush(self):
                                self.file.flush()
                            
                            def __getattr__(self, name):
                                return getattr(self.file, name)
                        
                        # 打开日志文件（追加模式）
                        with open(log_file, "a", encoding="utf-8", buffering=1) as log_f:  # line buffering
                            flush_log = FlushFile(log_f)
                            
                            # 保存原始 stdout 和 stderr
                            original_stdout = sys.stdout
                            original_stderr = sys.stderr
                            
                            try:
                                # 重定向输出到日志文件（带实时刷新）
                                sys.stdout = flush_log
                                sys.stderr = flush_log
                                
                                # 写入分隔符和时间戳
                                log_f.write("\n" + "=" * 60 + "\n")
                                log_f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 处理任务: {task_file.name}\n")
                                log_f.write("=" * 60 + "\n\n")
                                log_f.flush()
                                
                                # 调用 run_secretary()，verbose=True 确保输出所有对话过程
                                run_secretary(request, verbose=True)
                                
                                log_f.write("\n" + "=" * 60 + "\n")
                                log_f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 任务完成: {task_file.name}\n")
                                log_f.write("=" * 60 + "\n\n")
                                log_f.flush()
                                
                            finally:
                                # 恢复原始输出
                                sys.stdout = original_stdout
                                sys.stderr = original_stderr
                        
                        if verbose:
                            ts = datetime.now().strftime("%H:%M:%S")
                            print(f"✅ [{label} PID={_PID}] [{ts}] 任务处理完成: {task_file.name}")
                            print(f"   日志已写入: {log_file}")
                    
                    except Exception as e:
                        # 记录错误到日志
                        try:
                            with open(log_file, "a", encoding="utf-8") as log_f:
                                log_f.write(f"\n⚠️ 处理任务时发生错误: {e}\n")
                                traceback.print_exc(file=log_f)
                        except Exception:
                            pass
                        
                        print(f"⚠️ [{label} PID={_PID}] 处理任务时发生错误: {e}", file=sys.stderr)
                        if verbose:
                            traceback.print_exc(file=sys.stderr)
                
                else:
                    if verbose:
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"💤 [{label} PID={_PID}] [{ts}] 没有新任务，{cfg.SCAN_INTERVAL}s 后再扫描...")
            
            except Exception as e:
                # 单周期内异常不退出：记录后继续下一轮
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"\n⚠️ [{label} PID={_PID}] [{ts}] 本周期异常（已忽略，继续下一轮）: {e}",
                      file=sys.stderr)
                if verbose:
                    traceback.print_exc(file=sys.stderr)
            
            if once:
                break
            
            time.sleep(cfg.SCAN_INTERVAL)
    
    except KeyboardInterrupt:
        print(f"\n\n🛑 {label} 已停止 (PID={_PID}, 共 {cycle} 个周期)")
    finally:
        pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kai 任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()
    
    run_kai_scanner(once=args.once, verbose=args.verbose)


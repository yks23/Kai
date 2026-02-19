"""
Kai (秘书) 任务扫描器 — 入口与兼容层

实际逻辑已合并到 secretary.scanner：run_kai_scanner 扫描 agents/kai/tasks/，
每项读内容、移到 assigned/、调用 run_secretary，输出写入 agents/kai/logs/scanner.log。
本模块保留为薄包装，便于 `python -m secretary.kai_scanner` 与现有导入兼容。
"""
from secretary.scanner import run_kai_scanner

__all__ = ["run_kai_scanner"]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kai 任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()
    run_kai_scanner(once=args.once, verbose=args.verbose)

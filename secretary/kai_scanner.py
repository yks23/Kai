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
    import os
    parser = argparse.ArgumentParser(description="Secretary 任务扫描器")
    parser.add_argument("--once", action="store_true", help="只执行一次")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--name", type=str, help="Secretary 名称（默认从环境变量 SECRETARY_NAME 获取，否则使用 'kai'）")
    args = parser.parse_args()
    # 优先使用命令行参数，其次使用环境变量，最后使用默认值 "kai"
    secretary_name = args.name or os.environ.get("SECRETARY_NAME", "kai")
    run_kai_scanner(once=args.once, verbose=args.verbose, secretary_name=secretary_name)

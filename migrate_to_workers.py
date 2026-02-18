#!/usr/bin/env python3
"""
迁移脚本：将现有结构迁移到 workers/ 目录结构

1. 将根目录下的工人目录 (如 kaisen/, ninja/) 移动到 workers/
2. 将 tasks/ 下的任务分发到各个 worker 的 tasks/ 下
"""
import shutil
import json
from pathlib import Path

BASE_DIR = Path.cwd()
TASKS_DIR = BASE_DIR / "tasks"
WORKERS_DIR = BASE_DIR / "workers"
WORKERS_FILE = BASE_DIR / "workers.json"


def migrate_worker_dirs():
    """将根目录下的工人目录移动到 workers/"""
    print("📦 迁移工人目录到 workers/...")
    WORKERS_DIR.mkdir(exist_ok=True)
    
    # 读取现有工人注册表
    if WORKERS_FILE.exists():
        with open(WORKERS_FILE, "r", encoding="utf-8") as f:
            reg = json.load(f)
        worker_names = set(reg.get("workers", {}).keys())
    else:
        worker_names = set()
    
    # 查找根目录下的工人目录（排除系统目录）
    system_dirs = {"tasks", "ongoing", "report", "stats", "logs", "skills", 
                   "solved-report", "unsolved-report", "testcases", "workers",
                   "secretary", "secretary_agent.egg-info", "secretary_pkg",
                   ".cursor", ".git"}
    
    moved = 0
    for item in BASE_DIR.iterdir():
        if item.is_dir() and item.name not in system_dirs:
            # 检查是否是工人目录（有 tasks/ 或 ongoing/ 子目录）
            if (item / "tasks").exists() or (item / "ongoing").exists():
                dest = WORKERS_DIR / item.name
                if dest.exists():
                    print(f"   ⚠️ {item.name}/ 已存在于 workers/，跳过")
                else:
                    try:
                        shutil.move(str(item), str(dest))
                        print(f"   ✅ {item.name}/ → workers/{item.name}/")
                        moved += 1
                    except Exception as e:
                        print(f"   ❌ 移动 {item.name}/ 失败: {e}")
    
    print(f"   共迁移 {moved} 个工人目录\n")
    return moved


def distribute_tasks():
    """将 tasks/ 下的任务分发到各个 worker 的 tasks/ 下"""
    print("📋 分发 tasks/ 下的任务到各 worker...")
    
    if not TASKS_DIR.exists():
        print("   ℹ️ tasks/ 目录不存在，跳过")
        return 0
    
    # 读取工人列表
    if not WORKERS_FILE.exists():
        print("   ⚠️ workers.json 不存在，无法分发任务")
        return 0
    
    with open(WORKERS_FILE, "r", encoding="utf-8") as f:
        reg = json.load(f)
    workers = list(reg.get("workers", {}).keys())
    
    if not workers:
        print("   ℹ️ 没有已注册的工人，任务保留在 tasks/")
        return 0
    
    # 获取所有任务文件
    task_files = list(TASKS_DIR.glob("*.md"))
    if not task_files:
        print("   ℹ️ tasks/ 下没有任务文件")
        return 0
    
    print(f"   找到 {len(task_files)} 个任务，将分发到 {len(workers)} 个工人")
    
    # 轮询分发
    distributed = 0
    for i, task_file in enumerate(task_files):
        worker_name = workers[i % len(workers)]
        worker_tasks_dir = WORKERS_DIR / worker_name / "tasks"
        worker_tasks_dir.mkdir(parents=True, exist_ok=True)
        
        dest = worker_tasks_dir / task_file.name
        if dest.exists():
            # 如果目标已存在，添加时间戳
            stem = task_file.stem
            suffix = task_file.suffix
            from datetime import datetime
            ts = datetime.now().strftime("%H%M%S")
            dest = worker_tasks_dir / f"{stem}-{ts}{suffix}"
        
        try:
            shutil.move(str(task_file), str(dest))
            print(f"   ✅ {task_file.name} → workers/{worker_name}/tasks/{dest.name}")
            distributed += 1
        except Exception as e:
            print(f"   ❌ 移动 {task_file.name} 失败: {e}")
    
    print(f"   共分发 {distributed} 个任务\n")
    return distributed


def main():
    print("=" * 60)
    print("🔄 迁移到 workers/ 目录结构")
    print("=" * 60)
    print()
    
    # 1. 迁移工人目录
    migrate_worker_dirs()
    
    # 2. 分发任务
    distribute_tasks()
    
    print("=" * 60)
    print("✅ 迁移完成！")
    print("=" * 60)
    print()
    print("💡 现在所有工人都在 workers/ 目录下")
    print("💡 任务已分发到各工人的 tasks/ 目录")


if __name__ == "__main__":
    main()


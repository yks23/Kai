"""
Scheduler utilities for Learn input stream.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import secretary.config as cfg
from secretary.input_streams.learn import LearnStreamError, run_learn_stream


DEFAULT_INTERVAL_MINUTES = 360


def _workspace_root() -> Path:
    return (cfg.WORKSPACE or Path.cwd()).resolve()


def _learn_stream_dir() -> Path:
    p = cfg.BASE_DIR / "learn_stream"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_default_config_path() -> Path:
    return _learn_stream_dir() / "config.json"


def get_scheduler_pid_path() -> Path:
    return _learn_stream_dir() / "scheduler.pid.json"


def get_scheduler_log_path() -> Path:
    return _learn_stream_dir() / "scheduler.log"


def get_last_run_path() -> Path:
    return _learn_stream_dir() / "last_run.json"


def _default_config() -> dict[str, Any]:
    ws = _workspace_root()
    return {
        "output_dir": str((ws / "learn_sync").resolve()),
        "cookie": "",
        "cookie_file": "",
        "csrf_token": "",
        "semester_id": "",
        "only": "all",
        "lang": "zh",
        "base_url": "https://learn.tsinghua.edu.cn",
        "timeout": 20.0,
        "dry_run": False,
        "homework_attachments": True,
        "schedule_interval_minutes": DEFAULT_INTERVAL_MINUTES,
        # study-machine workflow
        "study_enabled": False,
        "study_solver_agent": "",
        "study_solver_timeout_minutes": 120,
        "study_output_dir": str((ws / "study_homework_output").resolve()),
        "study_email_enabled": False,
        "study_email_to": "",
        "study_email_from": "",
        "study_email_subject_template": "[study] 作业完成通知 ({count})",
        "study_smtp_host": "",
        "study_smtp_port": 587,
        "study_smtp_user": "",
        "study_smtp_password": "",
        "study_smtp_use_tls": True,
    }


def load_stream_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve() if config_path else get_default_config_path()
    if not path.exists():
        return _default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LearnStreamError(f"配置文件不是有效 JSON: {path}") from exc
    merged = _default_config()
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save_stream_config(config: dict[str, Any], config_path: str | Path | None = None) -> Path:
    path = Path(config_path).expanduser().resolve() if config_path else get_default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _resolve_credentials(config: dict[str, Any]) -> tuple[str, str]:
    cookie = str(config.get("cookie") or "").strip()
    cookie_file = str(config.get("cookie_file") or "").strip()
    if not cookie and cookie_file:
        cookie_path = Path(cookie_file).expanduser().resolve()
        if not cookie_path.exists():
            raise LearnStreamError(f"cookie_file 不存在: {cookie_path}")
        cookie = cookie_path.read_text(encoding="utf-8").strip()
    if not cookie:
        cookie = os.environ.get("LEARN_COOKIE", "").strip()

    csrf_token = str(config.get("csrf_token") or "").strip()
    if not csrf_token:
        csrf_token = os.environ.get("LEARN_CSRF", "").strip()

    if not cookie:
        raise LearnStreamError("缺少 Cookie（配置文件 / LEARN_COOKIE 均为空）")
    if not csrf_token:
        raise LearnStreamError("缺少 CSRF Token（配置文件 / LEARN_CSRF 均为空）")
    return cookie, csrf_token


def _write_last_run(payload: dict[str, Any]) -> None:
    path = get_last_run_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_once_from_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config = load_stream_config(config_path)
    cookie, csrf_token = _resolve_credentials(config)

    mode = str(config.get("only") or "all")
    include_files = mode in ("all", "files")
    include_homework = mode in ("all", "homework")
    interval_minutes = int(config.get("schedule_interval_minutes") or DEFAULT_INTERVAL_MINUTES)

    result = run_learn_stream(
        cookie=cookie,
        csrf_token=csrf_token,
        output_dir=str(config.get("output_dir") or (_workspace_root() / "learn_sync")),
        semester_id=(str(config.get("semester_id") or "").strip() or None),
        include_files=include_files,
        include_homework=include_homework,
        download_homework_attachments=bool(config.get("homework_attachments", True)),
        dry_run=bool(config.get("dry_run", False)),
        lang=str(config.get("lang") or "zh"),
        base_url=str(config.get("base_url") or "https://learn.tsinghua.edu.cn"),
        timeout=float(config.get("timeout") or 20.0),
    )

    study_result: dict[str, Any] | None = None
    if bool(config.get("study_enabled", False)):
        from secretary.study.workflow import run_study_workflow_for_manifest

        study_result = run_study_workflow_for_manifest(result, config)
        result["study"] = study_result

    _write_last_run(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "success": True,
            "interval_minutes": interval_minutes,
            "stats": result.get("stats", {}),
            "study": study_result,
            "config_path": str(Path(config_path).expanduser().resolve()) if config_path else str(get_default_config_path()),
        }
    )
    return result


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_scheduler_status() -> dict[str, Any]:
    pid_path = get_scheduler_pid_path()
    last_run_path = get_last_run_path()
    status: dict[str, Any] = {
        "running": False,
        "pid": None,
        "config_path": str(get_default_config_path()),
        "interval_minutes": None,
        "started_at": None,
        "last_run": None,
    }
    if pid_path.exists():
        try:
            data = json.loads(pid_path.read_text(encoding="utf-8"))
            pid = int(data.get("pid"))
            status.update(
                {
                    "pid": pid,
                    "config_path": data.get("config_path"),
                    "interval_minutes": data.get("interval_minutes"),
                    "started_at": data.get("started_at"),
                    "running": _pid_alive(pid),
                }
            )
        except Exception:
            status["running"] = False
    if last_run_path.exists():
        try:
            status["last_run"] = json.loads(last_run_path.read_text(encoding="utf-8"))
        except Exception:
            status["last_run"] = None
    return status


def _write_pid_file(pid: int, config_path: Path, interval_minutes: int) -> None:
    payload = {
        "pid": pid,
        "config_path": str(config_path),
        "interval_minutes": interval_minutes,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    get_scheduler_pid_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def start_scheduler(
    *,
    config_path: str | Path | None = None,
    interval_minutes: int | None = None,
    run_now: bool = True,
) -> dict[str, Any]:
    status = get_scheduler_status()
    if status.get("running"):
        return {"started": False, "reason": "already_running", "status": status}

    cfg_path = Path(config_path).expanduser().resolve() if config_path else get_default_config_path()
    config = load_stream_config(cfg_path)
    if interval_minutes is None:
        interval_minutes = int(config.get("schedule_interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    if interval_minutes <= 0:
        raise LearnStreamError("interval_minutes 必须 > 0")

    # Persist resolved interval to config for consistency.
    config["schedule_interval_minutes"] = interval_minutes
    save_stream_config(config, cfg_path)

    cmd = [
        sys.executable,
        "-m",
        "secretary.cli",
        "learn-stream-schedule",
        "loop",
        "--config",
        str(cfg_path),
        "--interval-minutes",
        str(interval_minutes),
    ]
    if run_now:
        cmd.append("--run-now")
    env = os.environ.copy()
    env["SECRETARY_WORKSPACE"] = str(cfg.WORKSPACE)

    log_file = get_scheduler_log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_file, "a", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=str(cfg.BASE_DIR),
        env=env,
    )

    _write_pid_file(proc.pid, cfg_path, interval_minutes)
    return {
        "started": True,
        "pid": proc.pid,
        "config_path": str(cfg_path),
        "interval_minutes": interval_minutes,
        "log_file": str(log_file),
    }


def stop_scheduler(timeout_sec: float = 10.0) -> dict[str, Any]:
    pid_path = get_scheduler_pid_path()
    if not pid_path.exists():
        return {"stopped": False, "reason": "not_running"}
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
        pid = int(data.get("pid"))
    except Exception as exc:
        pid_path.unlink(missing_ok=True)
        return {"stopped": False, "reason": f"bad_pid_file: {exc}"}

    if not _pid_alive(pid):
        pid_path.unlink(missing_ok=True)
        return {"stopped": False, "reason": "stale_pid", "pid": pid}

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return {"stopped": False, "reason": f"sigterm_failed: {exc}", "pid": pid}

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _pid_alive(pid):
            pid_path.unlink(missing_ok=True)
            return {"stopped": True, "pid": pid, "forced": False}
        time.sleep(0.2)

    # Force stop if graceful shutdown failed.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        return {"stopped": False, "reason": f"sigkill_failed: {exc}", "pid": pid}

    pid_path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid, "forced": True}


def run_scheduler_loop(
    *,
    config_path: str | Path | None = None,
    interval_minutes: int | None = None,
    run_now: bool = False,
) -> None:
    cfg_path = Path(config_path).expanduser().resolve() if config_path else get_default_config_path()
    config = load_stream_config(cfg_path)
    if interval_minutes is None:
        interval_minutes = int(config.get("schedule_interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    if interval_minutes <= 0:
        raise LearnStreamError("interval_minutes 必须 > 0")

    stop_flag = {"stop": False}

    def _on_term(_signum, _frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    _write_pid_file(os.getpid(), cfg_path, interval_minutes)
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️ learn-stream scheduler started "
        f"(interval={interval_minutes}m, run_now={run_now})"
    )

    first = True
    while not stop_flag["stop"]:
        if run_now or not first:
            ts = datetime.now().strftime("%H:%M:%S")
            try:
                result = run_once_from_config(cfg_path)
                stats = result.get("stats", {})
                print(
                    f"[{ts}] ✅ pull done | courses={stats.get('courses_processed', 0)} "
                    f"files={stats.get('courseware_downloaded', 0)}/{stats.get('courseware_items', 0)} "
                    f"homework_attach={stats.get('homework_attachments_downloaded', 0)}/{stats.get('homework_attachments', 0)} "
                    f"errors={stats.get('errors', 0)}"
                )
                study = result.get("study")
                if isinstance(study, dict):
                    print(
                        f"[{ts}] 🤖 study | new={study.get('new_jobs_submitted', 0)} "
                        f"completed={study.get('completed_jobs', 0)} pending={study.get('pending_jobs', 0)} "
                        f"timeouts={study.get('timeout_jobs', 0)}"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[{ts}] ❌ pull failed: {exc}")
                _write_last_run(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "success": False,
                        "error": str(exc),
                        "interval_minutes": interval_minutes,
                        "config_path": str(cfg_path),
                    }
                )

        first = False
        sleep_total = interval_minutes * 60
        slept = 0
        while slept < sleep_total and not stop_flag["stop"]:
            time.sleep(1)
            slept += 1

    get_scheduler_pid_path().unlink(missing_ok=True)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 👋 learn-stream scheduler stopped")

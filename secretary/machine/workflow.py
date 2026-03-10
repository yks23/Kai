"""
e2e-HW-machine workflow runtime.

Single ingress:
  learn_homework_tick

Single egress:
  email_sent
"""

from __future__ import annotations

import json
import re
import shutil
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import secretary.config as cfg
from secretary.agent_paths import _worker_reports_dir, _worker_tasks_dir
from secretary.agents import get_worker, list_workers


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_slug(raw: str, fallback: str = "item") -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip())
    s = s.strip("._-")
    return s or fallback


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _workflow_dir() -> Path:
    p = cfg.BASE_DIR / "machine_workflow"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path() -> Path:
    return _workflow_dir() / "state.json"


def _default_state() -> dict[str, Any]:
    return {
        "workflow_name": "e2e-HW-machine",
        "updated_at": _utc_now(),
        "jobs": {},
        "email_history": [],
    }


def _load_state() -> dict[str, Any]:
    state = _read_json(_state_path(), _default_state())
    if not isinstance(state, dict):
        return _default_state()
    state.setdefault("jobs", {})
    state.setdefault("email_history", [])
    return state


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    _write_json(_state_path(), state)


def _discover_homeworks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    semester_id = str(manifest.get("semester_id") or "unknown")
    discovered: list[dict[str, Any]] = []
    for course in manifest.get("courses", []) or []:
        if not isinstance(course, dict):
            continue
        course_id = str(course.get("course_id") or "unknown")
        course_name = str(course.get("course_name") or "course")
        course_dir_raw = str(course.get("course_dir") or "").strip()
        if not course_dir_raw:
            continue
        course_dir = Path(course_dir_raw)
        index_path = course_dir / "homework" / "index.json"
        index_payload = _read_json(index_path, {"items": []})
        items = index_payload.get("items", []) if isinstance(index_payload, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            xszyid = str(item.get("xszyid") or "")
            zyid = str(item.get("zyid") or "")
            homework_id = xszyid or zyid or _safe_slug(str(item.get("title") or "homework"), "homework")
            key = f"{semester_id}:{course_id}:{homework_id}"
            discovered.append(
                {
                    "key": key,
                    "semester_id": semester_id,
                    "course_id": course_id,
                    "course_name": course_name,
                    "homework_id": homework_id,
                    "title": str(item.get("title") or f"homework_{homework_id}"),
                    "deadline": item.get("deadline"),
                    "target_dir": str(item.get("target_dir") or ""),
                }
            )
    return discovered


def _resolve_solver_agent(config: dict[str, Any]) -> str:
    preferred = str(config.get("machine_solver_agent") or "").strip()
    if preferred and get_worker(preferred):
        return preferred
    workers = list_workers()
    for w in workers:
        if w.get("type") == "secretary":
            return str(w["name"])
    if workers:
        return str(workers[0]["name"])
    raise RuntimeError("No available agent for machine workflow. Please hire/start an agent first.")


def _task_markdown(job: dict[str, Any], export_dir: Path) -> str:
    return (
        f"# e2e-HW-machine 自动作业任务\n\n"
        f"- 作业键: `{job['key']}`\n"
        f"- 学期: `{job['semester_id']}`\n"
        f"- 课程: `{job['course_name']}` (`{job['course_id']}`)\n"
        f"- 作业标题: `{job['title']}`\n"
        f"- 截止时间: `{job.get('deadline') or '-'}`\n"
        f"- 输入目录: `{job.get('target_dir') or '-'}`\n"
        f"- 输出目录: `{export_dir}`\n\n"
        f"请执行以下步骤：\n"
        f"1. 阅读输入目录中的题目与附件。\n"
        f"2. 完成作业并输出一份清晰答案。\n"
        f"3. 在输出目录写 `answer.md`，内容包含解题步骤与最终答案。\n"
        f"4. 在 agent 报告中给出总结与潜在风险。\n"
    )


def _send_email(config: dict[str, Any], subject: str, body: str) -> dict[str, Any]:
    host = str(config.get("machine_smtp_host") or "").strip()
    if not host:
        return {"sent": False, "reason": "smtp_host_missing"}
    port = int(config.get("machine_smtp_port") or 587)
    smtp_user = str(config.get("machine_smtp_user") or "").strip()
    smtp_password = str(config.get("machine_smtp_password") or "").strip()
    if not smtp_password:
        smtp_password = str(__import__("os").environ.get("MACHINE_SMTP_PASSWORD", "")).strip()
    sender = str(config.get("machine_email_from") or smtp_user).strip()
    to_raw = str(config.get("machine_email_to") or "").strip()
    to_addrs = [x.strip() for x in to_raw.split(",") if x.strip()]
    if not sender or not to_addrs:
        return {"sent": False, "reason": "sender_or_recipient_missing"}

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    use_tls = bool(config.get("machine_smtp_use_tls", True))
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
        if smtp_user:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)
    return {"sent": True, "to": to_addrs, "host": host, "port": port}


def build_machine_workflow_json(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg_data = config or {}
    return {
        "project": "e2e-HW-machine",
        "alias": "machine",
        "version": "1.0",
        "entry_node": "learn_homework_tick",
        "exit_node": "email_sent",
        "nodes": [
            {
                "id": "learn_homework_tick",
                "type": "ingress",
                "description": "Scheduled learn-stream pull tick",
                "config": {
                    "interval_minutes": int(cfg_data.get("schedule_interval_minutes") or 360),
                },
            },
            {
                "id": "detect_new_homework",
                "type": "transform",
                "description": "Detect unprocessed homework delta",
                "config": {"state_file": str(_state_path())},
            },
            {
                "id": "dispatch_solver_task",
                "type": "action",
                "description": "Submit homework task into machine agent workflow",
                "config": {
                    "solver_agent": str(cfg_data.get("machine_solver_agent") or ""),
                },
            },
            {
                "id": "collect_and_export",
                "type": "action",
                "description": "Collect agent report and export deliverables",
                "config": {
                    "output_dir": str(
                        cfg_data.get("machine_output_dir")
                        or (cfg.WORKSPACE / "machine_homework_output").resolve()
                    ),
                },
            },
            {
                "id": "email_sent",
                "type": "egress",
                "description": "Send completion notification email",
                "config": {
                    "enabled": bool(cfg_data.get("machine_email_enabled", False)),
                    "to": str(cfg_data.get("machine_email_to") or ""),
                },
            },
        ],
        "edges": [
            {"from": "learn_homework_tick", "to": "detect_new_homework"},
            {"from": "detect_new_homework", "to": "dispatch_solver_task"},
            {"from": "dispatch_solver_task", "to": "collect_and_export"},
            {"from": "collect_and_export", "to": "email_sent"},
        ],
    }


def run_machine_workflow_for_manifest(manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    state = _load_state()
    jobs: dict[str, Any] = state.get("jobs", {})
    solver_agent = _resolve_solver_agent(config)
    solver_tasks_dir = _worker_tasks_dir(solver_agent)
    solver_reports_dir = _worker_reports_dir(solver_agent)
    solver_tasks_dir.mkdir(parents=True, exist_ok=True)
    solver_reports_dir.mkdir(parents=True, exist_ok=True)

    output_root = Path(
        str(config.get("machine_output_dir") or (cfg.WORKSPACE / "machine_homework_output").resolve())
    ).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    discovered = _discover_homeworks(manifest)
    new_jobs = 0
    completed_jobs = 0
    completed_items: list[dict[str, Any]] = []
    now = _utc_now()

    # 1) submit new homework tasks
    for hw in discovered:
        key = hw["key"]
        if key in jobs:
            continue
        job_slug = _safe_slug(key, "homework")
        task_stem = f"machine-hw-{job_slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        task_file = solver_tasks_dir / f"{task_stem}.md"
        export_dir = output_root / _safe_slug(hw["semester_id"], "semester") / _safe_slug(hw["course_id"], "course") / _safe_slug(hw["homework_id"], "homework")
        export_dir.mkdir(parents=True, exist_ok=True)
        task_file.write_text(_task_markdown(hw, export_dir), encoding="utf-8")
        jobs[key] = {
            **hw,
            "status": "submitted",
            "solver_agent": solver_agent,
            "task_file": str(task_file),
            "task_stem": task_stem,
            "submitted_at": now,
            "export_dir": str(export_dir),
        }
        new_jobs += 1

    # 2) collect reports for submitted jobs
    timeout_minutes = int(config.get("machine_solver_timeout_minutes") or 120)
    for key, job in list(jobs.items()):
        if not isinstance(job, dict):
            continue
        if job.get("status") != "submitted":
            continue
        report_path = solver_reports_dir / f"{job.get('task_stem')}-report.md"
        if report_path.exists():
            export_dir = Path(job["export_dir"]).resolve()
            export_dir.mkdir(parents=True, exist_ok=True)
            exported_report = export_dir / "report.md"
            shutil.copy2(report_path, exported_report)
            # ensure answer placeholder exists for downstream integrations
            answer_md = export_dir / "answer.md"
            if not answer_md.exists():
                answer_md.write_text(
                    "# machine answer placeholder\n\n请在后续流程中将最终答案写入该文件。\n",
                    encoding="utf-8",
                )
            metadata_path = export_dir / "metadata.json"
            _write_json(
                metadata_path,
                {
                    "key": key,
                    "solver_agent": job.get("solver_agent"),
                    "task_file": job.get("task_file"),
                    "report_file": str(report_path),
                    "exported_report": str(exported_report),
                    "completed_at": _utc_now(),
                },
            )
            job["status"] = "completed"
            job["completed_at"] = _utc_now()
            job["report_file"] = str(report_path)
            job["exported_report"] = str(exported_report)
            completed_jobs += 1
            completed_items.append(
                {
                    "key": key,
                    "title": job.get("title"),
                    "course_name": job.get("course_name"),
                    "export_dir": str(export_dir),
                    "report": str(exported_report),
                }
            )
            continue

        # timeout handling
        try:
            submitted = datetime.fromisoformat(str(job.get("submitted_at")).replace("Z", "+00:00"))
            elapsed_min = (datetime.now(timezone.utc) - submitted.astimezone(timezone.utc)).total_seconds() / 60.0
            if elapsed_min > timeout_minutes:
                job["status"] = "timeout"
                job["timeout_at"] = _utc_now()
        except Exception:
            pass

    email_result: dict[str, Any] | None = None
    if completed_items and bool(config.get("machine_email_enabled", False)):
        tpl = str(config.get("machine_email_subject_template") or "[machine] 作业完成通知 ({count})")
        subject = tpl.replace("{count}", str(len(completed_items)))
        lines = [
            "e2e-HW-machine 已完成以下作业：",
            "",
        ]
        for item in completed_items:
            lines.append(
                f"- {item['course_name']} | {item['title']} | 导出目录: {item['export_dir']}"
            )
        body = "\n".join(lines)
        try:
            email_result = _send_email(config, subject, body)
        except Exception as exc:  # noqa: BLE001
            email_result = {"sent": False, "reason": str(exc)}
        state.setdefault("email_history", []).append(
            {
                "time": _utc_now(),
                "subject": subject,
                "result": email_result,
                "count": len(completed_items),
            }
        )
        state["email_history"] = state["email_history"][-20:]

    state["jobs"] = jobs
    _save_state(state)
    return {
        "enabled": True,
        "solver_agent": solver_agent,
        "discovered": len(discovered),
        "new_jobs_submitted": new_jobs,
        "completed_jobs": completed_jobs,
        "pending_jobs": sum(
            1 for _, v in jobs.items() if isinstance(v, dict) and v.get("status") == "submitted"
        ),
        "timeout_jobs": sum(
            1 for _, v in jobs.items() if isinstance(v, dict) and v.get("status") == "timeout"
        ),
        "completed_items": completed_items,
        "email": email_result,
        "state_file": str(_state_path()),
        "output_root": str(output_root),
    }


def get_machine_workflow_status() -> dict[str, Any]:
    state = _load_state()
    jobs = state.get("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
    return {
        "state_file": str(_state_path()),
        "updated_at": state.get("updated_at"),
        "job_total": len(jobs),
        "job_submitted": sum(1 for _, v in jobs.items() if isinstance(v, dict) and v.get("status") == "submitted"),
        "job_completed": sum(1 for _, v in jobs.items() if isinstance(v, dict) and v.get("status") == "completed"),
        "job_timeout": sum(1 for _, v in jobs.items() if isinstance(v, dict) and v.get("status") == "timeout"),
        "email_history": list(state.get("email_history", []))[-5:],
    }


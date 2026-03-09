"""
Kai Web Dashboard
-----------------
Lightweight HTTP server that exposes a modern GUI dashboard on localhost:PORT.

Endpoints
  GET  /                              → SPA HTML page
  GET  /api/status                    → JSON: all agents summary
  GET  /api/graph                     → JSON: {nodes, edges} for graph view
  GET  /api/agent/<name>              → JSON: full agent info
  GET  /api/agent/<name>/log          → JSON: {content, size}
  GET  /api/agent/<name>/dialog       → JSON: {content, size}
  GET  /api/agent/<name>/tasks        → JSON: [{name, content, size}, ...]
  GET  /api/agent/<name>/reports      → JSON: [{name, content, size}, ...]
  GET  /api/agent/<name>/report/<f>   → JSON: {name, content}
  GET  /api/agent/<name>/known_agents → JSON: [name, ...] or null
  POST /api/task                      → {agent, content} → submit task
  POST /api/hire                      → {name, type, description} → register agent
  POST /api/fire/<name>               → delete agent
  POST /api/link                      → {from_agent, to_agent} → add link
  DELETE /api/link                    → {from_agent, to_agent} → remove link
  GET  /events                        → SSE stream (agent status + log tails)
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import secretary.config as cfg
from secretary.agent_paths import (
    _worker_logs_dir,
    _worker_ongoing_dir,
    _worker_reports_dir,
    _worker_tasks_dir,
)
from secretary.agents import (
    list_workers,
    register_agent,
    remove_worker,
    get_graph_data,
    get_agent_known_agents,
    add_known_agent_link,
    remove_known_agent_link,
)


# ──────────────────────────────────────────────────────────────
# Server-side logger  (keeps last 200 records; also writes to stderr)
# ──────────────────────────────────────────────────────────────

class _ServerLog:
    """Thread-safe ring-buffer of server log records."""

    def __init__(self, maxlen: int = 200):
        self._lock = threading.Lock()
        self._buf: collections.deque[dict] = collections.deque(maxlen=maxlen)

    def _emit(self, level: str, msg: str, tb: str = ""):
        ts = time.strftime("%H:%M:%S")
        rec = {"ts": ts, "level": level, "msg": msg, "tb": tb}
        with self._lock:
            self._buf.append(rec)
        # also print to stderr so `kai dashboard` terminal shows it
        line = f"[{ts}] [{level}] {msg}"
        try:
            print(line, file=sys.stderr, flush=True)
            if tb:
                print(tb, file=sys.stderr, flush=True)
        except UnicodeEncodeError:
            try:
                sys.stderr.buffer.write((line + "\n").encode("utf-8", errors="replace"))
                sys.stderr.buffer.flush()
            except Exception:
                pass
        # broadcast to connected clients
        try:
            _broadcaster.push("server_log", rec)
        except Exception:
            pass

    def info(self, msg: str):
        self._emit("INFO", msg)

    def error(self, msg: str, exc: BaseException | None = None):
        tb = traceback.format_exc() if exc is not None else ""
        self._emit("ERROR", msg, tb)

    def request(self, method: str, path: str, status: int, ms: float):
        color_level = "OK" if status < 400 else ("WARN" if status < 500 else "ERROR")
        self._emit(color_level, f"{method} {path} → {status}  ({ms:.0f}ms)")

    def records(self) -> list[dict]:
        with self._lock:
            return list(self._buf)


_slog = _ServerLog()


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _agent_summary(info: dict) -> dict:
    """Build the JSON blob for a single agent."""
    name = info["name"]
    pid = info.get("pid")
    is_running = bool(pid and _pid_alive(pid))

    tasks_dir    = _worker_tasks_dir(name)
    ongoing_dir  = _worker_ongoing_dir(name)
    logs_dir     = _worker_logs_dir(name)
    reports_dir  = _worker_reports_dir(name)
    dialog_file  = logs_dir.parent / "dialog" / "dialog.md"
    log_file     = logs_dir / "scanner.log"

    def _files(d: Path) -> list[dict]:
        if not d.exists():
            return []
        return sorted(
            [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
             for f in d.iterdir() if f.is_file()],
            key=lambda x: x["mtime"],
        )

    reports = _files(reports_dir)
    reports.reverse()           # newest first

    return {
        **info,
        "is_running": is_running,
        "pending_tasks": _files(tasks_dir),
        "ongoing_tasks": _files(ongoing_dir),
        "reports": reports[:20],
        "has_log":    log_file.exists(),
        "log_size":   log_file.stat().st_size   if log_file.exists()   else 0,
        "has_dialog": dialog_file.exists(),
        "dialog_size": dialog_file.stat().st_size if dialog_file.exists() else 0,
    }


def _read_file_safe(path: Path, max_bytes: int = 512 * 1024) -> str:
    """Read a file, returning at most max_bytes from the end."""
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()          # skip partial first line
            return f.read()
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────
# SSE broadcaster
# ──────────────────────────────────────────────────────────────

class _SSEBroadcaster:
    """Thread-safe broadcaster: push events → all connected SSE clients."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queues: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._queues = [x for x in self._queues if x is not q]

    def push(self, event: str, data: Any):
        payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        with self._lock:
            dead = []
            for q in self._queues:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._queues = [x for x in self._queues if x is not q]


_broadcaster = _SSEBroadcaster()


def _watch_loop():
    """Background thread: poll files every 2 s, push SSE events on change."""
    import secretary.command_system as cmd_sys
    
    last_sizes: dict[str, int] = {}
    last_pending: dict[str, int] = {}    # track pending_count per agent
    last_executing: dict[str, bool] = {} # track executing state per agent
    last_command_check: float = 0.0
    COMMAND_CHECK_INTERVAL = 2.0  # 每 2 秒检查一次命令
    
    while True:
        try:
            agents = list_workers()
            summaries = [_agent_summary(a) for a in agents]
            _broadcaster.push("agents", summaries)

            # --- detect dynamic changes for graph animations ---
            # Which agents are currently executing? (used to guess flow source)
            executing_agents = [a["name"] for a in agents if a.get("executing")]

            for a in agents:
                name = a["name"]
                pc = a.get("pending_count", 0)
                ex = bool(a.get("executing"))

                # pending_count increased → a task was written to this agent
                prev_pc = last_pending.get(name, pc)
                if pc > prev_pc:
                    # Try to guess source: an executing agent that knows this agent
                    known_by = []
                    for other in agents:
                        if other["name"] == name:
                            continue
                        oknown = other.get("known_agents")
                        if oknown is not None and name in oknown and other.get("executing"):
                            known_by.append(other["name"])
                    source = known_by[0] if len(known_by) == 1 else None
                    _broadcaster.push("task_flow", {
                        "from": source,   # may be None
                        "to": name,
                        "count": pc - prev_pc,
                    })
                last_pending[name] = pc

                # executing state changed
                prev_ex = last_executing.get(name, ex)
                if ex != prev_ex:
                    _broadcaster.push("agent_exec", {"name": name, "executing": ex})
                last_executing[name] = ex

                # --- file size tracking ---
                logs_dir = _worker_logs_dir(name)
                log_file = logs_dir / "scanner.log"
                dialog_file = logs_dir.parent / "dialog" / "dialog.md"
                for fpath in (log_file, dialog_file):
                    key = str(fpath)
                    sz = fpath.stat().st_size if fpath.exists() else 0
                    if sz != last_sizes.get(key, -1):
                        last_sizes[key] = sz
                        kind = "log" if "scanner" in fpath.name else "dialog"
                        _broadcaster.push(f"file_{kind}_{name}", {"size": sz})
            
            # --- process agent commands ---
            import time as time_module
            current_time = time_module.time()
            if current_time - last_command_check >= COMMAND_CHECK_INTERVAL:
                last_command_check = current_time
                try:
                    results = cmd_sys.process_all_pending_commands()
                    if results:
                        for r in results:
                            _slog.info(f"Command processed: {r.get('file', 'unknown')} - {'success' if r.get('success') else 'failed'}")
                        _broadcaster.push("commands_processed", {"count": len(results)})
                except Exception as e:
                    _slog.error(f"Command processing error: {e}")
        except Exception:
            pass
        time.sleep(2)


_watcher = threading.Thread(target=_watch_loop, daemon=True, name="kai-dashboard-watcher")
_watcher.start()


# ──────────────────────────────────────────────────────────────
# Scanner launcher (wraps cli._start_agent_scanner with real error capture)
# ──────────────────────────────────────────────────────────────

def _start_agent_scanner_safe(agent_name: str, agent_type: str) -> tuple[bool, str]:
    """
    Call cli._start_agent_scanner and capture the actual error if it fails.
    Returns (started: bool, error_message: str).
    """
    import io, subprocess, sys, os
    import secretary.config as cfg
    from secretary.agents import update_worker_status, _worker_logs_dir
    from secretary.agent_registry import get_agent_type, initialize_registry

    try:
        initialize_registry(cfg.CUSTOM_AGENTS_DIR)
    except Exception:
        pass

    try:
        agent_type_instance = get_agent_type(agent_type)
        if agent_type_instance is None:
            return False, f"未知的 agent 类型: {agent_type!r}（注册表中找不到）"

        log_dir = _worker_logs_dir(agent_name)
        log_dir.mkdir(parents=True, exist_ok=True)
        scanner_log_file = log_dir / "scanner.log"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["SECRETARY_WORKSPACE"] = str(cfg.WORKSPACE)

        if agent_type == "secretary":
            sub_cmd = [sys.executable, "-c",
                       f"from secretary.scanner import run_kai_scanner; "
                       f"run_kai_scanner(once=False, verbose=True, secretary_name='{agent_name}')"]
        elif agent_type == "recycler":
            sub_cmd = [sys.executable, "-m", "secretary.recycler"]
            env["KAI_RECYCLE_BACKGROUND"] = "1"
        else:
            sub_cmd = [sys.executable, "-m", "secretary.scanner",
                       "--agent", agent_name, "--type", agent_type, "--quiet"]

        log_fh = open(scanner_log_file, "a", encoding="utf-8", buffering=1)

        # On Windows, detach child from the dashboard console so that if
        # the child crashes it won't send CTRL_C_EVENT back to us.
        popen_kw: dict = {}
        if sys.platform == "win32":
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW        = 0x08000000
            popen_kw["creationflags"] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        proc = subprocess.Popen(
            sub_cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(cfg.BASE_DIR),
            env=env,
            **popen_kw,
        )

        update_worker_status(agent_name, "busy", pid=proc.pid)

        # also register in cli's in-process table (best-effort)
        try:
            from secretary.cli import _register_process
            _register_process(agent_name, agent_type, proc.pid)
        except Exception:
            pass

        _slog.info(f"scanner 已启动: {agent_name} ({agent_type})  PID={proc.pid}")
        return True, ""

    except Exception as exc:
        tb = traceback.format_exc()
        _slog.error(f"启动 scanner 失败: {agent_name} ({agent_type}) — {exc}", exc)
        return False, f"{exc}\n\n{tb}"


# ──────────────────────────────────────────────────────────────
# HTTP handler
# ──────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    _last_status: int = 200   # updated by _json; used by logging finally-blocks

    # ── routing ────────────────────────────────────────────────

    def do_GET(self):
        self._response_sent = False
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/") or "/"
        parts = [x for x in p.split("/") if x]
        _t0 = time.monotonic()

        try:
            if p == "/":
                self._serve_html()
            elif p in ("/api/status", "/api/agents"):
                self._api_status()
            elif p == "/api/graph":
                self._api_graph()
            elif p == "/api/commands":
                self._api_commands_list()
            elif p == "/api/agent-skills":
                self._api_agent_skills_list()
            elif p == "/api/server-log":
                self._json(_slog.records())
            elif len(parts) == 3 and parts[0] == "api" and parts[1] == "agent":
                self._api_agent(parts[2])
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agent":
                name, kind = parts[2], parts[3]
                if kind == "log":
                    self._api_file(name, "log")
                elif kind == "dialog":
                    self._api_file(name, "dialog")
                elif kind == "tasks":
                    self._api_tasks(name)
                elif kind == "reports":
                    self._api_reports(name)
                elif kind == "known_agents":
                    self._api_known_agents(name)
                else:
                    self._404()
            elif len(parts) == 5 and parts[0] == "api" and parts[1] == "agent" and parts[3] == "report":
                self._api_report_file(parts[2], parts[4])
            elif p == "/api/types":
                self._api_types()
            elif p == "/events":
                self._sse()
            else:
                self._404()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-response — nothing to do
        except Exception as exc:
            tb = traceback.format_exc()
            _slog.error(f"GET {p} — {exc}", exc)
            self._json({"error": str(exc), "traceback": tb}, 500)
        finally:
            if p != "/events":
                _slog.request("GET", p, self._last_status, (time.monotonic()-_t0)*1000)

    def do_POST(self):
        self._response_sent = False
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/")
        parts = [x for x in p.split("/") if x]
        _t0 = time.monotonic()
        try:
            if p == "/api/task":
                self._post_task()
            elif p == "/api/hire":
                self._post_hire()
            elif p == "/api/link":
                self._post_link(add=True)
            elif len(parts) == 3 and parts[0] == "api" and parts[1] == "fire":
                self._post_fire(parts[2])
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agent" and parts[3] == "task":
                self._post_agent_task(parts[2])
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agent" and parts[3] == "upload":
                self._post_agent_upload(parts[2])
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agent" and parts[3] == "chat":
                self._post_agent_chat(parts[2])
            elif len(parts) == 4 and parts[0] == "api" and parts[1] == "agent" and parts[3] == "start":
                self._post_agent_start(parts[2])
            elif p == "/api/types":
                self._post_type()
            else:
                self._404()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-response — nothing to do
        except Exception as exc:
            tb = traceback.format_exc()
            _slog.error(f"POST {p} — {exc}", exc)
            self._json({"error": str(exc), "traceback": tb}, 500)
        finally:
            _slog.request("POST", p, self._last_status, (time.monotonic()-_t0)*1000)

    def do_DELETE(self):
        self._response_sent = False
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/")
        _t0 = time.monotonic()
        parts = [x for x in p.split("/") if x]
        try:
            if p == "/api/link":
                self._post_link(add=False)
            elif len(parts) == 3 and parts[0] == "api" and parts[1] == "types":
                self._delete_type(parts[2])
            else:
                self._404()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-response — nothing to do
        except Exception as exc:
            tb = traceback.format_exc()
            _slog.error(f"DELETE {p} — {exc}", exc)
            self._json({"error": str(exc), "traceback": tb}, 500)
        finally:
            _slog.request("DELETE", p, self._last_status, (time.monotonic()-_t0)*1000)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── api handlers ───────────────────────────────────────────

    def _api_status(self):
        agents = list_workers()
        self._json([_agent_summary(a) for a in agents])

    def _api_agent(self, name: str):
        from secretary.agents import get_worker
        info = get_worker(name)
        if not info:
            self._json({"error": "not found"}, 404)
            return
        self._json(_agent_summary(info))

    def _api_file(self, name: str, kind: str):
        logs_dir = _worker_logs_dir(name)
        if kind == "log":
            path = logs_dir / "scanner.log"
        else:
            path = logs_dir.parent / "dialog" / "dialog.md"
        content = _read_file_safe(path)
        self._json({"content": content, "size": len(content.encode())})

    def _api_tasks(self, name: str):
        tasks_dir = _worker_tasks_dir(name)
        result = []
        if tasks_dir.exists():
            for f in sorted((f for f in tasks_dir.iterdir() if f.is_file()), key=lambda x: x.stat().st_mtime):
                result.append({
                    "name": f.name,
                    "content": _read_file_safe(f, 8192),
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        self._json(result)

    def _api_reports(self, name: str):
        reports_dir = _worker_reports_dir(name)
        result = []
        if reports_dir.exists():
            files = sorted((f for f in reports_dir.iterdir() if f.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)
            for f in files[:30]:
                result.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        self._json(result)

    def _api_report_file(self, name: str, filename: str):
        reports_dir = _worker_reports_dir(name)
        path = reports_dir / filename
        if not path.exists() or not path.is_relative_to(reports_dir):
            self._json({"error": "not found"}, 404)
            return
        self._json({"name": filename, "content": _read_file_safe(path, 64 * 1024)})

    def _api_graph(self):
        self._json(get_graph_data())
    
    def _api_commands_list(self):
        """GET /api/commands — 列出所有命令（pending, processed, failed）"""
        import secretary.command_system as cmd_sys
        import json
        
        pending = cmd_sys.list_pending_commands()
        processed_dir = cmd_sys.COMMANDS_PROCESSED_DIR
        failed_dir = cmd_sys.COMMANDS_FAILED_DIR
        
        result = {
            "pending": [],
            "processed": [],
            "failed": [],
        }
        
        # 读取 pending 命令
        for cmd_file in pending:
            try:
                data = json.loads(cmd_file.read_text(encoding="utf-8"))
                result["pending"].append({
                    "file": cmd_file.name,
                    "command_type": data.get("command_type"),
                    "agent_name": data.get("agent_name"),
                    "params": data.get("params", {}),
                    "created_at": data.get("created_at"),
                })
            except:
                pass
        
        # 读取 processed 命令（最近 20 个）
        if processed_dir.exists():
            processed_files = sorted(
                [f for f in processed_dir.iterdir() if f.is_file() and f.suffix == ".json"],
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )[:20]
            for cmd_file in processed_files:
                try:
                    data = json.loads(cmd_file.read_text(encoding="utf-8"))
                    result["processed"].append({
                        "file": cmd_file.name,
                        "command_type": data.get("command_type"),
                        "agent_name": data.get("agent_name"),
                        "params": data.get("params", {}),
                        "created_at": data.get("created_at"),
                        "processed_at": data.get("processed_at"),
                        "result": data.get("result"),
                    })
                except:
                    pass
        
        # 读取 failed 命令（最近 20 个）
        if failed_dir.exists():
            failed_files = sorted(
                [f for f in failed_dir.iterdir() if f.is_file() and f.suffix == ".json"],
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )[:20]
            for cmd_file in failed_files:
                try:
                    data = json.loads(cmd_file.read_text(encoding="utf-8"))
                    result["failed"].append({
                        "file": cmd_file.name,
                        "command_type": data.get("command_type"),
                        "agent_name": data.get("agent_name"),
                        "params": data.get("params", {}),
                        "created_at": data.get("created_at"),
                        "failed_at": data.get("failed_at"),
                        "error": data.get("error"),
                    })
                except:
                    pass
        
        self._json(result)
    
    def _api_agent_skills_list(self):
        """GET /api/agent-skills — 列出所有可用的 agent 技能"""
        import secretary.config as cfg
        from pathlib import Path
        
        result = []
        seen_skills = set()
        
        # 1. 先从 BASE_DIR/agent_skills 加载（用户自定义）
        skills_dir = cfg.BASE_DIR / "agent_skills"
        if skills_dir.exists():
            for skill_file in sorted(skills_dir.glob("*.md")):
                skill_name = skill_file.stem
                if skill_name in seen_skills:
                    continue
                seen_skills.add(skill_name)
                content = skill_file.read_text(encoding="utf-8")
                # 提取描述（第一行 > 开头）
                desc = ""
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("> "):
                        desc = stripped[2:]
                        break
                result.append({
                    "name": skill_name,
                    "description": desc or skill_name,
                })
        
        # 2. 再从包内 secretary/agent_skills 加载（内置技能）
        pkg_skills_dir = Path(__file__).parent.parent / "agent_skills"
        if pkg_skills_dir.exists():
            for skill_file in sorted(pkg_skills_dir.glob("*.md")):
                skill_name = skill_file.stem
                if skill_name in seen_skills:
                    continue  # 用户自定义优先
                seen_skills.add(skill_name)
                content = skill_file.read_text(encoding="utf-8")
                # 提取描述（第一行 > 开头）
                desc = ""
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("> "):
                        desc = stripped[2:]
                        break
                result.append({
                    "name": skill_name,
                    "description": desc or skill_name,
                })
        
        self._json(result)
  
    def _api_known_agents(self, name: str):
        from secretary.agents import get_worker
        if not get_worker(name):
            self._json({"error": "not found"}, 404)
            return
        self._json(get_agent_known_agents(name))   # None = knows all

    # ── Custom Types API ──

    def _api_types(self):
        from secretary.agents import list_custom_types
        builtins = [
            {"name": "worker",    "base_type": "worker",    "builtin": True, "description": "执行编程任务"},
            {"name": "secretary", "base_type": "secretary", "builtin": True, "description": "任务分类与分配"},
            {"name": "boss",      "base_type": "boss",      "builtin": True, "description": "监控 worker 并生成任务"},
            {"name": "recycler",  "base_type": "recycler",  "builtin": True, "description": "审查完成报告"},
        ]
        customs = [dict(t, builtin=False) for t in list_custom_types()]
        self._json(builtins + customs)

    def _post_type(self):
        from secretary.agents import register_custom_type
        from secretary.agent_registry import CustomAgentType, _registry
        body = self._read_json()
        if body is None:
            return
        tname      = body.get("name", "").strip()
        base       = body.get("base_type", "worker").strip()
        desc       = body.get("description", "").strip()
        first_p    = body.get("first_prompt", "").strip()
        continue_p = body.get("continue_prompt", "").strip()
        if not tname:
            self._json({"error": "name required"}, 400)
            return
        if not first_p:
            self._json({"error": "first_prompt required"}, 400)
            return
        if not continue_p:
            # 自动生成一个简单的续轮提示词
            continue_p = "任务文件: `{task_file}`\n\n{known_agents_section}\n\n按照之前的工作流程继续。"
        try:
            info = register_custom_type(tname, base, first_p, continue_p, desc)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
            return
        # 同时在运行时注册表中注册
        instance = CustomAgentType(info)
        _registry.register(tname, instance)
        _broadcaster.push("types_changed", {"action": "create", "name": tname})
        self._json({"ok": True, "type": info})

    def _delete_type(self, type_name: str):
        from secretary.agents import delete_custom_type
        if not delete_custom_type(type_name):
            self._json({"error": f"type '{type_name}' not found"}, 404)
            return
        _broadcaster.push("types_changed", {"action": "delete", "name": type_name})
        self._json({"ok": True})

    def _post_hire(self):
        body = self._read_json()
        if body is None:
            return
        name       = body.get("name", "").strip().lower()
        atype      = body.get("type", "worker").strip()
        desc       = body.get("description", "").strip()
        start_now  = bool(body.get("start", False))
        known_agents = body.get("known_agents", [])
        # 确保 known_agents 是字符串列表
        if isinstance(known_agents, list):
            known_agents = [str(ka).strip() for ka in known_agents if ka]
        else:
            known_agents = []
        skills = body.get("skills", [])
        # 确保 skills 是字符串列表
        if isinstance(skills, list):
            skills = [str(s).strip() for s in skills if s]
        else:
            skills = []
        if not name:
            self._json({"error": "name required"}, 400)
            return
        from secretary.agents import get_custom_type
        valid_builtins = {"worker", "secretary", "boss", "recycler"}
        if atype not in valid_builtins and not get_custom_type(atype):
            self._json({"error": f"invalid type: {atype}"}, 400)
            return
        info = register_agent(name, agent_type=atype, description=desc, known_agents=known_agents, skills=skills)
        _broadcaster.push("agents_changed", {"action": "hire", "name": name})

        started = False
        start_error = ""
        if start_now:
            started, start_error = _start_agent_scanner_safe(name, atype)

        self._json({"ok": True, "agent": info, "started": started,
                    "start_error": start_error if start_error else None})

    def _post_fire(self, name: str):
        from secretary.agents import get_worker
        if not get_worker(name):
            self._json({"error": "not found"}, 404)
            return
        remove_worker(name)
        _broadcaster.push("agents_changed", {"action": "fire", "name": name})
        self._json({"ok": True})

    def _post_link(self, add: bool):
        body = self._read_json()
        if body is None:
            return
        fr = body.get("from_agent", "").strip()
        to = body.get("to_agent", "").strip()
        if not fr or not to:
            self._json({"error": "from_agent and to_agent required"}, 400)
            return
        if add:
            add_known_agent_link(fr, to)
        else:
            remove_known_agent_link(fr, to)
        _broadcaster.push("graph_changed", {"action": "link" if add else "unlink",
                                             "from": fr, "to": to})
        self._json({"ok": True})

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            self._json({"error": "invalid json"}, 400)
            return None

    def _post_task(self):
        body = self._read_json()
        if body is None:
            return
        agent_name = body.get("agent", "").strip()
        content    = body.get("content", "").strip()
        if not agent_name or not content:
            self._json({"error": "agent and content required"}, 400)
            return
        tasks_dir = _worker_tasks_dir(agent_name)
        if not tasks_dir.exists():
            self._json({"error": f"agent '{agent_name}' not found"}, 404)
            return
        ts = time.strftime("%Y%m%d-%H%M%S")
        ms = int(time.time() * 1000) % 1000
        fname = f"task-{ts}-{ms:03d}.md"
        (tasks_dir / fname).write_text(content, encoding="utf-8")
        # Push flow animation to graph
        _broadcaster.push("task_flow", {"from": None, "to": agent_name, "count": 1})
        self._json({"ok": True, "file": fname})

    def _post_agent_start(self, name: str):
        """POST /api/agent/<name>/start  → launch the scanner process for this agent."""
        from secretary.agents import get_worker
        info = get_worker(name)
        if not info:
            self._json({"error": f"agent '{name}' not found"}, 404)
            return
        atype = info.get("type", "worker")
        try:
            started, err_msg = _start_agent_scanner_safe(name, atype)
            if started:
                _broadcaster.push("agents_changed", {"action": "start", "name": name})
            self._json({"ok": started, "started": started,
                        "error": err_msg if not started else None})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _post_agent_task(self, name: str):
        """POST /api/agent/<name>/task  {content: str} → write task file directly to agent tasks dir."""
        body = self._read_json()
        if body is None:
            return
        content = body.get("content", "").strip()
        if not content:
            self._json({"error": "content required"}, 400)
            return
        tasks_dir = _worker_tasks_dir(name)
        if not tasks_dir.exists():
            self._json({"error": f"agent '{name}' not found"}, 404)
            return
        ts = time.strftime("%Y%m%d-%H%M%S")
        ms = int(time.time() * 1000) % 1000
        fname = f"task-{ts}-{ms:03d}.md"
        (tasks_dir / fname).write_text(content, encoding="utf-8")
        _broadcaster.push("agents_changed", {"action": "task", "name": name, "file": fname})
        _broadcaster.push("task_flow", {"from": None, "to": name, "count": 1})
        self._json({"ok": True, "file": fname})

    def _post_agent_upload(self, name: str):
        """POST /api/agent/<name>/upload  multipart/form-data → copy uploaded file(s) to tasks dir."""
        import cgi
        tasks_dir = _worker_tasks_dir(name)
        if not tasks_dir.exists():
            self._json({"error": f"agent '{name}' not found"}, 404)
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._json({"error": "Content-Type must be multipart/form-data"}, 400)
            return
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST",
                     "CONTENT_TYPE": ctype,
                     "CONTENT_LENGTH": self.headers.get("Content-Length", "0")},
        )
        files_saved: list[str] = []
        items = form["files"] if "files" in form else []
        if not isinstance(items, list):
            items = [items]
        for item in items:
            if not item.filename:
                continue
            raw = item.file.read()
            fname = Path(item.filename).name
            dest = tasks_dir / fname
            dest.write_bytes(raw)
            files_saved.append(fname)
        if not files_saved:
            self._json({"error": "no files uploaded"}, 400)
            return
        for f in files_saved:
            _broadcaster.push("task_flow", {"from": None, "to": name, "count": 1})
        _broadcaster.push("agents_changed", {"action": "upload", "name": name, "files": files_saved})
        self._json({"ok": True, "files": files_saved})

    def _post_agent_chat(self, name: str):
        """POST /api/agent/<name>/chat  {message: str} → send chat request to scanner process."""
        import json
        import time
        from secretary.agents import get_worker

        info = get_worker(name)
        if not info:
            self._json({"error": f"agent '{name}' not found"}, 404)
            return
        body = self._read_json()
        if body is None:
            return
        message = body.get("message", "").strip()
        if not message:
            self._json({"error": "message required"}, 400)
            return

        # 检查 scanner 是否在运行
        if not info.get("pid"):
            self._json({"error": f"agent '{name}' scanner is not running. Please start it first."}, 400)
            return

        # 写入 chat 请求文件
        agent_dir = cfg.AGENTS_DIR / name
        chat_request_file = agent_dir / "chat_request.json"
        chat_response_file = agent_dir / "chat_response.json"
        
        # 如果已有未处理的请求，先删除旧响应
        if chat_response_file.exists():
            try:
                chat_response_file.unlink()
            except:
                pass
        
        # 写入请求
        try:
            chat_request_file.write_text(
                json.dumps({"message": message}, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            _slog.error(f"Failed to write chat request for {name}: {e}")
            self._json({"error": f"Failed to write chat request: {e}"}, 500)
            return

        # 轮询等待响应（最多等待 60 秒）
        max_wait = 60
        poll_interval = 0.5
        waited = 0.0
        
        while waited < max_wait:
            if chat_response_file.exists():
                try:
                    response_data = json.loads(chat_response_file.read_text(encoding="utf-8"))
                    # 删除响应文件
                    chat_response_file.unlink()
                    
                    # 推送 dialog 更新事件
                    dialog_file = agent_dir / "dialog" / "dialog.md"
                    if dialog_file.exists():
                        _broadcaster.push(f"file_dialog_{name}", {"size": dialog_file.stat().st_size})
                    
                    self._json(response_data)
                    return
                except Exception as e:
                    _slog.error(f"Failed to read chat response for {name}: {e}")
                    self._json({"error": f"Failed to read response: {e}"}, 500)
                    return
            
            time.sleep(poll_interval)
            waited += poll_interval
        
        # 超时
        _slog.error(f"Chat request timeout for {name} after {max_wait}s")
        # 尝试删除请求文件（可能 scanner 卡住了）
        try:
            if chat_request_file.exists():
                chat_request_file.unlink()
        except:
            pass
        self._json({
            "ok": False,
            "error": f"Chat request timeout after {max_wait}s. Scanner may be busy or stuck.",
            "output": "",
        }, 504)

    # ── SSE ────────────────────────────────────────────────────

    def _sse(self):
        self._response_sent = True   # SSE headers about to be sent; block error fallback
        self._last_status = 200
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = _broadcaster.subscribe()
        try:
            # Send initial heartbeat
            self.wfile.write(b": heartbeat\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=15)
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            _broadcaster.unsubscribe(q)

    # ── HTML ───────────────────────────────────────────────────

    def _serve_html(self):
        html_file = Path(__file__).parent / "static" / "index.html"
        if not html_file.exists():
            self._json({"error": "index.html not found"}, 500)
            return
        content = html_file.read_bytes()
        self._response_sent = True   # prevent error handler sending a 2nd response
        self._last_status = 200
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ── helpers ────────────────────────────────────────────────

    def _json(self, data, status=200):
        # Only ever send one response per request; ignore extra calls
        # (e.g. when both a handler and an outer except block both call _json).
        if getattr(self, "_response_sent", False):
            return
        self._response_sent = True
        self._last_status = status
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Client disconnected before we could reply — that's fine.
            pass

    def _404(self):
        self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass   # request logging handled by _slog in do_GET/POST/DELETE


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def run_dashboard(port: int = 12345, open_browser: bool = True):
    """Start the Kai web dashboard and block until Ctrl+C."""
    import webbrowser
    import io as _io

    # Use a UTF-8 stdout so emoji in startup messages don't crash on Windows (GBK).
    _out = _io.TextIOWrapper(_io.FileIO(sys.stdout.fileno(), closefd=False),
                             encoding="utf-8", line_buffering=True)

    def _print(*args, **kw):
        try:
            print(*args, **kw)
        except UnicodeEncodeError:
            try:
                _out.write(" ".join(str(a) for a in args) + "\n")
                _out.flush()
            except Exception:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.timeout = 0.5           # handle_request() returns after 0.5 s if no request
    server.daemon_threads = True   # handler threads won't block shutdown
    url = f"http://127.0.0.1:{port}"
    _print(f"\n\033[1;36mKai Dashboard\033[0m  ->  \033[4m{url}\033[0m")
    _print(f"\033[2m workspace: {cfg.BASE_DIR}  |  Ctrl+C to quit\033[0m\n")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()

    # Install a SIGINT handler so that child-process console events
    # on Windows don't kill the dashboard.  signal.signal() only
    # works from the main thread, so fall back gracefully.
    import signal
    _stop_flag = threading.Event()
    _original_sigint = None
    _use_signal = (threading.current_thread() is threading.main_thread())

    if _use_signal:
        _original_sigint = signal.getsignal(signal.SIGINT)

        def _sigint_handler(sig, frame):
            _stop_flag.set()

        signal.signal(signal.SIGINT, _sigint_handler)

    _print("\033[2m  (press Ctrl+C to stop)\033[0m")

    try:
        while not _stop_flag.is_set():
            try:
                server.handle_request()          # returns after server.timeout (0.5 s)
            except KeyboardInterrupt:
                break                            # real Ctrl+C when no signal handler
            except Exception as exc:
                tb_str = traceback.format_exc()
                _slog.error(f"serve loop — {exc}", exc)
                _print(f"\033[31m[ERROR] {exc}\033[0m")
                # Push the error to every connected browser via SSE
                try:
                    _broadcaster.push("server_error", {
                        "msg": str(exc),
                        "tb": tb_str,
                    })
                except Exception:
                    pass
                # Don't exit — keep serving
    except KeyboardInterrupt:
        pass
    finally:
        if _use_signal and _original_sigint is not None:
            signal.signal(signal.SIGINT, _original_sigint)
        server.server_close()
        _print("\n\033[0mDashboard closed.")


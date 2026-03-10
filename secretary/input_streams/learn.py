"""
Input stream for Tsinghua Web Learning (learn.tsinghua.edu.cn).

This module focuses on "read + extract" workflow:
- list courses in one semester
- list courseware files and download them
- list homework items and (optionally) download homework attachments parsed from pages
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://learn.tsinghua.edu.cn"

# Source endpoints used by Learn Helper / thu-learn-lib for student homework list.
HOMEWORK_LIST_ENDPOINTS = [
    ("/b/wlxt/kczy/zy/student/zyListWj", "new"),
    ("/b/wlxt/kczy/zy/student/zyListYjwg", "submitted"),
    ("/b/wlxt/kczy/zy/student/zyListYpg", "graded"),
]


class LearnStreamError(RuntimeError):
    """Raised when Learn input stream cannot continue safely."""


@dataclass
class _LearnAuth:
    cookie: str
    csrf_token: str
    base_url: str
    timeout: float
    user_agent: str


def _safe_name(name: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_unique_file(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


class _LearnClient:
    def __init__(self, auth: _LearnAuth):
        self.auth = auth

    def _build_url(self, path_or_url: str, add_csrf: bool = True) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            raw = path_or_url
        else:
            raw = urllib.parse.urljoin(self.auth.base_url.rstrip("/") + "/", path_or_url.lstrip("/"))
        if not add_csrf:
            return raw
        parsed = urllib.parse.urlsplit(raw)
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "_csrf" for key, _ in params):
            params.append(("_csrf", self.auth.csrf_token))
        query = urllib.parse.urlencode(params, doseq=True)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

    def _request(
        self,
        path_or_url: str,
        method: str = "GET",
        form_data: dict[str, str] | None = None,
        add_csrf: bool = True,
    ) -> tuple[bytes, dict[str, str]]:
        url = self._build_url(path_or_url, add_csrf=add_csrf)
        data = None
        headers = {
            "Cookie": self.auth.cookie,
            "User-Agent": self.auth.user_agent,
            "Accept": "application/json, text/plain, */*",
        }
        if form_data is not None:
            data = urllib.parse.urlencode(form_data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.auth.timeout) as resp:
                payload = resp.read()
                response_headers = {k.lower(): v for k, v in resp.headers.items()}
                return payload, response_headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise LearnStreamError(f"HTTP {exc.code} for {url}: {body}") from exc
        except urllib.error.URLError as exc:
            raise LearnStreamError(f"Network error for {url}: {exc}") from exc

    def _json(
        self,
        path_or_url: str,
        method: str = "GET",
        form_data: dict[str, str] | None = None,
        add_csrf: bool = True,
    ) -> Any:
        body, _ = self._request(path_or_url, method=method, form_data=form_data, add_csrf=add_csrf)
        text = body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LearnStreamError(f"Invalid JSON response from {path_or_url}") from exc

    def get_current_semester(self) -> str:
        data = self._json("/b/kc/zhjw_v_code_xnxq/getCurrentAndNextSemester")
        if isinstance(data, dict) and data.get("message") == "success":
            result = data.get("result") or {}
            semester = result.get("id")
            if semester:
                return str(semester)
        raise LearnStreamError("Failed to fetch current semester id")

    def get_courses(self, semester_id: str, lang: str = "zh") -> list[dict[str, Any]]:
        endpoint = f"/b/wlxt/kc/v_wlkc_xs_xkb_kcb_extend/student/loadCourseBySemesterId/{semester_id}/{lang}"
        data = self._json(endpoint)
        if not (isinstance(data, dict) and data.get("message") == "success"):
            raise LearnStreamError("Failed to fetch course list")
        courses = data.get("resultList")
        if not isinstance(courses, list):
            return []
        return [c for c in courses if isinstance(c, dict)]

    def get_courseware_list(self, course_id: str, size: int = 200) -> list[dict[str, Any]]:
        endpoint = f"/b/wlxt/kj/wlkc_kjxxb/student/kjxxbByWlkcidAndSizeForStudent?wlkcid={course_id}&size={size}"
        data = self._json(endpoint)
        if not (isinstance(data, dict) and data.get("result") == "success"):
            return []
        rows = data.get("object")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []

    def get_homework_list(self, course_id: str) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        for endpoint, status in HOMEWORK_LIST_ENDPOINTS:
            data = self._json(
                endpoint,
                method="POST",
                form_data={"aoData": json.dumps([{"name": "wlkcid", "value": course_id}], ensure_ascii=False)},
            )
            if not (isinstance(data, dict) and data.get("result") == "success"):
                continue
            rows = (data.get("object") or {}).get("aaData") or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                xszyid = str(row.get("xszyid") or "").strip()
                if not xszyid:
                    continue
                row = dict(row)
                row["_status"] = status
                collected.setdefault(xszyid, row)
        return list(collected.values())

    def get_homework_page(self, course_id: str, student_homework_id: str) -> str:
        endpoint = (
            f"/f/wlxt/kczy/zy/student/viewCj?wlkcid={urllib.parse.quote(course_id)}"
            f"&xszyid={urllib.parse.quote(student_homework_id)}"
        )
        body, _ = self._request(endpoint, method="GET")
        return body.decode("utf-8", errors="replace")

    def download(self, path_or_url: str, target_file: Path) -> dict[str, str]:
        body, headers = self._request(path_or_url, method="GET")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(body)
        return headers


def _extract_homework_attachments(page_html: str, base_url: str) -> list[dict[str, str]]:
    """
    Parse homework page and extract candidate attachment links.
    The parser is intentionally permissive because page structure may vary.
    """
    anchor_re = re.compile(r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
    tag_re = re.compile(r"<[^>]+>")
    seen: set[str] = set()
    found: list[dict[str, str]] = []
    for href, label_html in anchor_re.findall(page_html):
        href = html.unescape(href).strip()
        if not href:
            continue
        if "fileId=" not in href and "downloadFile" not in href and "downloadUrl=" not in href:
            continue
        parsed = urllib.parse.urlsplit(href)
        params = urllib.parse.parse_qs(parsed.query)
        actual_href = href
        if "downloadUrl" in params and params["downloadUrl"]:
            actual_href = html.unescape(params["downloadUrl"][0])
        abs_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", actual_href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        file_id = ""
        actual_parsed = urllib.parse.urlsplit(actual_href)
        actual_qs = urllib.parse.parse_qs(actual_parsed.query)
        for key in ("fileId", "wjid"):
            if key in actual_qs and actual_qs[key]:
                file_id = actual_qs[key][0]
                break
        label = tag_re.sub("", html.unescape(label_html)).strip()
        found.append(
            {
                "file_id": file_id,
                "name": _safe_name(label, fallback=f"attachment_{len(found)+1}"),
                "url": abs_url,
            }
        )
    return found


def run_learn_stream(
    *,
    cookie: str,
    csrf_token: str,
    output_dir: str | Path,
    semester_id: str | None = None,
    include_files: bool = True,
    include_homework: bool = True,
    download_homework_attachments: bool = True,
    dry_run: bool = False,
    lang: str = "zh",
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 20.0,
    user_agent: str = "study-learn-stream/0.1",
) -> dict[str, Any]:
    """
    Execute Learn input stream and sync selected content to local folder.
    """
    cookie = cookie.strip()
    csrf_token = csrf_token.strip()
    if not cookie:
        raise LearnStreamError("Missing cookie")
    if not csrf_token:
        raise LearnStreamError("Missing CSRF token")
    if not include_files and not include_homework:
        raise LearnStreamError("Both include_files and include_homework are false")
    if lang not in {"zh", "en"}:
        raise LearnStreamError("lang must be one of: zh, en")

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    client = _LearnClient(
        _LearnAuth(
            cookie=cookie,
            csrf_token=csrf_token,
            base_url=base_url,
            timeout=timeout,
            user_agent=user_agent,
        )
    )

    chosen_semester = semester_id or client.get_current_semester()
    courses = client.get_courses(chosen_semester, lang=lang)

    manifest: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "semester_id": chosen_semester,
        "base_url": base_url,
        "dry_run": dry_run,
        "include_files": include_files,
        "include_homework": include_homework,
        "course_count": len(courses),
        "stats": {
            "courses_processed": 0,
            "courseware_items": 0,
            "courseware_downloaded": 0,
            "homework_items": 0,
            "homework_attachments": 0,
            "homework_attachments_downloaded": 0,
            "errors": 0,
        },
        "courses": [],
    }

    for course in courses:
        course_id = str(course.get("wlkcid") or course.get("id") or "").strip()
        if not course_id:
            manifest["stats"]["errors"] += 1
            continue
        course_name = html.unescape(
            str(course.get("kcm") or course.get("zywkcm") or course.get("courseName") or f"course_{course_id}")
        )
        course_dir = out / _safe_name(f"{course_name}_{course_id}")
        course_dir.mkdir(parents=True, exist_ok=True)

        course_entry: dict[str, Any] = {
            "course_id": course_id,
            "course_name": course_name,
            "course_dir": str(course_dir),
            "courseware_count": 0,
            "homework_count": 0,
            "homework_attachment_count": 0,
            "errors": [],
        }
        _write_json(course_dir / "course.json", {"course": course, "course_id": course_id, "course_name": course_name})

        if include_files:
            files = client.get_courseware_list(course_id)
            course_entry["courseware_count"] = len(files)
            manifest["stats"]["courseware_items"] += len(files)
            file_index: list[dict[str, Any]] = []
            for item in files:
                file_id = str(item.get("wjid") or "").strip()
                if not file_id:
                    continue
                title = html.unescape(str(item.get("bt") or item.get("wjmc") or item.get("name") or f"file_{file_id}"))
                ext = str(item.get("wjlx") or "").strip().lstrip(".")
                if ext and "." not in title:
                    title = f"{title}.{ext}"
                file_name = _safe_name(title, fallback=f"courseware_{file_id}")
                rel_path = Path("courseware") / file_name
                target_file = _ensure_unique_file(course_dir / rel_path)
                download_url = f"/b/wlxt/kj/wlkc_kjxxb/student/downloadFile?sfgk=0&wjid={urllib.parse.quote(file_id)}"
                record = {
                    "file_id": file_id,
                    "title": title,
                    "download_url": client._build_url(download_url),
                    "target": str(target_file),
                    "raw": item,
                }
                if not dry_run:
                    try:
                        client.download(download_url, target_file)
                        manifest["stats"]["courseware_downloaded"] += 1
                    except Exception as exc:  # noqa: BLE001
                        manifest["stats"]["errors"] += 1
                        course_entry["errors"].append(f"courseware {file_id}: {exc}")
                file_index.append(record)
            _write_json(course_dir / "courseware" / "index.json", {"items": file_index})

        if include_homework:
            homeworks = client.get_homework_list(course_id)
            course_entry["homework_count"] = len(homeworks)
            manifest["stats"]["homework_items"] += len(homeworks)
            homework_index: list[dict[str, Any]] = []

            for homework in homeworks:
                xszyid = str(homework.get("xszyid") or "").strip()
                base_id = str(homework.get("zyid") or "").strip()
                title = html.unescape(str(homework.get("bt") or f"homework_{xszyid or base_id}"))
                hw_folder = _safe_name(f"{xszyid or base_id}_{title}", fallback=f"homework_{xszyid or base_id}")
                hw_dir = course_dir / "homework" / hw_folder
                hw_dir.mkdir(parents=True, exist_ok=True)
                record: dict[str, Any] = {
                    "xszyid": xszyid,
                    "zyid": base_id,
                    "title": title,
                    "status": homework.get("_status"),
                    "deadline": homework.get("jzsj"),
                    "target_dir": str(hw_dir),
                    "raw": homework,
                    "attachments": [],
                }

                if download_homework_attachments and xszyid:
                    try:
                        page = client.get_homework_page(course_id, xszyid)
                        attachments = _extract_homework_attachments(page, base_url=base_url)
                        record["attachments"] = attachments
                        course_entry["homework_attachment_count"] += len(attachments)
                        manifest["stats"]["homework_attachments"] += len(attachments)
                        for att in attachments:
                            target_name = _safe_name(att["name"], fallback=f"attachment_{att.get('file_id') or 'unknown'}")
                            if "." not in target_name:
                                target_name = f"{target_name}.bin"
                            target_file = _ensure_unique_file(hw_dir / target_name)
                            if not dry_run:
                                try:
                                    client.download(att["url"], target_file)
                                    manifest["stats"]["homework_attachments_downloaded"] += 1
                                except Exception as exc:  # noqa: BLE001
                                    manifest["stats"]["errors"] += 1
                                    course_entry["errors"].append(
                                        f"homework attachment {att.get('file_id') or att['url']}: {exc}"
                                    )
                    except Exception as exc:  # noqa: BLE001
                        manifest["stats"]["errors"] += 1
                        course_entry["errors"].append(f"homework {xszyid} page parse: {exc}")

                homework_index.append(record)

            _write_json(course_dir / "homework" / "index.json", {"items": homework_index})

        manifest["stats"]["courses_processed"] += 1
        manifest["courses"].append(course_entry)

    _write_json(out / "learn_stream_manifest.json", manifest)
    return manifest

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地考试后端管理系统
====================

基于仓库中的本地考试机前端镜像，提供不依赖数据库、以明文 JSON 文件存储数据的
考试后端：

- 考生（准考证号）管理
- 试题管理
- 考试管理（统一考试 / 单独启动 / 自动开考开关）
- 数据管理与 CLI

用法示例
--------
启动服务::

    python exam_backend.py server --port 8000

管理 CLI::

    python exam_backend.py candidate add --ticket 20260001 --name 张三
    python exam_backend.py question add --type sc --stem '...' --option 'A=...' --answer A
    python exam_backend.py exam create --name '期中考试' --duration 20 --question q001,q002
    python exam_backend.py exam start --id exam_xxx
    python exam_backend.py exam auto --id exam_xxx --on
    python exam_backend.py session start --ticket 20260001 --exam exam_xxx

打开管理后台: http://127.0.0.1:8000/admin
考试机前端: http://127.0.0.1:8000/
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"
MIRROR_DIR = ROOT / "mirror"
DEFAULT_PORT = 8000

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_ts() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def parse_json_body(data: bytes):
    if not data:
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}


def json_dumps(obj, pretty=True) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None).encode("utf-8")


def clean_str(value, default: str = "") -> str:
    """把 None 安全转为字符串；空字符串可替换为 default。"""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def to_bool(value, default: bool = False) -> bool:
    """宽容地把常见字符串/布尔值解析为 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on", "开启")
    return bool(value)


# ---------------------------------------------------------------------------
# 明文数据仓储
# ---------------------------------------------------------------------------

class DataStore:
    """使用 JSON 文件保存所有业务数据，不引入数据库。"""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_path = self.data_dir / "candidates.json"
        self.questions_path = self.data_dir / "questions.json"
        self.exams_path = self.data_dir / "exams.json"
        self.sessions_path = self.data_dir / "sessions.json"
        self.events_path = self.data_dir / "events.json"
        self.settings_path = self.data_dir / "settings.json"
        self.lock = threading.RLock()
        self._ensure_default_files()
        self.candidates = self._load_list(self.candidates_path)
        self.questions = self._load_list(self.questions_path)
        self.exams = self._load_list(self.exams_path)
        self.sessions = self._load_dict(self.sessions_path)
        self.events = self._load_list(self.events_path)
        self.settings = self._load_dict(self.settings_path)
        if not self.settings.get("mode"):
            self.settings["mode"] = "simulation"

    def _ensure_default_files(self) -> None:
        paths = [self.candidates_path, self.questions_path, self.exams_path,
                 self.sessions_path, self.events_path]
        for path in paths:
            if not path.exists():
                path.write_text("[]" if path.suffix != ".json" or path.name != "sessions.json" else "{}",
                                encoding="utf-8")
        if not self.settings_path.exists():
            self.settings_path.write_text('{"mode": "simulation"}', encoding="utf-8")

    def _load_list(self, path: Path) -> list:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _load_dict(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_json(self, path: Path, obj) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def save_candidates(self) -> None:
        with self.lock:
            self._write_json(self.candidates_path, self.candidates)

    def save_questions(self) -> None:
        with self.lock:
            self._write_json(self.questions_path, self.questions)

    def save_exams(self) -> None:
        with self.lock:
            self._write_json(self.exams_path, self.exams)

    def save_sessions(self) -> None:
        with self.lock:
            self._write_json(self.sessions_path, self.sessions)

    def save_events(self) -> None:
        with self.lock:
            self._write_json(self.events_path, self.events)

    def save_settings(self) -> None:
        with self.lock:
            self._write_json(self.settings_path, self.settings)

    def get_mode(self) -> str:
        return self.settings.get("mode", "simulation")

    def set_mode(self, mode: str) -> dict:
        if mode not in ("simulation", "standard"):
            raise ValueError("模式只能是 simulation 或 standard")
        if mode == "standard":
            active = [e for e in self.exams if e.get("status") in ("ready", "running")]
            if len(active) > 1:
                raise ValueError("无法切换到标准模式：当前有多场进行中的考试")
        self.settings["mode"] = mode
        self.save_settings()
        return self.settings

    # ---- candidates ----
    def find_candidate(self, ticket_no: str) -> dict | None:
        for c in self.candidates:
            if c.get("ticket_no") == ticket_no:
                return c
        return None

    def add_candidate(self, data: dict) -> dict:
        ticket = str(data.get("ticket_no", "")).strip()
        if not ticket:
            raise ValueError("准考证号不能为空")
        if self.find_candidate(ticket):
            raise ValueError(f"考生已存在: {ticket}")
        def _s(value):
            if value is None:
                return ""
            return str(value).strip()

        candidate = {
            "ticket_no": ticket,
            "name": _s(data.get("name", "")),
            "gender": _s(data.get("gender", "")),
            "id_card": _s(data.get("id_card", "")),
            "exam_id": _s(data.get("exam_id")) or None,
            "seat_no": _s(data.get("seat_no", "")),
            "status": _s(data.get("status")) or "normal",
            "remark": _s(data.get("remark", "")),
            "created_at": now_iso(),
        }
        with self.lock:
            self.candidates.append(candidate)
            self.save_candidates()
        return candidate

    def update_candidate(self, ticket_no: str, data: dict) -> dict | None:
        with self.lock:
            for c in self.candidates:
                if c.get("ticket_no") == ticket_no:
                    for key in ("name", "gender", "id_card", "exam_id", "seat_no",
                                "status", "remark"):
                        if key in data:
                            if data[key] is None:
                                c[key] = None
                            elif key == "exam_id":
                                c[key] = str(data[key]).strip() or None
                            else:
                                c[key] = str(data[key]).strip()
                    self.save_candidates()
                    return c
        return None

    def delete_candidate(self, ticket_no: str) -> bool:
        with self.lock:
            old_len = len(self.candidates)
            self.candidates = [c for c in self.candidates if c.get("ticket_no") != ticket_no]
            if len(self.candidates) != old_len:
                self.save_candidates()
                return True
        return False

    # ---- questions ----
    def find_question(self, qid: str) -> dict | None:
        for q in self.questions:
            if q.get("id") == qid:
                return q
        return None

    def add_question(self, data: dict) -> dict:
        qid = str(data.get("id", "")).strip() or new_id("q")
        if self.find_question(qid):
            raise ValueError(f"试题已存在: {qid}")
        qtype = str(data.get("type", "sc")).strip() or "sc"
        if qtype not in ("sc", "mc", "judge", "fill", "sa"):
            # 保留前端可识别的类型，未知类型也允许保存。
            pass
        question = {
            "id": qid,
            "type": qtype,
            "stem": clean_str(data.get("stem")),
            "options": data.get("options", []),
            "answer": data.get("answer", []),
            "score": float(data.get("score", 1) or 1),
            "subject": clean_str(data.get("subject")),
            "section": clean_str(data.get("section")),
            "group": clean_str(data.get("group")),
            "explanation": clean_str(data.get("explanation")),
            "enabled": to_bool(data.get("enabled"), True),
            "created_at": now_iso(),
        }
        # 统一 options 形式: [{"id":"A","description":"..."}]
        if isinstance(question["options"], list):
            normalized = []
            for opt in question["options"]:
                if isinstance(opt, dict):
                    normalized.append({
                        "id": str(opt.get("id", "")),
                        "description": str(opt.get("description", "")),
                    })
                else:
                    normalized.append({"id": str(len(normalized) + 1), "description": str(opt)})
            question["options"] = normalized
        # answer 统一为 list
        if isinstance(question["answer"], str):
            question["answer"] = [x.strip() for x in question["answer"].replace("，", ",").split(",") if x.strip()]
        elif not isinstance(question["answer"], list):
            question["answer"] = []
        with self.lock:
            self.questions.append(question)
            self.save_questions()
        return question

    def update_question(self, qid: str, data: dict) -> dict | None:
        with self.lock:
            for q in self.questions:
                if q.get("id") == qid:
                    for key in ("type", "stem", "options", "answer", "score",
                                "subject", "section", "group", "explanation", "enabled"):
                        if key in data:
                            if key == "enabled":
                                q[key] = to_bool(data[key])
                            else:
                                q[key] = data[key]
                    # 答案统一为 list
                    if isinstance(q.get("answer"), str):
                        q["answer"] = [x.strip() for x in q["answer"].replace("，", ",").split(",") if x.strip()]
                    elif not isinstance(q.get("answer"), list):
                        q["answer"] = []
                    # 分值转数值
                    try:
                        q["score"] = float(q.get("score", 1) or 1)
                    except Exception:
                        q["score"] = 1.0
                    self.save_questions()
                    return q
        return None

    def delete_question(self, qid: str) -> bool:
        with self.lock:
            old_len = len(self.questions)
            self.questions = [q for q in self.questions if q.get("id") != qid]
            if len(self.questions) != old_len:
                self.save_questions()
                return True
        return False

    # ---- exams ----
    def find_exam(self, exam_id: str) -> dict | None:
        for e in self.exams:
            if e.get("id") == exam_id:
                return e
        return None

    def add_exam(self, data: dict) -> dict:
        exam_id = str(data.get("id", "")).strip() or new_id("exam")
        if self.find_exam(exam_id):
            raise ValueError(f"考试已存在: {exam_id}")
        exam = {
            "id": exam_id,
            "name": clean_str(data.get("name"), "未命名考试"),
            "title": clean_str(data.get("title"), clean_str(data.get("name"), "未命名考试")),
            "status": clean_str(data.get("status"), "draft"),
            "stage": clean_str(data.get("stage"), "created"),
            "mode": clean_str(data.get("mode"), "unified"),
            "auto_start": to_bool(data.get("auto_start"), False),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "duration_minutes": int(data.get("duration_minutes", 20) or 20),
            "notice": clean_str(data.get("notice")),
            "sections": data.get("sections", []),
            "subjects": data.get("subjects", []),
            "candidate_tickets": data.get("candidate_tickets", []),
            "created_at": now_iso(),
        }
        with self.lock:
            self.exams.append(exam)
            self.save_exams()
        return exam

    def update_exam(self, exam_id: str, data: dict) -> dict | None:
        with self.lock:
            for e in self.exams:
                if e.get("id") == exam_id:
                    for key in ("name", "title", "status", "stage", "mode", "auto_start",
                                "start_time", "end_time", "duration_minutes",
                                "notice", "sections", "subjects", "candidate_tickets",
                                "phase", "current_subject_index", "phase_ends_at",
                                "guide_subject_index", "guide_ends_at"):
                        if key in data:
                            if key == "auto_start":
                                e[key] = to_bool(data[key])
                            elif key == "duration_minutes":
                                try:
                                    e[key] = int(data[key])
                                except Exception:
                                    e[key] = int(data.get(key, 20) or 20)
                            else:
                                e[key] = data[key]
                    self.save_exams()
                    return e
        return None

    def delete_exam(self, exam_id: str) -> bool:
        with self.lock:
            old_len = len(self.exams)
            self.exams = [e for e in self.exams if e.get("id") != exam_id]
            if len(self.exams) != old_len:
                self.save_exams()
                return True
        return False

    # ---- sessions ----
    def _session_key(self, ticket_no: str, exam_id: str) -> str:
        return f"{ticket_no}::{exam_id}"

    def get_session(self, ticket_no: str, exam_id: str) -> dict | None:
        return self.sessions.get(self._session_key(ticket_no, exam_id))

    def list_sessions(self) -> list:
        return list(self.sessions.values())

    def start_session(self, ticket_no: str, exam_id: str) -> dict:
        key = self._session_key(ticket_no, exam_id)
        with self.lock:
            if key in self.sessions:
                return self.sessions[key]
            candidate = self.find_candidate(ticket_no)
            if not candidate:
                raise ValueError(f"考生不存在: {ticket_no}")
            exam = self.find_exam(exam_id)
            if not exam:
                raise ValueError(f"考试不存在: {exam_id}")
            sess = {
                "key": key,
                "ticket_no": ticket_no,
                "exam_id": exam_id,
                "candidate_name": candidate.get("name", ""),
                "started_at": now_ts(),
                "ended_at": None,
                "current_section": 0,
                "current_group": 0,
                "answers": {},
                "states": {},
                "events": [],
                "score": None,
                "submitted": False,
            }
            self.sessions[key] = sess
            self.save_sessions()
            return sess

    def end_session(self, ticket_no: str, exam_id: str) -> dict | None:
        with self.lock:
            sess = self.get_session(ticket_no, exam_id)
            if not sess:
                return None
            sess["ended_at"] = now_ts()
            sess["submitted"] = True
            self.save_sessions()
            return sess

    def add_event(self, ticket_no: str, exam_id: str, event: dict) -> dict:
        sess = self.get_session(ticket_no, exam_id)
        with self.lock:
            if sess:
                sess.setdefault("events", []).append({
                    "time": now_ts(),
                    "type": str(event.get("type", "")),
                    "data": event,
                })
                self.save_sessions()
            record = {
                "ticket_no": ticket_no,
                "exam_id": exam_id,
                "time": now_ts(),
                "type": str(event.get("type", "")),
                "data": event,
            }
            self.events.append(record)
            self.save_events()
            return record

    # ---- helpers ----
    def active_exam(self) -> dict | None:
        """返回当前应生效的进行中考试（简单取第一个 running）。"""
        now = now_ts()
        candidates = []
        for e in self.exams:
            if e.get("status") == "running":
                st = e.get("start_time")
                et = e.get("end_time")
                try:
                    st_i = int(st) if st else None
                except Exception:
                    st_i = None
                try:
                    et_i = int(et) if et else None
                except Exception:
                    et_i = None
                if st_i and now < st_i:
                    continue
                if et_i and now > et_i:
                    continue
                candidates.append(e)
        # 如果有多个，取最近启动的
        if candidates:
            return sorted(candidates, key=lambda x: x.get("start_time") or 0)[-1]
        return None

    def available_exam(self) -> dict | None:
        """返回当前考试机可用的考试：优先进行中的考试；否则返回开启了自动开考的考试。"""
        exam = self.active_exam()
        if exam:
            return exam
        for e in self.exams:
            if e.get("auto_start") and e.get("status") != "ended":
                return e
        return None

    # ---- 考试生命周期 ----
    def get_exam_subjects(self, exam: dict) -> list:
        """返回规范化的科目列表。"""
        subjects = exam.get("subjects")
        if subjects:
            return subjects
        # 兼容旧 sections 结构
        result = []
        for si, sec in enumerate(self.get_exam_sections(exam)):
            qids = []
            for grp in sec.get("groups", []):
                qids.extend(grp.get("question_ids", []))
            result.append({
                "id": f"s{si+1}",
                "name": sec.get("name", f"科目{si+1}"),
                "duration": int((sec.get("timer") or {}).get("time_limit", int(exam.get("duration_minutes", 20) or 20) * 60)),
                "guide_duration": 600,
                "question_ids": qids,
                "allow_early_submit": False,
            })
        return result

    def subject_duration(self, exam: dict, index: int) -> int:
        subjects = self.get_exam_subjects(exam)
        if 0 <= index < len(subjects):
            sub = subjects[index]
            if "duration_minutes" in sub:
                return int(sub["duration_minutes"]) * 60
            val = sub.get("duration", 600)
            return int(val)
        return int(exam.get("duration_minutes", 20) or 20) * 60

    def subject_guide_duration(self, exam: dict, index: int) -> int:
        subjects = self.get_exam_subjects(exam)
        if 0 <= index < len(subjects):
            sub = subjects[index]
            if "guide_duration_minutes" in sub:
                return int(sub["guide_duration_minutes"]) * 60
            val = sub.get("guide_duration", 600)
            return int(val)
        return 600

    def prepare_exam(self, exam_id: str) -> dict | None:
        with self.lock:
            exam = self.find_exam(exam_id)
            if not exam:
                return None
            if self.get_mode() == "standard":
                conflict = [e for e in self.exams if e["id"] != exam_id and e.get("status") in ("ready", "running")]
                if conflict:
                    raise ValueError("标准模式下全局只能同时进行一场考试")
            exam["stage"] = "prepared"
            exam["status"] = "ready"
            exam["phase"] = "prepared"
            self.save_exams()
            return exam

    def start_exam(self, exam_id: str) -> dict | None:
        with self.lock:
            exam = self.find_exam(exam_id)
            if not exam:
                return None
            if self.get_mode() == "standard":
                conflict = [e for e in self.exams if e["id"] != exam_id and e.get("status") in ("ready", "running")]
                if conflict:
                    raise ValueError("标准模式下全局只能同时进行一场考试")
            subjects = self.get_exam_subjects(exam)
            if not subjects:
                raise ValueError("考试没有科目，无法开始")
            exam["stage"] = "running"
            exam["status"] = "running"
            exam["start_time"] = now_ts()
            exam["phase"] = "answering"
            exam["current_subject_index"] = 0
            exam["phase_ends_at"] = now_ts() + self.subject_duration(exam, 0)
            exam["guide_subject_index"] = None
            exam["guide_ends_at"] = None
            self.save_exams()
            return exam

    def end_exam(self, exam_id: str) -> dict | None:
        with self.lock:
            exam = self.find_exam(exam_id)
            if not exam:
                return None
            exam["stage"] = "ended"
            exam["status"] = "ended"
            exam["phase"] = "ended"
            exam["end_time"] = now_ts()
            self.save_exams()
            return exam

    def advance_exam(self, exam: dict) -> dict:
        """根据后端时间推进考试阶段。所有倒计时以后端系统时间为准。"""
        with self.lock:
            if exam.get("status") != "running":
                return exam
            now = now_ts()
            phase = exam.get("phase")
            subjects = self.get_exam_subjects(exam)
            idx = int(exam.get("current_subject_index", 0) or 0)
            if phase == "answering":
                end = exam.get("phase_ends_at") or 0
                if now >= end:
                    # 当前科目自动收卷，进入下一科引导（或结束）
                    if idx + 1 < len(subjects):
                        exam["phase"] = "guide"
                        exam["guide_subject_index"] = idx + 1
                        exam["guide_ends_at"] = now + self.subject_guide_duration(exam, idx + 1)
                        exam["phase_ends_at"] = None
                        self.add_event("", exam["id"], {
                            "type": "next_guide", "subject_index": idx + 1,
                            "subject": subjects[idx + 1].get("name", ""),
                            "guide_ends_at": exam["guide_ends_at"],
                        })
                    else:
                        exam["phase"] = "ended"
                        exam["status"] = "ended"
                        exam["end_time"] = now
                        self.add_event("", exam["id"], {"type": "exam_ended"})
                    self.save_exams()
            elif phase == "guide":
                end = exam.get("guide_ends_at") or 0
                if now >= end:
                    nxt = int(exam.get("guide_subject_index", idx + 1) or idx + 1)
                    exam["current_subject_index"] = nxt
                    exam["phase"] = "answering"
                    exam["phase_ends_at"] = now + self.subject_duration(exam, nxt)
                    exam["guide_ends_at"] = None
                    exam["guide_subject_index"] = None
                    self.add_event("", exam["id"], {
                        "type": "next_answer_start", "subject_index": nxt,
                        "subject": subjects[nxt].get("name", ""),
                        "phase_ends_at": exam["phase_ends_at"],
                    })
                    self.save_exams()
        return exam

    # ---- per-exam organization ----
    def find_exam_for_ticket(self, ticket_no: str) -> dict | None:
        """模拟模式遍历所有考试，标准模式只查当前进行中的考试。"""
        mode = self.get_mode()
        if mode == "standard":
            for exam in self.exams:
                if exam.get("status") in ("ready", "running") and exam.get("status") != "ended":
                    if ticket_no in (exam.get("candidate_tickets") or []) or any(
                            c.get("ticket_no") == ticket_no and c.get("exam_id") == exam["id"]
                            for c in self.candidates):
                        return exam
            return None
        # 模拟模式：按顺序找到第一个包含该考生的考试（自由登录/自由考试）
        for e in self.exams:
            if e.get("status") == "ended":
                continue
            if ticket_no in (e.get("candidate_tickets") or []) or any(
                    c.get("ticket_no") == ticket_no and c.get("exam_id") == e["id"] for c in self.candidates):
                return e
        return None

    def get_exam_sections(self, exam: dict) -> list:
        """返回规范化 sections；如果 exam 没有 sections，则按试题自动分组。"""
        if exam.get("sections"):
            return exam["sections"]
        if exam.get("subjects"):
            sections = []
            for si, sub in enumerate(exam["subjects"]):
                qids = sub.get("question_ids", [])
                sections.append({
                    "name": sub.get("name", f"科目{si+1}"),
                    "section_type": "exam",
                    "timer": {
                        "time_limit": self.subject_duration(exam, si),
                        "time_min_limit": self.subject_duration(exam, si),
                        "time_remind": 300,
                        "time_remind_prompt": "还剩余5分钟，请抓紧时间答题！",
                    },
                    "groups": [{
                        "name": sub.get("name", f"科目{si+1}"),
                        "question_ids": qids,
                        "point": sum(float(self.find_question(q).get("score", 1) or 1) for q in qids if self.find_question(q)),
                    }],
                    "point": sum(float(self.find_question(q).get("score", 1) or 1) for q in qids if self.find_question(q)),
                })
            return sections
        # 自动按 subject/section/group 分组
        sections = []
        by_section = {}
        for q in self.questions:
            if not q.get("enabled", True):
                continue
            sec_name = q.get("section") or q.get("subject") or "默认科目"
            by_section.setdefault(sec_name, []).append(q)
        for idx, (sec_name, qs) in enumerate(by_section.items()):
            groups = {}
            for q in qs:
                gname = q.get("group") or "默认组"
                groups.setdefault(gname, []).append(q)
            sec = {
                "name": sec_name,
                "section_type": "exam",
                "timer": {"time_limit": int(exam.get("duration_minutes", 20) or 20) * 60,
                          "time_min_limit": int(exam.get("duration_minutes", 20) or 20) * 60,
                          "time_remind": 300,
                          "time_remind_prompt": "还剩余5分钟，请抓紧时间答题！"},
                "groups": [
                    {"name": gname, "question_ids": [q["id"] for q in gq],
                     "point": sum(float(q.get("score", 1) or 1) for q in gq)}
                    for gname, gq in groups.items()
                ],
                "point": sum(float(q.get("score", 1) or 1) for q in qs),
            }
            sections.append(sec)
        return sections

    def build_form(self, exam: dict) -> dict:
        """按考试机前端 /seat/form/ 格式生成试卷结构。"""
        sections = self.get_exam_sections(exam)
        form_sections = []
        total_point = 0.0
        for si, sec in enumerate(sections):
            sec_groups = []
            sec_point = 0.0
            groups = sec.get("groups", [])
            for gi, grp in enumerate(groups):
                qids = grp.get("question_ids", [])
                items = []
                group_point = 0.0
                for qi, qid in enumerate(qids):
                    q = self.find_question(qid)
                    if not q:
                        continue
                    options = []
                    for opt in q.get("options", []):
                        if isinstance(opt, dict):
                            options.append({"id": str(opt.get("id", "")),
                                            "description": str(opt.get("description", ""))})
                        else:
                            options.append({"id": str(len(options) + 1), "description": str(opt)})
                    # 前端题目 type: sc=单选, mc=多选；其它类型原样透传
                    items.append({
                        "id": q["id"],
                        "index": str(qi + 1),
                        "name": str(grp.get("name", "试题")) + str(qi + 1),
                        "point": float(q.get("score", 1) or 1),
                        "type": q.get("type", "sc"),
                        "content": {
                            "stem": q.get("stem", ""),
                            "options": options,
                            "option_shuffle": True,
                        },
                    })
                    group_point += float(q.get("score", 1) or 1)
                sec_groups.append({
                    "id": f"{si + 1}-{gi + 1}-{exam['id']}",
                    "name": grp.get("name", "默认组"),
                    "description": "（共 {item_length} 题，共 {group_score} 分。）",
                    "item_shuffle": True,
                    "items": items,
                    "point": group_point,
                })
                sec_point += group_point
            total_point += sec_point
            form_sections.append({
                "id": f"{si + 1}{exam['id']}",
                "name": sec.get("name", f"科目{si + 1}"),
                "section_type": sec.get("section_type", "exam"),
                "calculator": sec.get("calculator", False),
                "group_shuffle": sec.get("group_shuffle", False),
                "point": sec_point,
                "timer": sec.get("timer") or {
                    "time_limit": int(exam.get("duration_minutes", 20) or 20) * 60,
                    "time_min_limit": int(exam.get("duration_minutes", 20) or 20) * 60,
                    "time_remind": 300,
                    "time_remind_prompt": "还剩余5分钟，请抓紧时间答题！",
                },
                "instruction": {
                    "pages": [{
                        "title": f"{sec.get('name', '科目')}说明",
                        "content": f"<p>您即将进入“{sec.get('name', '科目')}”科目，请点击开始答题。</p>",
                    }]
                },
                "groups": sec_groups,
            })
        return {
            "form_id": exam["id"],
            "form_type": "exam",
            "id": exam["id"],
            "name": exam.get("title") or exam.get("name", "考试"),
            "point": total_point,
            "section_shuffle": False,
            "timer": {"time_limit": int(exam.get("duration_minutes", 20) or 20) * 60},
            "instruction": {
                "pages": [{
                    "title": "试卷说明",
                    "content": exam.get("notice") or f"<p>本场考试共 {len(form_sections)} 个科目，请认真作答。</p>",
                }]
            },
            "sections": form_sections,
        }


# ---------------------------------------------------------------------------
# 业务逻辑：开考/登录/提交
# ---------------------------------------------------------------------------

class ExamService:
    def __init__(self, store: DataStore):
        self.store = store

    @staticmethod
    def _display_time(value):
        if value is None or value == "":
            return None
        try:
            return datetime.fromtimestamp(int(value)).strftime("%Y/%m/%d %H:%M:%S")
        except Exception:
            return str(value)

    def _resolve_exam(self, ticket: str | None = None) -> dict | None:
        """根据准考证号或当前可用考试，解析本次请求对应的考试。"""
        if ticket:
            # 新版按考试组织匹配
            exam = self.store.find_exam_for_ticket(ticket)
            if exam:
                return exam
            # 已单独启动的考生优先使用其考试记录对应的考试
            for sess in self.store.sessions.values():
                if sess.get("ticket_no") == ticket:
                    exam = self.store.find_exam(sess.get("exam_id"))
                    if exam:
                        return exam
        exam = self.store.available_exam()
        if exam:
            return exam
        if not ticket:
            if self.store.get_mode() == "standard":
                for e in self.store.exams:
                    if e.get("status") == "ready":
                        return e
            # 单独启动场景：只要存在考试记录，就向前端返回该场考试
            for e in self.store.exams:
                if any(s.get("exam_id") == e["id"] for s in self.store.sessions.values()):
                    return e
        return None

    def get_session_payload(self) -> dict:
        """GET /seat/session/ 返回与前端兼容的 session 信息。"""
        exam = self._resolve_exam()
        if not exam:
            return {"status": "error", "message": "当前没有正在进行的考试"}
        # 使用一个稳定的 session id（以考试 id 派生）
        sid = "sess_" + exam["id"]
        try:
            started_ts = int(exam.get("start_time") or 0) or now_ts()
        except Exception:
            started_ts = now_ts()
        return {
            "status": "success",
            "enable_register": False,
            "session": {
                "id": sid,
                "name": f"{exam['id']}-{sid}",
                "project": "",
                "project_id": "",
                "schedule_id": "",
                "title": exam.get("title") or exam.get("name", "考试"),
                "status": "ongoing",
                "start_time": self._display_time(exam.get("start_time")) or now_iso(),
                "end_time": self._display_time(exam.get("end_time")),
                "started_at": started_ts,
                "ended_at": 0,
                "test": 1,
                "skin": {"name": "default"},
                "config": {
                    "answer_history": 0,
                    "anti_view": False,
                    "barrier_free": False,
                    "check_face": False,
                    "enter_limit": 60,
                    "form_limit": 1440,
                    "hand_paper_limit": 0,
                    "idcode_matching": "<4",
                    "is_try": False,
                    "lang": "zh",
                    "late_limit": 30,
                    "leave_screen_limit": 0,
                    "lock_screen": False,
                    "login_with": "permit",
                    "mode": exam.get("mode", "unified"),
                    "password_limit": 15,
                    "seat_according_to_number": True,
                    "show_score": False,
                    "skin": "demo:0589eed14019486c",
                    "skin_md5": "0589eed14019486ccb20cb8626d18f47",
                    "time_delay_limit": 30,
                    "time_delay_limit_single": 30,
                    "watermark": True,
                },
            },
            "center_name": "",
        }

    def login(self, body: dict) -> dict:
        ticket = str(body.get("permit") or body.get("ticket_no") or "").strip()
        if not ticket:
            return {"status": "invalid", "message": "请输入准考证号"}
        candidate = self.store.find_candidate(ticket)
        if not candidate:
            return {"status": "invalid", "message": "准考证号不存在"}
        exam = self._resolve_exam(ticket)
        if not exam:
            return {"status": "invalid", "message": "当前没有进行中的考试"}
        assigned = candidate.get("exam_id") == exam["id"] or ticket in (exam.get("candidate_tickets") or [])
        if exam.get("candidate_tickets") and ticket not in exam["candidate_tickets"]:
            return {"status": "invalid", "message": "该考生未安排本场考试"}
        if candidate.get("exam_id") and candidate["exam_id"] != exam["id"] and not assigned:
            return {"status": "invalid", "message": "该考生未安排本场考试"}
        # 自动开考开关：考试机输入考号即可开考；否则需要后台单独启动该考生
        auto_start = bool(exam.get("auto_start", False))
        mode = exam.get("mode", "unified")
        sess = self.store.get_session(ticket, exam["id"])
        if not sess and not auto_start and mode != "unified":
            return {"status": "invalid", "message": "本场考试未自动开考，请等待监考老师启动"}
        if not sess:
            try:
                sess = self.store.start_session(ticket, exam["id"])
            except Exception as e:
                return {"status": "invalid", "message": str(e)}
        return {
            "entry_id": new_id("entry"),
            "permit": ticket,
            "status": "valid",
            "subject": exam.get("title") or exam.get("name", ""),
            "subject_id": exam["id"],
            "seat": int(candidate.get("seat_no") or 0) or 1,
            "started_at": sess.get("started_at", now_ts()),
            "identity_id": candidate.get("id_card") or "",
            "personal": {
                "full_name": candidate.get("name", ""),
                "gender": candidate.get("gender", ""),
            },
            "response": sess.get("answers", {}),
        }

    def confirm(self, body: dict) -> dict:
        ticket = str(body.get("permit", "")).strip()
        exam = self._resolve_exam(ticket)
        if exam and ticket:
            try:
                self.store.start_session(ticket, exam["id"])
            except ValueError:
                pass
        return {}

    def get_notice(self, body=None) -> dict:
        exam = self._resolve_exam()
        if not exam:
            return {"title": "", "text": ""}
        return {"title": exam.get("title", ""), "text": exam.get("notice") or ""}

    def get_form(self) -> dict:
        exam = self._resolve_exam()
        if not exam:
            return {"status": "error", "message": "no exam", "form": {}}
        return {"status": "success", "form": self.store.build_form(exam)}

    def get_response(self, ticket: str | None, exam_id: str | None = None) -> list:
        if ticket:
            exam = self.store.find_exam(exam_id) if exam_id else self._resolve_exam(ticket)
            if exam:
                sess = self.store.get_session(ticket, exam["id"])
                if sess:
                    return [sess.get("answers", {})]
        return [{}]

    @staticmethod
    def _extract_item_answer(item: dict):
        """从前端 response.items 中提取一题的答案。"""
        ans = item.get("answer")
        if ans is None:
            return None
        if isinstance(ans, dict):
            ans = ans.get("value", ans.get("values", ""))
        if isinstance(ans, list):
            return [str(x) for x in ans if str(x).strip()]
        if ans is None:
            return None
        text = str(ans)
        if not text.strip():
            return None
        # 多选可能以逗号分隔
        return [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]

    def patch_response(self, body: dict) -> dict:
        ticket = str(body.get("permit") or body.get("ticket_no") or "").strip()
        exam = self._resolve_exam(ticket)
        if not ticket or not exam:
            return {}
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {}
        answers = sess.setdefault("answers", {})
        # 前端完整 response 对象格式
        items = body.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if not item_id:
                    continue
                val = self._extract_item_answer(item)
                if val is not None:
                    answers[item_id] = val
        # 兼容简化格式：{"answers": {...}} 或直接 body 本身是答案 dict
        simple = body.get("answers") or body.get("response") or {}
        if isinstance(simple, dict):
            for k, v in simple.items():
                if isinstance(v, list):
                    answers[k] = [str(x) for x in v]
                else:
                    answers[k] = v
        # 保存导航/状态字段
        for key in ("current_section_id", "current_group_id", "current_item_id",
                    "current_sub_item_id", "instruction_read", "time_spent", "time_left"):
            if key in body:
                sess[key] = body[key]
        self.store.save_sessions()
        return {"status": 200, "version_acked": body.get("version", sess.get("version", 0))}

    # ---------- 考试机新版客户端接口 ----------
    def client_login(self, ticket: str) -> dict:
        ticket = (ticket or "").strip()
        candidate = self.store.find_candidate(ticket)
        if not candidate:
            return {"ok": False, "code": "ticket_not_found", "message": "准考证号不存在"}
        exam = self.store.find_exam_for_ticket(ticket)
        if not exam:
            return {"ok": False, "code": "no_exam", "message": "未在进行考试"}
        # 模拟模式：考试机自由登录、自由考试，登录后自动进入答题
        if self.store.get_mode() == "simulation" and exam.get("status") not in ("running", "ended"):
            try:
                self.store.start_exam(exam["id"])
                exam = self.store.find_exam(exam["id"])
            except ValueError:
                pass
        exam = self.store.advance_exam(exam)
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            try:
                sess = self.store.start_session(ticket, exam["id"])
            except Exception as e:
                return {"ok": False, "code": "error", "message": str(e)}
        # 简化会话字段
        sess.setdefault("subject_answers", {})
        sess.setdefault("guide_completed", {})
        sess.setdefault("seat", int(candidate.get("seat_no") or 0) or 1)
        self.store.save_sessions()
        return {"ok": True, "state": self.client_state(ticket, exam["id"])}

    def client_state(self, ticket: str, exam_id: str | None = None) -> dict:
        if not exam_id:
            exam = self.store.find_exam_for_ticket(ticket)
        else:
            exam = self.store.find_exam(exam_id)
        if not exam:
            return {"ok": False, "code": "no_exam", "message": "未在进行考试", "server_time": now_ts()}
        exam = self.store.advance_exam(exam)
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {"ok": False, "code": "no_session", "message": "请先登录", "server_time": now_ts()}
        subjects = self.store.get_exam_subjects(exam)
        idx = int(exam.get("current_subject_index", 0) or 0)
        phase = exam.get("phase", "prepared") if exam.get("status") == "running" else (
            "ended" if exam.get("status") == "ended" else ("prepared" if exam.get("status") == "ready" else "created"))
        now = now_ts()
        phase_ends_at = None
        time_left = None
        subject = None
        next_subject = None
        if phase == "answering" and idx < len(subjects):
            subject = subjects[idx]
            phase_ends_at = exam.get("phase_ends_at")
            time_left = max(0, int(phase_ends_at or 0) - now) if phase_ends_at else None
        elif phase == "guide":
            # 当前处于下一科目引导阶段
            guide_idx = int(exam.get("guide_subject_index", idx + 1) or idx + 1)
            idx = guide_idx
            if guide_idx < len(subjects):
                next_subject = subjects[guide_idx]
                subject = next_subject
                phase_ends_at = exam.get("guide_ends_at")
                time_left = max(0, int(phase_ends_at or 0) - now) if phase_ends_at else None
        # 组装题目（不含答案）
        question_list = []
        if subject and phase in ("answering",):
            for qid in subject.get("question_ids", []):
                q = self.store.find_question(qid)
                if q:
                    question_list.append({
                        "id": q["id"],
                        "type": q.get("type", "sc"),
                        "stem": q.get("stem", ""),
                        "options": q.get("options", []),
                        "score": q.get("score", 1),
                    })
        answers = sess.get("subject_answers", {}).get(subject.get("id") if subject else "", sess.get("answers", {}))
        # 标准模式不向客户端发送下一科的具体信息，由后台统一发信号
        mode = self.store.get_mode()
        return {
            "ok": True,
            "server_time": now,
            "mode": mode,
            "ticket": ticket,
            "candidate": {
                "name": sess.get("candidate_name", ""),
                "seat": sess.get("seat", 1),
            },
            "exam": {
                "id": exam["id"],
                "name": exam.get("name", ""),
                "title": exam.get("title", exam.get("name", "")),
                "stage": exam.get("stage", exam.get("status", "")),
                "phase": phase,
            },
            "current_subject": {
                "index": idx,
                "id": subject.get("id") if subject else None,
                "name": subject.get("name") if subject else None,
                "duration": self.store.subject_duration(exam, idx) if subject and phase == "answering" else (self.store.subject_guide_duration(exam, int(exam.get("guide_subject_index", idx + 1) or idx + 1)) if phase == "guide" else None),
                "allow_early_submit": bool(subject.get("allow_early_submit", False)) if subject else False,
            } if subject else None,
            "phase_ends_at": phase_ends_at,
            "time_left": time_left,
            "guide_completed": bool(sess.get("guide_completed", {}).get(str(idx))),
            "can_enter_answer": bool(not (phase == "answering" and idx > 0 and not sess.get("guide_completed", {}).get(str(idx)))),
            "next_subject": next_subject if mode == "simulation" and next_subject else None,
            "questions": question_list,
            "answers": answers,
            "has_next_subject": idx + 1 < len(subjects),
        }

    def client_submit_answer(self, ticket: str, answers: dict, subject_id: str | None = None) -> dict:
        exam = self.store.find_exam_for_ticket(ticket)
        if not exam:
            return {"ok": False, "message": "未在进行考试"}
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {"ok": False, "message": "请先登录"}
        subs = self.store.get_exam_subjects(exam)
        idx = int(exam.get("current_subject_index", 0) or 0)
        sid = subject_id or (subs[idx].get("id") if idx < len(subs) else None)
        if isinstance(answers, dict):
            if sid:
                sess.setdefault("subject_answers", {}).setdefault(sid, {}).update(answers)
            sess.setdefault("answers", {}).update(answers)
        self.store.save_sessions()
        return {"ok": True, "saved": len(answers)}

    def client_early_submit(self, ticket: str) -> dict:
        exam = self.store.find_exam_for_ticket(ticket)
        if not exam:
            return {"ok": False, "message": "未在进行考试"}
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {"ok": False, "message": "请先登录"}
        subs = self.store.get_exam_subjects(exam)
        idx = int(exam.get("current_subject_index", 0) or 0)
        if idx < len(subs) and not subs[idx].get("allow_early_submit", False):
            return {"ok": False, "message": "后端未开启允许提前交卷"}
        # 标记当前科目完成，进入下一科目引导或结束（模拟模式）
        if idx + 1 < len(subs):
            exam["phase"] = "guide"
            exam["guide_subject_index"] = idx + 1
            exam["guide_ends_at"] = now_ts() + self.store.subject_guide_duration(exam, idx + 1)
            exam["phase_ends_at"] = None
        else:
            exam["phase"] = "ended"
            exam["status"] = "ended"
            exam["end_time"] = now_ts()
        self.store.save_exams()
        return {"ok": True, "state": self.client_state(ticket, exam["id"])}

    def client_complete_guide(self, ticket: str) -> dict:
        exam = self.store.find_exam_for_ticket(ticket)
        if not exam:
            return {"ok": False, "message": "未在进行考试"}
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {"ok": False, "message": "请先登录"}
        idx = int(exam.get("guide_subject_index", exam.get("current_subject_index", 0)) or 0)
        sess.setdefault("guide_completed", {})[str(idx)] = True
        self.store.save_sessions()
        return {"ok": True, "guide_completed": True}

        ticket = str(body.get("permit") or body.get("ticket_no") or "").strip()
        exam = self._resolve_exam(ticket)
        if not exam:
            return {}
        state_value = body.get("state", body)
        if ticket:
            sess = self.store.get_session(ticket, exam["id"])
            if sess:
                sess.setdefault("states", {})[item_id] = state_value
                self.store.save_sessions()
        else:
            # 前端此接口通常不携带 permit，本地单机场景保存到当前场次所有/首个会话。
            for sess in self.store.sessions.values():
                if sess.get("exam_id") == exam["id"]:
                    sess.setdefault("states", {})[item_id] = state_value
            self.store.save_sessions()
        return {}

    def get_state(self, item_id: str, query: dict | None = None) -> dict:
        # 简化：从 query 或默认取最新一场考试
        exam = self._resolve_exam()
        if not exam:
            return {}
        # 无法从 GET 识别考生时，返回空或全部状态中该题
        for sess in self.store.sessions.values():
            if sess.get("exam_id") == exam["id"] and item_id in sess.get("states", {}):
                return {"item_id": item_id, "state": sess["states"][item_id]}
        return {}

    def end(self, body: dict) -> dict:
        ticket = str(body.get("permit") or body.get("ticket_no") or "").strip()
        exam = self._resolve_exam(ticket)
        if exam and ticket:
            self.store.end_session(ticket, exam["id"])
        return {}

    def get_score(self, ticket: str | None = None, exam_id: str | None = None) -> dict:
        exam = self.store.find_exam(exam_id) if exam_id else self._resolve_exam(ticket)
        if not exam or not ticket:
            return {}
        sess = self.store.get_session(ticket, exam["id"])
        if not sess:
            return {}
        # 仅计算本场考试题目得分
        qids = []
        for sec in self.store.get_exam_sections(exam):
            for grp in sec.get("groups", []):
                qids.extend(grp.get("question_ids", []))
        total = 0.0
        got = 0.0
        answers = sess.get("answers", {})
        for qid in qids:
            q = self.store.find_question(qid)
            if not q:
                continue
            total += float(q.get("score", 1) or 1)
            correct = set(str(x).upper() for x in (q.get("answer") or []))
            user = set(str(x).upper() for x in (answers.get(qid) or []))
            if correct and user == correct:
                got += float(q.get("score", 1) or 1)
        sess["score"] = got
        self.store.save_sessions()
        return {"score": got, "total": total, "correct": got, "full": total}


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>本地考试管理系统</title>
<style>
:root {
  --primary:#2563eb; --primary-dark:#1d4ed8; --bg:#f0f4f8; --card:#fff;
  --border:#dbe2ea; --text:#0f172a; --muted:#64748b; --success:#16a34a;
  --warning:#d97706; --danger:#dc2626; --info:#0891b2;
}
* { box-sizing:border-box; }
body {
  margin:0; font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;
  background:var(--bg); color:var(--text); font-size:14px;
}
a { color:var(--primary); text-decoration:none; }
.app { display:flex; min-height:100vh; }
.sidebar {
  width:230px; background:#0f172a; color:#e2e8f0; padding:16px 0;
  display:flex; flex-direction:column; position:sticky; top:0; height:100vh; flex-shrink:0;
}
.brand { padding:8px 20px 16px; font-size:18px; font-weight:700; color:#fff; border-bottom:1px solid #1e293b; }
.brand small { display:block; font-size:12px; color:#94a3b8; font-weight:400; margin-top:4px; }
.nav { padding:12px 0; overflow-y:auto; }
.nav-item {
  display:flex; align-items:center; gap:10px; padding:10px 20px; color:#cbd5e1;
  cursor:pointer; border-left:3px solid transparent; font-size:14px;
}
.nav-item:hover { background:#1e293b; color:#fff; }
.nav-item.active { background:#1e293b; color:#fff; border-left-color:var(--primary); }
.nav-item .icon { width:20px; text-align:center; }
.main { flex:1; min-width:0; display:flex; flex-direction:column; }
.topbar {
  background:#fff; border-bottom:1px solid var(--border); padding:14px 24px;
  display:flex; align-items:center; justify-content:space-between; gap:12px;
}
.topbar h1 { margin:0; font-size:18px; }
.topbar .meta { color:var(--muted); font-size:13px; }
.content { padding:24px; max-width:1400px; width:100%; }
.card {
  background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:18px; margin-bottom:18px; box-shadow:0 1px 2px rgba(15,23,42,.04);
}
.card-title { font-size:16px; font-weight:600; margin:0 0 12px; }
.card-title .sub { font-size:12px; color:var(--muted); font-weight:400; margin-left:8px; }
.grid { display:grid; gap:14px; }
.grid-4 { grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); }
.grid-3 { grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
.stat-card { padding:18px; border-radius:12px; background:#fff; border:1px solid var(--border); }
.stat-card .label { color:var(--muted); font-size:13px; }
.stat-card .value { font-size:30px; font-weight:700; margin:6px 0; }
.stat-card .hint { font-size:12px; color:var(--muted); }
.stat-card.primary { border-top:3px solid var(--primary); }
.stat-card.success { border-top:3px solid var(--success); }
.stat-card.warning { border-top:3px solid var(--warning); }
.stat-card.danger { border-top:3px solid var(--danger); }
.stat-card.info { border-top:3px solid var(--info); }
.toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:14px; }
.toolbar .spacer { flex:1; }
input, select, textarea {
  font:inherit; padding:8px 10px; border:1px solid var(--border); border-radius:8px;
  background:#fff; color:var(--text); min-width:0;
}
input:focus, select:focus, textarea:focus { outline:2px solid #bfdbfe; border-color:var(--primary); }
button {
  font:inherit; padding:8px 14px; border:1px solid transparent; border-radius:8px;
  cursor:pointer; background:var(--primary); color:#fff; transition:.15s;
}
button:hover { background:var(--primary-dark); }
button.secondary { background:#f1f5f9; color:#334155; border-color:var(--border); }
button.secondary:hover { background:#e2e8f0; }
button.success { background:var(--success); }
button.success:hover { background:#15803d; }
button.warning { background:var(--warning); }
button.warning:hover { background:#b45309; }
button.danger { background:var(--danger); }
button.danger:hover { background:#b91c1c; }
button.small { padding:5px 10px; font-size:12px; border-radius:6px; }
button.link { background:none; border:none; color:var(--primary); padding:2px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { border-bottom:1px solid var(--border); padding:9px 10px; text-align:left; vertical-align:middle; }
th { background:#f8fafc; color:#475569; font-weight:600; white-space:nowrap; }
tr:hover td { background:#f8fafc; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:500; }
.badge.blue { background:#dbeafe; color:#1d4ed8; }
.badge.green { background:#dcfce7; color:#15803d; }
.badge.amber { background:#fef3c7; color:#b45309; }
.badge.red { background:#fee2e2; color:#b91c1c; }
.badge.gray { background:#f1f5f9; color:#475569; }
.badge.cyan { background:#cffafe; color:#0e7490; }
.tag { display:inline-block; padding:2px 8px; border-radius:6px; background:#f1f5f9; color:#475569; font-size:12px; }
.empty { text-align:center; padding:32px 12px; color:var(--muted); }
.empty .icon { font-size:40px; margin-bottom:8px; }
.help { background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:12px 16px; font-size:13px; color:#1e40af; margin-bottom:16px; }
.help strong { display:block; margin-bottom:6px; }
.modal-mask {
  position:fixed; inset:0; background:rgba(15,23,42,.45); display:none;
  align-items:center; justify-content:center; z-index:100; padding:20px;
}
.modal-mask.show { display:flex; }
.modal {
  background:#fff; border-radius:14px; width:100%; max-width:560px; max-height:90vh;
  overflow:auto; box-shadow:0 20px 50px rgba(0,0,0,.2);
}
.modal-header { display:flex; align-items:center; justify-content:space-between; padding:16px 20px; border-bottom:1px solid var(--border); }
.modal-header h3 { margin:0; }
.modal-body { padding:20px; }
.modal-footer { padding:14px 20px; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:8px; }
.form-group { margin-bottom:14px; }
.form-group label { display:block; margin-bottom:4px; font-size:13px; color:#475569; font-weight:500; }
.form-group .hint { font-size:12px; color:var(--muted); margin-top:2px; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.form-row-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
.toast-wrap { position:fixed; top:18px; right:18px; z-index:200; display:flex; flex-direction:column; gap:8px; }
.toast {
  background:#0f172a; color:#fff; padding:12px 18px; border-radius:10px;
  box-shadow:0 8px 20px rgba(0,0,0,.18); font-size:14px; max-width:360px;
  animation:slideIn .2s ease;
}
.toast.success { background:var(--success); }
.toast.error { background:var(--danger); }
.toast.warning { background:var(--warning); }
@keyframes slideIn { from { transform:translateX(20px); opacity:0; } to { transform:none; opacity:1; } }
.list-check { margin:6px 0 0; padding-left:20px; }
.list-check li { margin-bottom:6px; }
.mono { font-family:ui-monospace,Consolas,monospace; font-size:12px; }
.section { display:none; }
.section.active { display:block; }
.progress { height:8px; background:#e2e8f0; border-radius:999px; overflow:hidden; }
.progress > div { height:100%; background:var(--primary); border-radius:999px; }
@media (max-width: 800px) {
  .app { flex-direction:column; }
  .sidebar { width:100%; height:auto; position:static; flex-direction:row; overflow-x:auto; }
  .nav { display:flex; padding:0; }
  .nav-item { white-space:nowrap; border-left:none; border-bottom:3px solid transparent; }
  .nav-item.active { border-bottom-color:var(--primary); }
  .brand { border-bottom:none; padding:10px 16px; }
  .content { padding:14px; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"> 考试管理系统<small>本地后端 · 明文存储</small></div>
    <nav class="nav" id="nav">
      <div class="nav-item" data-view="dashboard" onclick="switchView('dashboard')"><span class="icon"></span>总览监控</div>
      <div class="nav-item" data-view="candidates" onclick="switchView('candidates')"><span class="icon"></span>考生管理</div>
      <div class="nav-item" data-view="questions" onclick="switchView('questions')"><span class="icon"></span>试题管理</div>
      <div class="nav-item" data-view="exams" onclick="switchView('exams')"><span class="icon">⏱️</span>考试管理</div>
      <div class="nav-item" data-view="sessions" onclick="switchView('sessions')"><span class="icon"></span>考试记录</div>
      <div class="nav-item" data-view="data" onclick="switchView('data')"><span class="icon"></span>数据管理</div>
      <div class="nav-item" data-view="events" onclick="switchView('events')"><span class="icon"></span>事件日志</div>
    </nav>
  </aside>
  <div class="main">
    <div class="topbar">
      <h1 id="pageTitle">总览监控</h1>
      <div class="meta">
        <span id="refreshMeta">尚未刷新</span>
        <button class="secondary small" onclick="refreshCurrent()">刷新</button>
      </div>
    </div>
    <main class="content" id="content">
      <div class="help" id="globalGuide">
        <strong> 使用引导</strong>
        <div>建议按顺序：<b>1.</b> 创建考试并添加科目/试题 → <b>2.</b> 分配考生 → <b>3.</b> 点击“准备考试” → <b>4.</b> 点击“开始考试” → <b>5.</b> 在考试记录监控答题状态。</div>
      </div>
      <div id="view"></div>
    </main>
  </div>
</div>

<div class="modal-mask" id="modalMask">
  <div class="modal">
    <div class="modal-header">
      <h3 id="modalTitle"></h3>
      <button class="link" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-footer">
      <button class="secondary" onclick="closeModal()">取消</button>
      <button id="modalOk" onclick="modalSubmit()">保存</button>
    </div>
  </div>
</div>
<div class="toast-wrap" id="toastWrap"></div>

<script>
const API = '/api';
let currentView = 'dashboard';
let autoRefreshTimer = null;
let modalCallback = null;

// ---------- 基础工具 ----------
function newId(prefix){ return prefix + '_' + Math.random().toString(36).slice(2,10); }
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function val(id) { const el = document.getElementById(id); return el ? el.value.trim() : ''; }
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v ?? ''; }
function fmtTime(t) { return t ? new Date(t * 1000).toLocaleString('zh-CN', {hour12:false}) : '—'; }
function badge(text, cls) { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function toast(msg, type='info') {
  const wrap = document.getElementById('toastWrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
async function api(path, method='GET', body) {
  const opt = { method, headers: {'Content-Type': 'application/json'} };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const res = await fetch(API + path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) throw new Error(data.message || `请求失败 (${res.status})`);
  return data;
}
function openModal(title, bodyHtml, onSubmit, readOnly=false) {
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = bodyHtml;
  document.getElementById('modalMask').classList.add('show');
  document.getElementById('modalOk').style.display = readOnly ? 'none' : '';
  modalCallback = onSubmit;
}
function closeModal() {
  document.getElementById('modalMask').classList.remove('show');
  modalCallback = null;
}
async function modalSubmit() {
  if (!modalCallback) return closeModal();
  try {
    await modalCallback();
    closeModal();
  } catch (e) {
    toast(e.message, 'error');
  }
}
function confirmAction(msg, fn) {
  if (confirm(msg)) { fn().catch(e => toast(e.message, 'error')); }
}

// ---------- 视图切换 ----------
async function switchView(view) {
  currentView = view;
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  const titles = {
    dashboard:'总览监控', candidates:'考生管理', questions:'试题管理', exams:'考试管理',
    sessions:'考试记录', data:'数据管理', events:'事件日志'
  };
  document.getElementById('pageTitle').textContent = titles[view] || '管理后台';
  await renderView();
  if (view === 'dashboard') startAutoRefresh();
  else stopAutoRefresh();
}
function startAutoRefresh() {
  stopAutoRefresh();
  autoRefreshTimer = setInterval(() => { if (currentView === 'dashboard') renderDashboard(true); }, 5000);
}
function stopAutoRefresh() { if (autoRefreshTimer) clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
async function refreshCurrent() { await renderView(); toast('已刷新'); }
async function renderView() {
  const view = document.getElementById('view');
  view.innerHTML = '<div class="empty"><div class="icon">⏳</div>加载中...</div>';
  try {
    if (currentView === 'dashboard') await renderDashboard();
    else if (currentView === 'candidates') await renderCandidates();
    else if (currentView === 'questions') await renderQuestions();
    else if (currentView === 'exams') await renderExams();
    else if (currentView === 'sessions') await renderSessions();
    else if (currentView === 'data') await renderData();
    else if (currentView === 'events') await renderEvents();
  } catch (e) {
    view.innerHTML = `<div class="empty"><div class="icon">⚠️</div>${esc(e.message)}</div>`;
  }
}

// ---------- 总览监控 ----------
async function renderDashboard(silent=false) {
  const ov = await api('/overview');
  const exams = await api('/exams');
  const sessions = await api('/sessions');
  const events = await api('/events');
  const settings = await api('/settings');
  const runningSessions = sessions.filter(s => !s.ended_at).length;
  const endedSessions = sessions.filter(s => s.ended_at).length;
  const runningExams = exams.filter(e => e.status === 'running').length;
  const autoExams = exams.filter(e => e.auto_start && e.status !== 'ended').length;
  const activeExam = exams.find(e => e.id === ov.active_exam);
  const recent = events.slice(-8).reverse();
  document.getElementById('refreshMeta').textContent = '最近刷新 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false});
  const view = document.getElementById('view');
  view.innerHTML = `
    <div class="grid grid-4">
      <div class="stat-card primary"><div class="label">考生总数</div><div class="value">${ov.candidates}</div><div class="hint">可管理准考证号</div></div>
      <div class="stat-card info"><div class="label">试题总数</div><div class="value">${ov.questions}</div><div class="hint">当前题库</div></div>
      <div class="stat-card success"><div class="label">考试总数</div><div class="value">${ov.exams}</div><div class="hint">进行中 ${runningExams} 场</div></div>
      <div class="stat-card warning"><div class="label">考试记录</div><div class="value">${ov.sessions}</div><div class="hint">进行中 ${runningSessions} · 已交卷 ${endedSessions}</div></div>
    </div>
    <div class="grid grid-3" style="margin-top:16px">
      <div class="card">
        <div class="card-title">当前考试状态 <span class="sub">自动刷新5秒</span></div>
        ${activeExam ? `
          <div style="margin-bottom:10px">${badge('进行中','green')} <b>${esc(activeExam.name)}</b></div>
          <div class="mono">ID: ${esc(activeExam.id)}</div>
          <div style="margin-top:8px">模式：${esc(activeExam.mode === 'unified' ? '统一考试' : '单独启动')} · 自动开考：${activeExam.auto_start ? '开启' : '关闭'}</div>
          <div style="margin-top:10px"><button class="small warning" onclick="stopExam('${esc(activeExam.id)}')">结束本场考试</button></div>
        ` : `<div class="empty"><div class="icon"></div>当前没有进行中的考试<br><button class="small" onclick="switchView('exams')">去考试管理开始</button></div>`}
      </div>
      <div class="card">
        <div class="card-title">运行状态 <span class="sub">建议</span></div>
        <ul class="list-check">
          <li>考生：${ov.candidates > 0 ? '✅ 已配置' : '❌ 暂无考生'}</li>
          <li>试题：${ov.questions > 0 ? '✅ 已配置' : '❌ 暂无试题'}</li>
          <li>考试：${ov.exams > 0 ? '✅ 已创建' : '❌ 暂无考试'}</li>
          <li>自动开考考试：${autoExams > 0 ? `✅ ${autoExams} 场已开放` : '⚠️ 未开启自动开考'}</li>
          <li>系统模式：${settings.mode==='simulation' ? '模拟模式（自由考试）' : '标准模式（统一开考）'}</li>
        </ul>
      </div>
      <div class="card">
        <div class="card-title">快捷操作</div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <button onclick="switchView('candidates')"> 管理考生</button>
          <button onclick="switchView('questions')"> 管理试题</button>
          <button onclick="switchView('exams')">⏱️ 管理考试</button>
          <button class="secondary" onclick="switchView('sessions')"> 查看考试记录</button>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title">最近事件 <span class="sub">最新 8 条</span></div>
      ${recent.length ? `<table><thead><tr><th>时间</th><th>准考证号</th><th>考试</th><th>类型</th></tr></thead><tbody>
        ${recent.map(ev => `<tr><td>${fmtTime(ev.time)}</td><td>${esc(ev.ticket_no||'')}</td><td>${esc(ev.exam_id||'')}</td><td>${esc(ev.type||ev.data?.type||'')}</td></tr>`).join('')}
      </tbody></table>` : '<div class="empty">暂无事件，考试机操作后会显示在这里</div>'}
    </div>`;
  if (!silent) {
    document.getElementById('refreshMeta').textContent = '最近刷新 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false});
  }
}

// ---------- 考生管理 ----------
async function renderCandidates() {
  const list = await api('/candidates');
  const exams = await api('/exams');
  const search = val('candidateSearch');
  const examFilter = window._candidateExamFilter || '';
  const filtered = list.filter(c => { const okSearch = !search || c.ticket_no.includes(search) || (c.name||'').includes(search) || (c.id_card||'').includes(search); const okExam = !examFilter || c.exam_id === examFilter; return okSearch && okExam; });
  const rows = filtered.map(c => {
    const exam = exams.find(e => e.id === c.exam_id);
    return `<tr>
      <td><b>${esc(c.ticket_no)}</b></td>
      <td>${esc(c.name||'')}</td>
      <td>${esc(c.gender||'')}</td>
      <td>${exam ? esc(exam.name) : (c.exam_id ? esc(c.exam_id) : '<span class="muted">未分配</span>')}</td>
      <td>${esc(c.seat_no||'')}</td>
      <td>${esc(c.status||'normal')}</td>
      <td>
        <button class="small" onclick="editCandidate('${esc(c.ticket_no)}')">编辑</button>
        <button class="small danger" onclick="deleteCandidate('${esc(c.ticket_no)}')">删除</button>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('view').innerHTML = `
    ${list.length === 0 ? `<div class="help"><strong>还没有考生</strong>请先添加考生，或在下方点击“新增考生”。准考证号将作为考试机登录账号。</div>` : ''}
    <div class="card">
      <div class="card-title">考生列表 <span class="sub">共 ${filtered.length} / ${list.length} 人</span></div>
      <div class="toolbar">
        <input id="candidateSearch" placeholder="搜索准考证号/姓名/身份证" value="${esc(search)}" oninput="renderCandidates()" style="width:260px">
        <select id="candidateExamFilter" onchange="filterCandidatesByExam()">
          <option value="" ${!examFilter?'selected':''}>全部考试</option>
          ${exams.map(e => `<option value="${esc(e.id)}" ${examFilter===e.id?'selected':''}>${esc(e.name)}</option>`).join('')}
        </select>
        <div class="spacer"></div>
        <button onclick="openAddCandidate()">➕ 新增考生</button>
      </div>
      <table><thead><tr><th>准考证号</th><th>姓名</th><th>性别</th><th>所属考试</th><th>座位号</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7"><div class="empty">没有匹配的考生</div></td></tr>'}</tbody></table>
    </div>`;
}
function filterCandidatesByExam() {
  window._candidateExamFilter = val('candidateExamFilter');
  renderCandidates();
}
async function renderCandidatesWithFilter() {
  const list = await api('/candidates');
  const exams = await api('/exams');
  const search = val('candidateSearch');
  const examFilter = window._candidateExamFilter || '';
  const filtered = list.filter(c => {
    const okSearch = !search || c.ticket_no.includes(search) || (c.name||'').includes(search) || (c.id_card||'').includes(search);
    const okExam = !examFilter || c.exam_id === examFilter;
    return okSearch && okExam;
  });
  const rows = filtered.map(c => {
    const exam = exams.find(e => e.id === c.exam_id);
    return `<tr>
      <td><b>${esc(c.ticket_no)}</b></td>
      <td>${esc(c.name||'')}</td>
      <td>${esc(c.gender||'')}</td>
      <td>${exam ? esc(exam.name) : (c.exam_id ? esc(c.exam_id) : '未分配')}</td>
      <td>${esc(c.seat_no||'')}</td>
      <td>${esc(c.status||'normal')}</td>
      <td><button class="small" onclick="editCandidate('${esc(c.ticket_no)}')">编辑</button><button class="small danger" onclick="deleteCandidate('${esc(c.ticket_no)}')">删除</button></td>
    </tr>`;
  }).join('');
  document.querySelector('#view .card:last-child table tbody').innerHTML = rows || '<tr><td colspan="7"><div class="empty">没有匹配的考生</div></td></tr>';
}
async function openAddCandidate() {
  const exams = await api('/exams');
  openModal('新增考生', `
    <div class="form-group"><label>准考证号 *</label><input id="f_ticket" placeholder="例如 20260001"><div class="hint">考试机输入这个号即可登录/开考</div></div>
    <div class="form-row">
      <div class="form-group"><label>姓名 *</label><input id="f_name" placeholder="考生姓名"></div>
      <div class="form-group"><label>性别</label><select id="f_gender"><option value="">未填写</option><option>男</option><option>女</option></select></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>身份证号</label><input id="f_idcard" placeholder="用于身份核验"></div>
      <div class="form-group"><label>座位号</label><input id="f_seat" placeholder="例如 12"></div>
    </div>
    <div class="form-group"><label>参加考试</label><select id="f_exam"><option value="">暂不分配</option>${exams.map(e => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('')}</select></div>
    <div class="form-group"><label>备注</label><input id="f_remark" placeholder="选填"></div>
  `, async () => {
    const body = {ticket_no: val('f_ticket'), name: val('f_name'), gender: val('f_gender'), id_card: val('f_idcard'), seat_no: val('f_seat'), exam_id: val('f_exam') || null, remark: val('f_remark')};
    await api('/candidates', 'POST', body);
    toast('考生已添加', 'success');
    await renderCandidates();
  });
}
async function editCandidate(ticket) {
  const list = await api('/candidates');
  const exams = await api('/exams');
  const c = list.find(x => x.ticket_no === ticket);
  if (!c) return toast('未找到考生', 'error');
  openModal('编辑考生', `
    <div class="form-group"><label>准考证号</label><input id="f_ticket" value="${esc(c.ticket_no)}" disabled></div>
    <div class="form-row">
      <div class="form-group"><label>姓名</label><input id="f_name" value="${esc(c.name||'')}"></div>
      <div class="form-group"><label>性别</label><select id="f_gender"><option value="">未填写</option><option ${c.gender==='男'?'selected':''}>男</option><option ${c.gender==='女'?'selected':''}>女</option></select></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>身份证号</label><input id="f_idcard" value="${esc(c.id_card||'')}"></div>
      <div class="form-group"><label>座位号</label><input id="f_seat" value="${esc(c.seat_no||'')}"></div>
    </div>
    <div class="form-group"><label>参加考试</label><select id="f_exam"><option value="">暂不分配</option>${exams.map(e => `<option value="${esc(e.id)}" ${c.exam_id===e.id?'selected':''}>${esc(e.name)}</option>`).join('')}</select></div>
    <div class="form-group"><label>状态</label><input id="f_status" value="${esc(c.status||'normal')}" placeholder="normal / disabled"></div>
    <div class="form-group"><label>备注</label><input id="f_remark" value="${esc(c.remark||'')}"></div>
  `, async () => {
    await api('/candidates/' + encodeURIComponent(c.ticket_no), 'PUT', {
      name: val('f_name'), gender: val('f_gender'), id_card: val('f_idcard'), seat_no: val('f_seat'),
      exam_id: val('f_exam') || null, status: val('f_status'), remark: val('f_remark')
    });
    toast('考生已更新', 'success');
    await renderCandidates();
  });
}
function deleteCandidate(ticket) {
  confirmAction(`确定删除考生 ${ticket}？该操作不会删除已有考试记录。`, async () => {
    await api('/candidates/' + encodeURIComponent(ticket), 'DELETE');
    toast('考生已删除', 'success');
    await renderCandidates();
  });
}

// ---------- 试题管理 ----------
async function renderQuestions() {
  const list = await api('/questions');
  const search = val('questionSearch');
  const typeFilter = val('questionTypeFilter');
  window._questionTypeFilter = typeFilter;
  const filtered = list.filter(q => {
    const okSearch = !search || (q.id||'').includes(search) || (q.stem||'').includes(search);
    const okType = !typeFilter || q.type === typeFilter;
    return okSearch && okType;
  });
  const rows = filtered.map(q => `<tr>
    <td class="mono">${esc(q.id)}</td>
    <td>${typeBadge(q.type)}</td>
    <td style="max-width:380px">${esc(String(q.stem||'').slice(0,80))}</td>
    <td>${esc((q.answer||[]).join(','))}</td>
    <td>${esc(q.score)}</td>
    <td>${esc(q.group||q.section||'')}</td>
    <td>${q.enabled ? badge('启用','green') : badge('停用','gray')}</td>
    <td>
      <button class="small" onclick="editQuestion('${esc(q.id)}')">编辑</button>
      <button class="small danger" onclick="deleteQuestion('${esc(q.id)}')">删除</button>
    </td>
  </tr>`).join('');
  document.getElementById('view').innerHTML = `
    ${list.length === 0 ? `<div class="help"><strong>题库是空的</strong>请添加试题。试题可以按科目/分组组织，考试时按组抽取。</div>` : ''}
    <div class="card">
      <div class="card-title">试题列表 <span class="sub">共 ${filtered.length} / ${list.length} 题</span></div>
      <div class="toolbar">
        <input id="questionSearch" placeholder="搜索题干/ID" value="${esc(search)}" oninput="renderQuestions()" style="width:240px">
        <select id="questionTypeFilter" onchange="renderQuestions()">
          <option value="" ${!typeFilter?'selected':''}>全部题型</option>
          <option value="sc" ${typeFilter==='sc'?'selected':''}>单选题</option>
          <option value="mc" ${typeFilter==='mc'?'selected':''}>多选题</option>
          <option value="judge" ${typeFilter==='judge'?'selected':''}>判断题</option>
          <option value="fill" ${typeFilter==='fill'?'selected':''}>填空题</option>
          <option value="sa" ${typeFilter==='sa'?'selected':''}>简答题</option>
        </select>
        <div class="spacer"></div>
        <button onclick="addQuestion()">➕ 新增试题</button>
      </div>
      <table><thead><tr><th>ID</th><th>题型</th><th>题干</th><th>答案</th><th>分值</th><th>分组/科目</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8"><div class="empty">没有匹配的试题</div></td></tr>'}</tbody></table>
    </div>`;
}
function typeBadge(t) {
  const map = {sc:'单选', mc:'多选', judge:'判断', fill:'填空', sa:'简答'};
  return badge(map[t] || t || '未知', t==='sc'?'blue':t==='mc'?'cyan':'gray');
}
function addQuestion() {
  openModal('新增试题', `
    <div class="form-row">
      <div class="form-group"><label>题型</label><select id="q_type"><option value="sc">单选题</option><option value="mc">多选题</option><option value="judge">判断题</option><option value="fill">填空题</option><option value="sa">简答题</option></select></div>
      <div class="form-group"><label>分值</label><input id="q_score" type="number" step="0.5" value="1"></div>
    </div>
    <div class="form-group"><label>题干 *</label><textarea id="q_stem" rows="3" placeholder="请输入题干"></textarea></div>
    <div class="form-group"><label>选项（JSON 数组）</label><textarea id="q_options" rows="4" class="mono" placeholder='[{"id":"A","description":"选项内容"},{"id":"B","description":"选项内容"}]'></textarea><div class="hint">也可先留空，编辑时再补充；单选/多选建议使用 A/B/C/D。</div></div>
    <div class="form-row">
      <div class="form-group"><label>答案</label><input id="q_answer" placeholder="单选填 A；多选填 A,B"></div>
      <div class="form-group"><label>分组/科目</label><input id="q_group" placeholder="例如 科目一/单选题" value="科目一"></div>
    </div>
    <div class="form-group"><label>解析/备注</label><input id="q_explanation" placeholder="选填"></div>
  `, async () => {
    let options = [];
    try { options = JSON.parse(val('q_options') || '[]'); } catch (e) { throw new Error('选项 JSON 格式错误：' + e.message); }
    const answer = val('q_answer').split(',').map(s => s.trim()).filter(Boolean);
    await api('/questions', 'POST', {
      type: val('q_type'), stem: val('q_stem'), score: parseFloat(val('q_score') || 1),
      options, answer, group: val('q_group'), section: val('q_group'), explanation: val('q_explanation')
    });
    toast('试题已添加', 'success');
    await renderQuestions();
  });
}
async function editQuestion(id) {
  const list = await api('/questions');
  const q = list.find(x => x.id === id);
  if (!q) return toast('试题不存在', 'error');
  openModal('编辑试题', `
    <div class="form-group"><label>ID</label><input value="${esc(q.id)}" disabled></div>
    <div class="form-row">
      <div class="form-group"><label>题型</label><select id="q_type">${['sc','mc','judge','fill','sa'].map(t => `<option value="${t}" ${q.type===t?'selected':''}>${t==='sc'?'单选':t==='mc'?'多选':t==='judge'?'判断':t==='fill'?'填空':'简答'}</option>`).join('')}</select></div>
      <div class="form-group"><label>分值</label><input id="q_score" type="number" step="0.5" value="${esc(q.score||1)}"></div>
    </div>
    <div class="form-group"><label>题干</label><textarea id="q_stem" rows="3">${esc(q.stem||'')}</textarea></div>
    <div class="form-group"><label>选项（JSON 数组）</label><textarea id="q_options" rows="4" class="mono">${esc(JSON.stringify(q.options||[], null, 2))}</textarea></div>
    <div class="form-row">
      <div class="form-group"><label>答案</label><input id="q_answer" value="${esc((q.answer||[]).join(','))}"></div>
      <div class="form-group"><label>分组/科目</label><input id="q_group" value="${esc(q.group||q.section||'')}"></div>
    </div>
    <div class="form-group"><label>解析/备注</label><input id="q_explanation" value="${esc(q.explanation||'')}"></div>
    <div class="form-group"><label>是否启用</label><select id="q_enabled"><option value="true" ${q.enabled!==false?'selected':''}>启用</option><option value="false" ${q.enabled===false?'selected':''}>停用</option></select></div>
  `, async () => {
    let options = [];
    try { options = JSON.parse(val('q_options') || '[]'); } catch (e) { throw new Error('选项 JSON 格式错误：' + e.message); }
    const answer = val('q_answer').split(',').map(s => s.trim()).filter(Boolean);
    await api('/questions/' + encodeURIComponent(id), 'PUT', {
      type: val('q_type'), stem: val('q_stem'), score: parseFloat(val('q_score') || 1),
      options, answer, group: val('q_group'), section: val('q_group'), explanation: val('q_explanation'),
      enabled: val('q_enabled') === 'true'
    });
    toast('试题已更新', 'success');
    await renderQuestions();
  });
}
function deleteQuestion(id) {
  confirmAction(`确定删除试题 ${id}？`, async () => {
    await api('/questions/' + encodeURIComponent(id), 'DELETE');
    toast('试题已删除', 'success');
    await renderQuestions();
  });
}

// ---------- 考试管理 ----------
async function renderExams() {
  const list = await api('/exams');
  const sessions = await api('/sessions');
  const candidates = await api('/candidates');
  const rows = list.map(e => {
    const sessList = sessions.filter(s => s.exam_id === e.id);
    const runningCount = sessList.filter(s => !s.ended_at).length;
    return `<tr>
      <td class="mono">${esc(e.id)}</td>
      <td><b>${esc(e.name)}</b></td>
      <td>${examStageBadge(e)}</td>
      <td>${e.mode === 'unified' ? badge('统一','blue') : badge('单独','amber')}</td>
      <td>${e.auto_start ? badge('自动开考','green') : badge('手动','gray')}</td>
      <td>${esc(e.duration_minutes)} 分钟</td>
      <td>${e.candidate_tickets?.length || 0} 人 / 进行中 ${runningCount}</td>
      <td>
        ${e.stage === 'created' || e.status === 'draft' ? `<button class="small" onclick="prepareExam('${esc(e.id)}')">准备考试</button>` : ''}
        ${e.status === 'running' ? `<button class="small warning" onclick="stopExam('${esc(e.id)}')">结束</button>` : (e.status === 'ready' ? `<button class="small success" onclick="startExam('${esc(e.id)}')">开始考试</button>` : '')}
        ${e.status === 'draft' ? `<button class="small success" onclick="startExam('${esc(e.id)}')">直接开始</button>` : ''}
        <button class="small" onclick="toggleAuto('${esc(e.id)}')">${e.auto_start ? '关闭自动' : '开启自动'}</button>
        <button class="small secondary" onclick="assignExam('${esc(e.id)}')">分配考生</button>
        <button class="small secondary" onclick="manageSubjects('${esc(e.id)}')">科目</button>
        <button class="small" onclick="editExam('${esc(e.id)}')">编辑</button>
        <button class="small danger" onclick="deleteExam('${esc(e.id)}')">删除</button>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('view').innerHTML = `
    ${list.length === 0 ? `<div class="help"><strong>还没有考试</strong>建议先创建考试，再通过“分配考生”把考生加入本场考试。</div>` : ''}
    <div class="card">
      <div class="card-title">考试列表 <span class="sub">共 ${list.length} 场</span></div>
      <div class="toolbar">
        <div class="spacer"></div>
        <button onclick="addExam()">➕ 创建考试</button>
      </div>
      <table><thead><tr><th>ID</th><th>考试名称</th><th>状态</th><th>模式</th><th>开考方式</th><th>时长</th><th>考生/进行中</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8"><div class="empty">暂无考试</div></td></tr>'}</tbody></table>
    </div>`;
}
function examStatusBadge(s) {
  const map = {draft:['草稿','gray'], ready:['就绪','blue'], running:['进行中','green'], ended:['已结束','red']};
  const [text, cls] = map[s] || [s, 'gray'];
  return badge(text, cls);
}
function examStageBadge(e) {
  const stage = e.stage || e.status || 'created';
  const map = {created:['创建阶段','gray'], prepared:['准备完成','blue'], ready:['准备完成','blue'], running:['开始考试','green'], ended:['已结束','red']};
  const [text, cls] = map[stage] || [stage, 'gray'];
  return badge(text, cls);
}
async function prepareExam(id) {
  await api('/exams/' + encodeURIComponent(id) + '/prepare', 'POST', {});
  toast('考试已进入准备阶段', 'success');
  await renderExams();
}
function addExam() {
  openModal('创建考试', `
    <div class="help"><strong>创建流程</strong>先填写考试基本信息；如需直接配置科目，可在下方填写“初始科目”，之后仍可在考试列表继续添加科目。</div>
    <div class="form-group"><label>考试名称 *</label><input id="e_name" placeholder="例如 2026年期中考试"></div>
    <div class="form-row">
      <div class="form-group"><label>时长（分钟）</label><input id="e_duration" type="number" value="20" min="1"></div>
      <div class="form-group"><label>模式</label><select id="e_mode"><option value="unified">统一考试（一场考试统一开始）</option><option value="individual">单独启动（每位考生单独开考）</option></select></div>
    </div>
    <div class="form-group"><label><input type="checkbox" id="e_auto" checked> 开启自动开考</label><div class="hint">开启后，考试机输入准考证号即可自动开始该考生的考试。</div></div>
    <div class="form-group"><label>考试说明/通知</label><textarea id="e_notice" rows="3" placeholder="可填写考试须知 HTML 或纯文本"></textarea></div>
    <hr>
    <div class="card-title">初始科目（可选）</div>
    <div class="form-row">
      <div class="form-group"><label>科目名称</label><input id="e_sub_name" placeholder="例如 科目一"></div>
      <div class="form-group"><label>答题时长（分钟）</label><input id="e_sub_duration" type="number" value="10"></div>
    </div>
    <div class="form-group"><label>试题ID（逗号分隔）</label><input id="e_sub_questions" placeholder="q1,q2,q3"></div>
    <div class="form-group"><label><input type="checkbox" id="e_sub_early"> 允许该科目提前交卷</label></div>
  `, async () => {
    const subjects = [];
    const subName = val('e_sub_name');
    const qids = val('e_sub_questions').split(',').map(s => s.trim()).filter(Boolean);
    if (subName || qids.length) {
      subjects.push({
        id: newId('subj'),
        name: subName || '科目一',
        duration: parseInt(val('e_sub_duration') || 10) * 60,
        guide_duration: 600,
        question_ids: qids,
        allow_early_submit: document.getElementById('e_sub_early').checked
      });
    }
    await api('/exams', 'POST', {
      name: val('e_name'), duration_minutes: parseInt(val('e_duration') || 20),
      mode: val('e_mode'), auto_start: document.getElementById('e_auto').checked,
      notice: val('e_notice'), subjects
    });
    toast('考试已创建', 'success');
    await renderExams();
  });
}
async function editExam(id) {
  const list = await api('/exams');
  const e = list.find(x => x.id === id);
  if (!e) return toast('考试不存在', 'error');
  openModal('编辑考试', `
    <div class="form-group"><label>考试名称</label><input id="e_name" value="${esc(e.name)}"></div>
    <div class="form-row">
      <div class="form-group"><label>时长（分钟）</label><input id="e_duration" type="number" value="${esc(e.duration_minutes||20)}"></div>
      <div class="form-group"><label>模式</label><select id="e_mode"><option value="unified" ${e.mode!=='individual'?'selected':''}>统一考试</option><option value="individual" ${e.mode==='individual'?'selected':''}>单独启动</option></select></div>
    </div>
    <div class="form-group"><label><input type="checkbox" id="e_auto" ${e.auto_start?'checked':''}> 自动开考</label></div>
    <div class="form-group"><label>考试说明/通知</label><textarea id="e_notice" rows="3">${esc(e.notice||'')}</textarea></div>
  `, async () => {
    await api('/exams/' + encodeURIComponent(id), 'PUT', {
      name: val('e_name'), duration_minutes: parseInt(val('e_duration') || 20),
      mode: val('e_mode'), auto_start: document.getElementById('e_auto').checked,
      notice: val('e_notice')
    });
    toast('考试已更新', 'success');
    await renderExams();
  });
}
async function startExam(id) {
  await api('/exams/' + encodeURIComponent(id) + '/start', 'POST', {});
  toast('考试已开始', 'success');
  await renderExams();
}
async function stopExam(id) {
  confirmAction(`确定结束考试 ${id}？结束后考生将不能继续答题。`, async () => {
    await api('/exams/' + encodeURIComponent(id) + '/stop', 'POST', {});
    toast('考试已结束', 'success');
    await renderExams();
  });
}
async function toggleAuto(id) {
  const list = await api('/exams');
  const e = list.find(x => x.id === id);
  await api('/exams/' + encodeURIComponent(id) + '/auto', 'POST', {enabled: !e.auto_start});
  toast(e.auto_start ? '已关闭自动开考' : '已开启自动开考', 'success');
  await renderExams();
}
async function manageSubjects(id) {
  const exam = (await api('/exams')).find(x => x.id === id);
  const subjects = await api('/exams/' + encodeURIComponent(id) + '/subjects');
  if (!exam) return;
  const rows = subjects.map((s, i) => `<tr>
    <td>${i+1}</td><td>${esc(s.name)}</td><td>${Math.round((s.duration||0)/60)} 分钟</td>
    <td>${(s.question_ids||[]).length} 题</td><td>${s.allow_early_submit?'允许提前交':'不允许'}</td>
    <td><button class="small danger" onclick="deleteSubject('${esc(id)}','${esc(s.id)}')">删除</button></td>
  </tr>`).join('');
  openModal('管理科目：' + esc(exam.name), `
    <div class="help"><strong>科目说明</strong>一场考试可包含多个科目；每个科目独立倒计时，时间到自动收卷并进入下一科引导。</div>
    <table><thead><tr><th>#</th><th>科目名</th><th>时长</th><th>题数</th><th>交卷</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td colspan="6">暂无科目</td></tr>'}</tbody></table>
    <div style="margin-top:12px"><button onclick="openAddSubject('${esc(id)}')">添加科目</button></div>
  `, null, true);
}
function openAddSubject(id) {
  openModal('添加科目', `
    <div class="form-group"><label>科目名称</label><input id="sub_name" placeholder="例如 科目一"></div>
    <div class="form-row">
      <div class="form-group"><label>答题时长（分钟）</label><input id="sub_duration" type="number" value="10"></div>
      <div class="form-group"><label>引导时长（分钟）</label><input id="sub_guide" type="number" value="10"></div>
    </div>
    <div class="form-group"><label>试题ID（逗号分隔）</label><input id="sub_questions" placeholder="q1,q2,q3"></div>
    <div class="form-group"><label><input type="checkbox" id="sub_early"> 允许提前交卷</label></div>
  `, async () => {
    const qids = val('sub_questions').split(',').map(s => s.trim()).filter(Boolean);
    await api('/exams/' + encodeURIComponent(id) + '/subjects', 'POST', {
      name: val('sub_name'), duration_minutes: parseInt(val('sub_duration')||10),
      guide_duration: parseInt(val('sub_guide')||10) * 60, question_ids: qids,
      allow_early_submit: document.getElementById('sub_early').checked
    });
    toast('科目已添加', 'success');
    await manageSubjects(id);
  });
}
async function deleteSubject(id, sid) {
  confirmAction(`确定删除科目 ${sid}？`, async () => {
    await api('/exams/' + encodeURIComponent(id) + '/subjects/' + encodeURIComponent(sid), 'DELETE');
    toast('科目已删除', 'success');
    await manageSubjects(id);
  });
}
async function assignExam(id) {
  const exams = await api('/exams');
  const candidates = await api('/candidates');
  const e = exams.find(x => x.id === id);
  if (!e) return;
  const assigned = new Set(e.candidate_tickets || []);
  const unassigned = candidates.filter(c => !assigned.has(c.ticket_no));
  openModal('分配考生到考试', `
    <div class="help"><strong>分配说明</strong>已分配考生可直接在考试机使用准考证号登录；可多选。</div>
    <div class="form-group">
      <label>选择考生</label>
      <div style="max-height:300px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:8px">
        ${candidates.length === 0 ? '<div class="empty">暂无考生，请先到考生管理添加</div>' : candidates.map(c => `<label style="display:flex;gap:8px;padding:4px 2px"><input type="checkbox" class="assign-check" value="${esc(c.ticket_no)}" ${assigned.has(c.ticket_no)?'checked':''}> <span>${esc(c.ticket_no)} - ${esc(c.name||'')}</span></label>`).join('')}
      </div>
    </div>
    <div class="form-group"><label>或手动输入准考证号（逗号分隔）</label><input id="assign_manual" placeholder="20260001,20260002"></div>
  `, async () => {
    const checks = [...document.querySelectorAll('.assign-check:checked')].map(el => el.value);
    const manual = val('assign_manual').split(',').map(s => s.trim()).filter(Boolean);
    const tickets = [...new Set([...checks, ...manual])];
    await api('/exams/' + encodeURIComponent(id) + '/assign', 'POST', {tickets});
    toast('考生分配已保存', 'success');
    await renderExams();
  });
}
function deleteExam(id) {
  confirmAction(`确定删除考试 ${id}？考试记录会保留，但考试配置将不可恢复。`, async () => {
    await api('/exams/' + encodeURIComponent(id), 'DELETE');
    toast('考试已删除', 'success');
    await renderExams();
  });
}

// ---------- 考试记录 ----------
async function renderSessions() {
  const list = await api('/sessions');
  const exams = await api('/exams');
  const search = val('sessionSearch');
  const statusFilter = val('sessionStatus');
  window._sessionStatus = statusFilter;
  const filtered = list.filter(s => {
    const okSearch = !search || s.ticket_no.includes(search) || (s.candidate_name||'').includes(search);
    const okStatus = !statusFilter || (statusFilter === 'running' ? !s.ended_at : statusFilter === 'ended' ? !!s.ended_at : true);
    return okSearch && okStatus;
  });
  const rows = filtered.map(s => {
    const exam = exams.find(e => e.id === s.exam_id);
    return `<tr>
      <td><b>${esc(s.ticket_no)}</b></td>
      <td>${esc(s.candidate_name||'')}</td>
      <td>${exam ? esc(exam.name) : esc(s.exam_id||'')}</td>
      <td>${fmtTime(s.started_at)}</td>
      <td>${s.ended_at ? badge('已交卷','red') : badge('进行中','green')}</td>
      <td>${s.score ?? '—'}</td>
      <td>${Object.keys(s.answers||{}).length} 题已答</td>
      <td>
        <button class="small secondary" onclick="viewSession('${esc(s.key)}')">详情</button>
        ${s.ended_at ? '' : `<button class="small warning" onclick="stopSession('${esc(s.ticket_no)}','${esc(s.exam_id)}')">结束</button>`}
      </td>
    </tr>`;
  }).join('');
  document.getElementById('view').innerHTML = `
    <div class="card">
      <div class="card-title">考试记录 <span class="sub">共 ${filtered.length} / ${list.length} 条</span></div>
      <div class="toolbar">
        <input id="sessionSearch" placeholder="搜索准考证号/姓名" value="${esc(search)}" oninput="renderSessions()" style="width:220px">
        <select id="sessionStatus" onchange="renderSessions()">
          <option value="" ${!statusFilter?'selected':''}>全部状态</option>
          <option value="running" ${statusFilter==='running'?'selected':''}>进行中</option>
          <option value="ended" ${statusFilter==='ended'?'selected':''}>已交卷</option>
        </select>
        <div class="spacer"></div>
        <button class="secondary" onclick="openStartSession()">手动开考</button>
      </div>
      <table><thead><tr><th>准考证号</th><th>姓名</th><th>考试</th><th>开始时间</th><th>状态</th><th>得分</th><th>答题数</th><th>操作</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="8"><div class="empty">暂无考试记录</div></td></tr>'}</tbody></table>
    </div>`;
}
async function openStartSession() {
  const candidates = await api('/candidates');
  const exams = await api('/exams');
  openModal('手动开考', `
    <div class="help"><strong>用于单独启动模式</strong>给指定考生创建/开始一场考试记录；自动开考模式一般不需要手动操作。</div>
    <div class="form-group"><label>考生</label><select id="s_ticket"><option value="">请选择考生</option>${candidates.map(c => `<option value="${esc(c.ticket_no)}">${esc(c.ticket_no)} - ${esc(c.name||'')}</option>`).join('')}</select></div>
    <div class="form-group"><label>考试</label><select id="s_exam"><option value="">请选择考试</option>${exams.map(e => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('')}</select></div>
  `, async () => {
    if (!val('s_ticket') || !val('s_exam')) throw new Error('请选择考生和考试');
    await api('/sessions/start', 'POST', {ticket: val('s_ticket'), exam: val('s_exam')});
    toast('已手动开考', 'success');
    await renderSessions();
  });
}
async function viewSession(key) {
  const list = await api('/sessions');
  const s = list.find(x => x.key === key);
  if (!s) return toast('记录不存在', 'error');
  const answers = Object.entries(s.answers || {}).map(([id, ans]) => `<tr><td class="mono">${esc(id)}</td><td>${esc(Array.isArray(ans)?ans.join(','):ans)}</td></tr>`).join('');
  openModal('考试记录详情', `
    <div class="form-row">
      <div class="form-group"><label>准考证号</label><div>${esc(s.ticket_no)}</div></div>
      <div class="form-group"><label>姓名</label><div>${esc(s.candidate_name||'')}</div></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>考试</label><div>${esc(s.exam_id)}</div></div>
      <div class="form-group"><label>得分</label><div>${s.score ?? '未评分'}</div></div>
    </div>
    <div class="form-group"><label>开始时间</label><div>${fmtTime(s.started_at)}</div></div>
    <div class="form-group"><label>结束时间</label><div>${fmtTime(s.ended_at)}</div></div>
    <div class="form-group"><label>答题情况</label>${answers ? `<table><thead><tr><th>题目ID</th><th>答案</th></tr></thead><tbody>${answers}</tbody></table>` : '<div class="empty">暂无作答</div>'}</div>
    <div class="form-group"><label>事件数</label><div>${(s.events||[]).length}</div></div>
  `, null, true);
}
async function stopSession(ticket, examId) {
  confirmAction(`确定结束 ${ticket} 的考试记录？`, async () => {
    await api('/sessions/stop', 'POST', {ticket, exam: examId});
    toast('考试记录已结束', 'success');
    await renderSessions();
  });
}

// ---------- 数据管理 ----------
async function renderData() {
  const ov = await api('/overview');
  const settings = await api('/settings');
  document.getElementById('view').innerHTML = `
    <div class="grid grid-3">
      <div class="card">
        <div class="card-title">系统模式</div>
        <p class="muted">模拟模式可自由多场并发；标准模式全局同一时间只有一场考试，由后台统一发开考信号。</p>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="${settings.mode==='simulation'?'success':'secondary'}" onclick="setMode('simulation')">模拟模式</button>
          <button class="${settings.mode==='standard'?'success':'secondary'}" onclick="setMode('standard')">标准模式</button>
        </div>
        <div style="margin-top:8px" class="info">当前：${settings.mode==='simulation'?'模拟模式（默认）':'标准模式'}</div>
      </div>
      <div class="card">
        <div class="card-title">数据导出</div>
        <p class="muted">导出全部考生、试题、考试、考试记录和事件为 JSON 明文文件。</p>
        <button class="success" onclick="exportData()">⬇ 导出全部数据</button>
      </div>
      <div class="card">
        <div class="card-title">数据导入</div>
        <p class="muted">粘贴之前导出的 JSON，或选择本地 JSON 文件进行恢复。</p>
        <textarea id="importText" rows="5" class="mono" placeholder='{"candidates":[],"questions":[]...}'></textarea>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button onclick="importData()">导入</button>
          <button class="secondary" onclick="loadImportExample()">载入示例</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">数据文件位置</div>
        <p class="muted">数据保存在以下明文 JSON 文件中，可随时备份。</p>
        <ul class="mono" style="line-height:2">
          <li>data/candidates.json</li>
          <li>data/questions.json</li>
          <li>data/exams.json</li>
          <li>data/sessions.json</li>
          <li>data/events.json</li>
        </ul>
        <div class="muted">当前共有考生 ${ov.candidates} 人、试题 ${ov.questions} 道、考试 ${ov.exams} 场。</div>
      </div>
    </div>`;
}
async function setMode(mode) {
  await api('/settings', 'PUT', {mode});
  toast('系统模式已切换为 ' + (mode === 'simulation' ? '模拟模式' : '标准模式'), 'success');
  await renderData();
}
async function exportData() {
  const res = await fetch(API + '/data/export');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'exam_data_' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
  URL.revokeObjectURL(url);
  toast('导出已开始', 'success');
}
async function importData() {
  const text = val('importText');
  if (!text) return toast('请先粘贴或载入 JSON', 'warning');
  let data;
  try { data = JSON.parse(text); } catch (e) { return toast('JSON 格式错误：' + e.message, 'error'); }
  await api('/data/import', 'POST', data);
  toast('数据导入成功', 'success');
  await renderData();
}
async function loadImportExample() {
  const res = await fetch(API + '/data/export');
  const data = await res.json();
  document.getElementById('importText').value = JSON.stringify(data, null, 2);
  toast('已载入当前数据作为示例', 'info');
}

// ---------- 事件日志 ----------
async function renderEvents() {
  const events = await api('/events');
  const rows = events.slice().reverse().slice(0,100).map(ev => `<tr>
    <td>${fmtTime(ev.time)}</td>
    <td>${esc(ev.ticket_no||'')}</td>
    <td>${esc(ev.exam_id||'')}</td>
    <td>${esc(ev.type||'')}</td>
    <td class="mono" style="max-width:420px">${esc(JSON.stringify(ev.data||{}))}</td>
  </tr>`).join('');
  document.getElementById('view').innerHTML = `
    <div class="help"><strong>事件日志</strong>记录考生登录、交卷、暂离等事件，最多显示最近 100 条。</div>
    <div class="card">
      <div class="card-title">事件列表 <span class="sub">共 ${events.length} 条</span></div>
      <table><thead><tr><th>时间</th><th>准考证号</th><th>考试</th><th>类型</th><th>数据</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5"><div class="empty">暂无事件</div></td></tr>'}</tbody></table>
    </div>`;
}

// ---------- 初始化 ----------
window._examsCache = [];
(async function init() {
  try { window._examsCache = await api('/exams'); } catch (e) {}
  switchView('dashboard');
})();
</script>
</body>
</html>
"""


class ExamHTTPHandler(BaseHTTPRequestHandler):
    server_version = "ExamBackend/1.0"
    store: DataStore = None  # set by server
    service: ExamService = None

    def log_message(self, fmt, *args):
        # 减少前端静态资源噪音
        url = str(args[1]) if len(args) > 1 else ""
        if url.startswith("/client/") or url.startswith("/seat/skin/") or url.startswith("/seat/resource/"):
            return
        super().log_message(fmt, *args)

    # ---- helpers ----
    def _send_bytes(self, data: bytes, content_type: str = "application/octet-stream", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _send_json(self, obj, status: int = 200):
        self._send_bytes(json_dumps(obj), "application/json; charset=utf-8", status)

    def _send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8"):
        self._send_bytes(text.encode("utf-8"), content_type, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        return parse_json_body(body)

    def _query(self) -> dict:
        parsed = urlparse(self.path)
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}

    # ---- dispatch ----
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/admin" or path == "/admin/":
                self._send_text(ADMIN_HTML, content_type="text/html; charset=utf-8")
                return
            if path == "/exam-client" or path == "/exam-client/":
                client_file = ROOT / "exam_client.html"
                if client_file.exists():
                    self._send_bytes(client_file.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send_text("考试机客户端页面缺失", 404)
                return
            if path.startswith("/api/"):
                self._handle_api(method, path, self._query(), self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {})
                return
            if path.startswith("/seat/") or path == "/ts-api/v1/time":
                self._handle_seat(method, path, self._query(), self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {})
                return
            if path == "/" or path == "":
                self.send_response(302)
                self.send_header("Location", "/demo/t/xajtdxsnb_20260123/1")
                self.end_headers()
                return
            self._serve_static(path)
        except Exception as e:
            self._send_json({"status": "error", "message": str(e)}, 500)

    # ---- admin API ----
    def _handle_api(self, method: str, path: str, query: dict, body: dict):
        store = self.store
        # overview
        if path in ("/api/overview", "/api/overview/") and method == "GET":
            return self._send_json({
                "candidates": len(store.candidates),
                "questions": len(store.questions),
                "exams": len(store.exams),
                "sessions": len(store.sessions),
                "active_exam": store.active_exam()["id"] if store.active_exam() else None,
            })
        if path in ("/api/events", "/api/events/") and method == "GET":
            return self._send_json(store.events)
        if path in ("/api/settings", "/api/settings/") and method == "GET":
            return self._send_json(store.settings)
        if path in ("/api/settings", "/api/settings/") and method in ("PUT", "PATCH", "POST"):
            try:
                return self._send_json(store.set_mode(str(body.get("mode", store.get_mode()))))
            except ValueError as e:
                return self._send_json({"status": "error", "message": str(e)}, 400)
        # candidates
        if path in ("/api/candidates", "/api/candidates/"):
            if method == "GET":
                return self._send_json(store.candidates)
            if method == "POST":
                try:
                    return self._send_json(store.add_candidate(body), 201)
                except ValueError as e:
                    return self._send_json({"status": "error", "message": str(e)}, 400)
        if path.startswith("/api/candidates/"):
            ticket = path[len("/api/candidates/"):].strip("/")
            if method == "GET":
                c = store.find_candidate(ticket)
                return self._send_json(c or {"status": "error", "message": "not found"}, 200 if c else 404)
            if method in ("PUT", "PATCH"):
                c = store.update_candidate(ticket, body)
                return self._send_json(c or {"status": "error", "message": "not found"}, 200 if c else 404)
            if method == "DELETE":
                ok = store.delete_candidate(ticket)
                return self._send_json({"deleted": ok}, 200 if ok else 404)
        # questions
        if path in ("/api/questions", "/api/questions/"):
            if method == "GET":
                return self._send_json(store.questions)
            if method == "POST":
                try:
                    return self._send_json(store.add_question(body), 201)
                except ValueError as e:
                    return self._send_json({"status": "error", "message": str(e)}, 400)
        if path.startswith("/api/questions/"):
            qid = path[len("/api/questions/"):].strip("/")
            if method == "GET":
                q = store.find_question(qid)
                return self._send_json(q or {"status": "error", "message": "not found"}, 200 if q else 404)
            if method in ("PUT", "PATCH"):
                q = store.update_question(qid, body)
                return self._send_json(q or {"status": "error", "message": "not found"}, 200 if q else 404)
            if method == "DELETE":
                ok = store.delete_question(qid)
                return self._send_json({"deleted": ok}, 200 if ok else 404)
        # exams
        if path in ("/api/exams", "/api/exams/"):
            if method == "GET":
                return self._send_json(store.exams)
            if method == "POST":
                try:
                    return self._send_json(store.add_exam(body), 201)
                except ValueError as e:
                    return self._send_json({"status": "error", "message": str(e)}, 400)
        if path.startswith("/api/exams/"):
            rest = path[len("/api/exams/"):]
            # /api/exams/<id>/start|stop|auto|assign|unassign
            parts = rest.split("/", 1)
            exam_id = parts[0]
            action = parts[1].strip("/") if len(parts) > 1 else ""
            exam = store.find_exam(exam_id)
            if not exam:
                return self._send_json({"status": "error", "message": "考试不存在"}, 404)
            if not action and method == "GET":
                return self._send_json(exam)
            if action == "prepare" and method == "POST":
                try:
                    e = store.prepare_exam(exam_id)
                    return self._send_json(e or {"status": "error", "message": "not found"}, 200 if e else 404)
                except ValueError as ex:
                    return self._send_json({"status": "error", "message": str(ex)}, 400)
            if action == "start" and method == "POST":
                try:
                    e = store.start_exam(exam_id)
                    return self._send_json(e or {"status": "error", "message": "not found"}, 200 if e else 404)
                except ValueError as ex:
                    return self._send_json({"status": "error", "message": str(ex)}, 400)
            if action == "stop" and method == "POST":
                e = store.end_exam(exam_id)
                return self._send_json(e or {"status": "error", "message": "not found"}, 200 if e else 404)
            if action == "auto" and method == "POST":
                store.update_exam(exam_id, {"auto_start": bool(body.get("enabled", True))})
                return self._send_json(store.find_exam(exam_id))
            if action == "assign" and method == "POST":
                tickets = body.get("tickets") or body.get("candidate_tickets") or []
                tickets = [str(t) for t in tickets]
                exam["candidate_tickets"] = list(dict.fromkeys(tickets))
                store.save_exams()
                for t in tickets:
                    c = store.find_candidate(t)
                    if c:
                        c["exam_id"] = exam_id
                store.save_candidates()
                return self._send_json(exam)
            if action == "unassign" and method == "POST":
                old_tickets = list(exam.get("candidate_tickets") or [])
                exam["candidate_tickets"] = []
                store.save_exams()
                for t in old_tickets:
                    c = store.find_candidate(t)
                    if c and c.get("exam_id") == exam_id:
                        c["exam_id"] = None
                store.save_candidates()
                return self._send_json(exam)
            if action == "subjects" and method == "GET":
                return self._send_json(store.get_exam_subjects(exam))
            if action == "subjects" and method == "POST":
                body.setdefault("id", new_id("subj"))
                body["name"] = body.get("name") or f"科目{len(exam.get('subjects') or []) + 1}"
                if "duration_minutes" in body:
                    body["duration"] = int(body["duration_minutes"] or 10) * 60
                else:
                    body.setdefault("duration", 600)
                if "guide_duration_minutes" in body:
                    body["guide_duration"] = int(body["guide_duration_minutes"] or 10) * 60
                else:
                    body.setdefault("guide_duration", 600)
                body.setdefault("question_ids", body.get("question_ids") or [])
                body.setdefault("allow_early_submit", False)
                exam.setdefault("subjects", []).append(body)
                store.save_exams()
                return self._send_json(body, 201)
            if action.startswith("subjects/"):
                sid = action[len("subjects/"):].strip("/")
                subjects = store.get_exam_subjects(exam)
                target = next((s for s in subjects if s.get("id") == sid), None)
                if not target:
                    return self._send_json({"status": "error", "message": "科目不存在"}, 404)
                if method in ("PUT", "PATCH"):
                    for k in ("name", "duration", "duration_minutes", "guide_duration", "question_ids", "allow_early_submit"):
                        if k in body:
                            target[k] = body[k]
                    store.save_exams()
                    return self._send_json(target)
                if method == "DELETE":
                    exam["subjects"] = [s for s in exam.get("subjects", []) if s.get("id") != sid]
                    store.save_exams()
                    return self._send_json({"deleted": True})
            if method in ("PUT", "PATCH"):
                e = store.update_exam(exam_id, body)
                return self._send_json(e or {"status": "error", "message": "not found"}, 200 if e else 404)
            if method == "DELETE":
                ok = store.delete_exam(exam_id)
                return self._send_json({"deleted": ok}, 200 if ok else 404)
        # sessions
        if path in ("/api/sessions", "/api/sessions/") and method == "GET":
            sessions = store.list_sessions()
            return self._send_json(sessions)
        if path.startswith("/api/sessions/") and method == "GET":
            ticket = path[len("/api/sessions/"):].strip("/")
            sessions = [s for s in store.list_sessions() if s.get("ticket_no") == ticket]
            return self._send_json(sessions)
        if path.startswith("/api/sessions/") and method == "POST":
            # 手动启动某考生考试
            rest = path[len("/api/sessions/"):].strip("/")
            # path: /api/sessions/start  body ticket/exam
            if rest == "start":
                try:
                    sess = store.start_session(str(body.get("ticket") or body.get("ticket_no")), str(body.get("exam") or body.get("exam_id")))
                    return self._send_json(sess, 201)
                except ValueError as e:
                    return self._send_json({"status": "error", "message": str(e)}, 400)
            if rest == "stop":
                sess = store.end_session(str(body.get("ticket") or body.get("ticket_no")), str(body.get("exam") or body.get("exam_id")))
                return self._send_json(sess or {"status": "error", "message": "not found"}, 200 if sess else 404)
        # 考试机新版客户端接口
        if path in ("/api/exam-client/login", "/api/exam-client/login/") and method == "POST":
            return self._send_json(self.service.client_login(str(body.get("ticket") or body.get("permit") or "")))
        if path in ("/api/exam-client/state", "/api/exam-client/state/") and method == "GET":
            return self._send_json(self.service.client_state(query.get("ticket") or query.get("permit") or "", query.get("exam") or query.get("exam_id")))
        if path in ("/api/exam-client/answer", "/api/exam-client/answer/") and method == "POST":
            return self._send_json(self.service.client_submit_answer(
                str(body.get("ticket") or body.get("permit") or ""),
                body.get("answers", {}) or {},
                body.get("subject_id")))
        if path in ("/api/exam-client/early-submit", "/api/exam-client/early-submit/") and method == "POST":
            return self._send_json(self.service.client_early_submit(str(body.get("ticket") or body.get("permit") or "")))
        if path in ("/api/exam-client/guide-complete", "/api/exam-client/guide-complete/") and method == "POST":
            return self._send_json(self.service.client_complete_guide(str(body.get("ticket") or body.get("permit") or "")))
        # data export/import
        if path in ("/api/data/export", "/api/data/export/") and method == "GET":
            data = {
                "candidates": store.candidates,
                "questions": store.questions,
                "exams": store.exams,
                "sessions": store.sessions,
                "events": store.events,
                "settings": store.settings,
                "exported_at": now_iso(),
            }
            self._send_bytes(json_dumps(data, pretty=True), "application/json; charset=utf-8",
                            200)
            return
        if path in ("/api/data/import", "/api/data/import/") and method == "POST":
            data = body
            if isinstance(data.get("candidates"), list):
                store.candidates = data["candidates"]
                store.save_candidates()
            if isinstance(data.get("questions"), list):
                store.questions = data["questions"]
                store.save_questions()
            if isinstance(data.get("exams"), list):
                store.exams = data["exams"]
                store.save_exams()
            if isinstance(data.get("sessions"), dict):
                store.sessions = data["sessions"]
                store.save_sessions()
            if isinstance(data.get("events"), list):
                store.events = data["events"]
                store.save_events()
            if isinstance(data.get("settings"), dict):
                store.settings = data["settings"]
                store.save_settings()
            return self._send_json({"status": "ok"})
        self._send_json({"status": "error", "message": "API not found"}, 404)

    # ---- seat/exam machine API ----
    def _handle_seat(self, method: str, path: str, query: dict, body: dict):
        service = self.service
        store = self.store
        # 服务器时间
        if path == "/ts-api/v1/time" and method == "POST":
            return self._send_json({"timestamp": now_ts()})
        if path == "/seat/session" or path == "/seat/session/":
            if method == "GET":
                return self._send_json(service.get_session_payload())
        if path == "/seat/login" or path == "/seat/login/":
            if method == "POST":
                return self._send_json(service.login(body))
        if path == "/seat/confirm" or path == "/seat/confirm/":
            if method == "POST":
                return self._send_json(service.confirm(body))
        if path == "/seat/notice" or path == "/seat/notice/":
            if method == "GET":
                return self._send_json(service.get_notice())
        if path == "/seat/form" or path == "/seat/form/":
            if method == "GET":
                return self._send_json(service.get_form())
        if path == "/seat/response" or path == "/seat/response/":
            if method == "GET":
                return self._send_json(service.get_response(query.get("permit"), query.get("exam") or query.get("exam_id")))
        if path == "/seat/response/patch" or path == "/seat/response/patch/":
            if method == "POST":
                return self._send_json(service.patch_response(body))
        if path == "/seat/event" or path == "/seat/event/":
            if method == "POST":
                events = body if isinstance(body, list) else [body]
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    ticket = str(ev.get("permit") or ev.get("ticket_no") or "")
                    exam = service._resolve_exam(ticket)
                    if ticket and exam:
                        store.add_event(ticket, exam["id"], ev)
                return self._send_json({})
        if path.startswith("/seat/state/"):
            item_id = path[len("/seat/state/"):].strip("/")
            if method == "GET":
                return self._send_json(service.get_state(item_id, query))
            if method == "POST":
                return self._send_json(service.save_state(item_id, body))
        if path == "/seat/end" or path == "/seat/end/":
            if method == "POST":
                return self._send_json(service.end(body))
        if path == "/seat/score" or path == "/seat/score/":
            if method == "GET":
                return self._send_json(service.get_score(query.get("permit"), query.get("exam") or query.get("exam_id")))
        if path == "/seat/sessions" or path == "/seat/sessions/":
            if method == "GET":
                code = query.get("code", "")
                # 考试机查询可用的考试列表
                return self._send_json(store.exams)
        if path == "/seat/exam" or path == "/seat/exam/":
            if method == "POST":
                # 考试机预登录/选考接口：接受 session+permit，并直接尝试登录
                return self._send_json(service.login(body))
        # 考试机辅助接口（本地模拟/扩展）
        if path in ("/seat/status", "/seat/status/") and method == "GET":
            return self._send_json({"status": "ok", "online": True})
        if path in ("/seat/register", "/seat/register/", "/seat/unregister", "/seat/unregister/"):
            return self._send_json({})
        if path in ("/seat/transfer", "/seat/transfer/", "/seat/transfer/cancel", "/seat/transfer/cancel/"):
            return self._send_json({})
        if path in ("/seat/system/processes", "/seat/system/processes/",
                    "/seat/system/stats", "/seat/system/stats/"):
            return self._send_json({})
        if path.startswith("/seat/osstoken"):
            return self._send_json({"token": "", "bucket": "", "endpoint": ""})
        if path.startswith("/seat/rfile"):
            return self._send_json({})
        if path.startswith("/seat/encrypt"):
            return self._send_text(body.get("text", "") if isinstance(body, dict) else "")
        # 兼容前端直接 POST 完整 response 到 /seat/response/
        if path in ("/seat/response", "/seat/response/") and method == "POST":
            return self._send_json(service.patch_response(body))
        # 静态资源
        if path.startswith("/seat/css/"):
            css_id = path[len("/seat/css/"):].strip("/")
            css_file = MIRROR_DIR / "seat" / "css" / f"{css_id}.json"
            if css_file.exists():
                self._send_bytes(css_file.read_bytes(), "application/json; charset=utf-8")
            else:
                # 返回默认皮肤配置
                self._send_json({
                    "basic": "",
                    "customjs": "",
                    "exam_login": "",
                    "global": "",
                    "index_html": "",
                    "md5": "00000000000000000000000000000000",
                    "name": "default",
                    "notice": "",
                })
            return
        if path.startswith("/seat/skin/"):
            rel = path[len("/seat/skin/"):]
            rel = rel.replace(":", "_")
            target = MIRROR_DIR / "seat" / "skin" / rel
            self._serve_file(target)
            return
        if path.startswith("/seat/resource/"):
            rel = path[len("/seat/resource/"):]
            target = MIRROR_DIR / "seat" / "resource" / rel
            if not target.exists():
                target = MIRROR_DIR / rel
            self._serve_file(target)
            return
        if path in ("/seat/photo", "/seat/photo/"):
            # 照片接口：演示环境返回空，真实可扩展为保存到 data/photos
            return self._send_json({})
        self._send_json({"status": "error", "message": "seat API not found"}, 404)

    # ---- static ----
    def _serve_static(self, path: str):
        rel = path.lstrip("/")
        # 由镜像前端引用的 /client/... 直接映射到 mirror/client/...
        if rel.startswith("client/"):
            target = MIRROR_DIR / rel
            self._serve_file(target)
            return
        if rel.startswith("seat/skin/"):
            rel = rel.replace(":", "_")
            target = MIRROR_DIR / rel
            self._serve_file(target)
            return
        target = MIRROR_DIR / rel
        if target.is_file():
            self._serve_file(target)
            return
        # SPA fallback for front-end routes
        if not Path(rel).suffix:
            self._serve_file(MIRROR_DIR / "index.html")
            return
        self._send_text("Not Found", 404)

    def _serve_file(self, path: Path):
        if not path.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self._send_bytes(data, ctype or "application/octet-stream")


def run_server(port: int = DEFAULT_PORT, data_dir: str | Path | None = None, host: str = "127.0.0.1"):
    store = DataStore(data_dir)
    service = ExamService(store)
    handler = type("BoundHandler", (ExamHTTPHandler,), {"store": store, "service": service})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"本地考试后端已启动: http://{host}:{port}/")
    print(f"管理后台: http://{host}:{port}/admin")
    print(f"数据目录: {store.data_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_list(items, fields):
    if not items:
        print("(空)")
        return
    for item in items:
        print(" | ".join(f"{k}={item.get(k)}" for k in fields))


def _parse_options(option_args):
    """解析 --option 'A=描述' 或 --option 'A,描述'"""
    options = []
    for s in option_args or []:
        if "=" in s:
            oid, desc = s.split("=", 1)
        else:
            parts = s.split(",", 1)
            oid, desc = (parts[0], parts[1] if len(parts) > 1 else "")
        options.append({"id": oid.strip(), "description": desc.strip()})
    return options


def cmd_candidate(args, store: DataStore):
    if args.candidate_action == "list":
        _print_list(store.candidates, ["ticket_no", "name", "gender", "exam_id", "seat_no", "status"])
    elif args.candidate_action == "add":
        try:
            c = store.add_candidate({
                "ticket_no": args.ticket,
                "name": args.name,
                "gender": args.gender,
                "id_card": args.id_card,
                "exam_id": args.exam,
                "seat_no": args.seat,
                "remark": args.remark,
            })
        except ValueError as e:
            print("错误:", e)
            return
        print("已添加:", c["ticket_no"], c["name"])
    elif args.candidate_action == "update":
        data = {}
        if args.name:
            data["name"] = args.name
        if args.gender:
            data["gender"] = args.gender
        if args.id_card:
            data["id_card"] = args.id_card
        if args.exam:
            data["exam_id"] = args.exam
        if args.seat:
            data["seat_no"] = args.seat
        if args.status:
            data["status"] = args.status
        if args.remark:
            data["remark"] = args.remark
        c = store.update_candidate(args.ticket, data)
        print("已更新:", c or "未找到")
    elif args.candidate_action == "remove":
        ok = store.delete_candidate(args.ticket)
        print("已删除" if ok else "未找到")
    else:
        print("未知操作")


def cmd_question(args, store: DataStore):
    if args.question_action == "list":
        _print_list(store.questions, ["id", "type", "stem", "answer", "score", "subject", "group"])
    elif args.question_action == "add":
        options = _parse_options(args.option)
        try:
            q = store.add_question({
                "id": args.id,
                "type": args.type,
                "stem": args.stem,
                "options": options,
                "answer": args.answer,
                "score": args.score,
                "subject": args.subject,
                "section": args.section or args.subject,
                "group": args.group,
                "explanation": args.explanation,
            })
        except ValueError as e:
            print("错误:", e)
            return
        print("已添加试题:", q["id"])
    elif args.question_action == "update":
        data = {}
        if args.stem is not None:
            data["stem"] = args.stem
        if args.answer is not None:
            data["answer"] = args.answer
        if args.score is not None:
            data["score"] = args.score
        if args.group is not None:
            data["group"] = args.group
        q = store.update_question(args.id, data)
        print("已更新:", q or "未找到")
    elif args.question_action == "remove":
        ok = store.delete_question(args.id)
        print("已删除" if ok else "未找到")
    else:
        print("未知操作")


def cmd_exam(args, store: DataStore):
    if args.exam_action == "list":
        _print_list(store.exams, ["id", "name", "stage", "status", "mode", "auto_start", "duration_minutes", "candidate_tickets"])
    elif args.exam_action == "create":
        sections = []
        if args.questions:
            qids = [x.strip() for x in args.questions.split(",") if x.strip()]
            sections = [{
                "name": "科目一",
                "section_type": "exam",
                "groups": [{"name": "试题", "question_ids": qids,
                             "point": sum(float(store.find_question(q).get("score", 1) or 1) for q in qids if store.find_question(q))}],
                "point": sum(float(store.find_question(q).get("score", 1) or 1) for q in qids if store.find_question(q)),
            }]
        try:
            e = store.add_exam({
                "name": args.name,
                "title": args.title or args.name,
                "duration_minutes": args.duration,
                "mode": args.mode,
                "auto_start": True if args.auto else False,
                "start_time": args.start_time,
                "end_time": args.end_time,
                "sections": sections,
            })
        except ValueError as e:
            print("错误:", e)
            return
        print("已创建考试:", e["id"])
        if args.assign:
            tickets = [x.strip() for x in args.assign.split(",") if x.strip()]
            e["candidate_tickets"] = list(dict.fromkeys(tickets))
            store.save_exams()
            for t in tickets:
                c = store.find_candidate(t)
                if c:
                    c["exam_id"] = e["id"]
            store.save_candidates()
            print("已分配考生:", len(tickets))
    elif args.exam_action == "update":
        data = {}
        if args.name is not None:
            data["name"] = args.name
        if args.duration is not None:
            data["duration_minutes"] = args.duration
        if args.mode is not None:
            data["mode"] = args.mode
        if args.auto is not None:
            data["auto_start"] = args.auto
        if args.no_auto:
            data["auto_start"] = False
        if args.start_time is not None:
            data["start_time"] = args.start_time
        if args.end_time is not None:
            data["end_time"] = args.end_time
        e = store.update_exam(args.id, data)
        print("已更新:", e or "未找到")
    elif args.exam_action == "remove":
        ok = store.delete_exam(args.id)
        print("已删除" if ok else "未找到")
    elif args.exam_action == "prepare":
        e = store.prepare_exam(args.id)
        print("已准备:", e["id"] if e else "未找到")
    elif args.exam_action == "start":
        try:
            e = store.start_exam(args.id)
            print("已开始:", e["id"] if e else "未找到")
        except ValueError as ex:
            print("错误:", ex)
    elif args.exam_action == "stop":
        e = store.end_exam(args.id)
        print("已结束:", e["id"] if e else "未找到")
    elif args.exam_action == "auto":
        enabled = False if args.off else bool(args.on)
        e = store.update_exam(args.id, {"auto_start": enabled})
        print(f"自动开考 {'开启' if enabled else '关闭'}:", e["id"] if e else "未找到")
    elif args.exam_action == "assign":
        e = store.find_exam(args.id)
        if not e:
            print("未找到考试")
            return
        tickets = [x.strip() for x in args.tickets.split(",") if x.strip()]
        e["candidate_tickets"] = list(dict.fromkeys(tickets))
        store.save_exams()
        for t in tickets:
            c = store.find_candidate(t)
            if c:
                c["exam_id"] = e["id"]
        store.save_candidates()
        print("已分配:", len(tickets))
    elif args.exam_action == "unassign":
        e = store.find_exam(args.id)
        if e:
            old = list(e.get("candidate_tickets") or [])
            e["candidate_tickets"] = []
            store.save_exams()
            for t in old:
                c = store.find_candidate(t)
                if c and c.get("exam_id") == e["id"]:
                    c["exam_id"] = None
            store.save_candidates()
        print("已清空考生分配")
    else:
        print("未知操作")


def cmd_session(args, store: DataStore):
    if args.session_action == "list":
        _print_list(store.list_sessions(), ["key", "ticket_no", "exam_id", "candidate_name", "started_at", "ended_at", "score"])
    elif args.session_action == "start":
        try:
            s = store.start_session(args.ticket, args.exam)
            print("已启动:", s["key"])
        except ValueError as e:
            print("错误:", e)
    elif args.session_action == "stop":
        s = store.end_session(args.ticket, args.exam)
        print("已结束" if s else "未找到")
    elif args.session_action == "score":
        service = ExamService(store)
        print(service.get_score(args.ticket, args.exam or None))
    else:
        print("未知操作")


def cmd_settings(args, store: DataStore):
    if args.settings_action == "get":
        print(store.settings)
    elif args.settings_action == "set":
        try:
            print(store.set_mode(args.mode))
        except ValueError as e:
            print("错误:", e)


def cmd_data(args, store: DataStore):
    if args.data_action == "export":
        out = Path(args.output)
        data = {
            "candidates": store.candidates,
            "questions": store.questions,
            "exams": store.exams,
            "sessions": store.sessions,
            "events": store.events,
            "settings": store.settings,
        }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("导出完成:", out)
    elif args.data_action == "import":
        src = Path(args.input)
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:
            print("导入失败:", e)
            return
        if "candidates" in data:
            store.candidates = data["candidates"]
            store.save_candidates()
        if "questions" in data:
            store.questions = data["questions"]
            store.save_questions()
        if "exams" in data:
            store.exams = data["exams"]
            store.save_exams()
        if "sessions" in data:
            store.sessions = data["sessions"]
            store.save_sessions()
        if "events" in data:
            store.events = data["events"]
            store.save_events()
        if "settings" in data:
            store.settings = data["settings"]
            store.save_settings()
        print("导入完成:", src)
    elif args.data_action == "paths":
        print("数据目录:", store.data_dir)
        for p in (store.candidates_path, store.questions_path, store.exams_path,
                  store.sessions_path, store.events_path, store.settings_path):
            print(p)
    else:
        print("未知操作")


def cmd_seed_demo(args, store: DataStore):
    """从镜像 form.json 导入示例题目（不含正确答案，仅供界面预览）。"""
    form_file = MIRROR_DIR / "api" / "form.json"
    if not form_file.exists():
        print("未找到 mirror/api/form.json")
        return
    data = json.loads(form_file.read_text(encoding="utf-8"))
    form = data.get("form", {})
    added = 0
    for sec in form.get("sections", []):
        for grp in sec.get("groups", []):
            for item in grp.get("items", []):
                qid = item.get("id")
                if store.find_question(qid):
                    continue
                content = item.get("content", {})
                store.add_question({
                    "id": qid,
                    "type": item.get("type", "sc"),
                    "stem": content.get("stem", ""),
                    "options": content.get("options", []),
                    "answer": [],
                    "score": item.get("point", 1),
                    "section": sec.get("name", ""),
                    "group": grp.get("name", ""),
                    "enabled": True,
                })
                added += 1
    if not store.exams:
        single_qids = [q["id"] for q in store.questions if q.get("group") == "单选题"][:5]
        multi_qids = [q["id"] for q in store.questions if q.get("group") == "多选题"][:5]
        store.add_exam({
            "name": "示例考试",
            "title": "本地示例考试",
            "duration_minutes": 10,
            "mode": "unified",
            "auto_start": True,
            "stage": "created",
            "status": "draft",
            "subjects": [
                {
                    "id": "s1",
                    "name": "科目一",
                    "duration": 5 * 60,
                    "guide_duration": 10 * 60,
                    "question_ids": single_qids,
                    "allow_early_submit": False,
                },
                {
                    "id": "s2",
                    "name": "科目二",
                    "duration": 5 * 60,
                    "guide_duration": 10 * 60,
                    "question_ids": multi_qids,
                    "allow_early_submit": True,
                },
            ],
        })
    if not store.candidates:
        store.add_candidate({"ticket_no": "1234CS", "name": "演示考生", "gender": "男", "id_card": "", "exam_id": store.exams[0]["id"] if store.exams else None})
    print(f"导入示例完成，新增试题 {added} 条")


def build_arg_parser():
    parser = argparse.ArgumentParser(description="本地考试后端管理系统")
    sub = parser.add_subparsers(dest="command")

    p_server = sub.add_parser("server", help="启动 HTTP 服务")
    p_server.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_server.add_argument("port_pos", nargs="?", type=int, default=None,
                          help="可选：端口号（与 --port 等效）")
    p_server.add_argument("--host", default="127.0.0.1")
    p_server.add_argument("--data", default=None)

    p_cand = sub.add_parser("candidate", help="考生管理")
    p_cand.add_argument("candidate_action", choices=["list", "add", "update", "remove"])
    p_cand.add_argument("--ticket", default="")
    p_cand.add_argument("--name", default="")
    p_cand.add_argument("--gender", default="")
    p_cand.add_argument("--id-card", dest="id_card", default="")
    p_cand.add_argument("--exam", default="")
    p_cand.add_argument("--seat", default="")
    p_cand.add_argument("--status", default="")
    p_cand.add_argument("--remark", default="")
    p_cand.add_argument("--data", default=None)

    p_q = sub.add_parser("question", help="试题管理")
    p_q.add_argument("question_action", choices=["list", "add", "update", "remove"])
    p_q.add_argument("--id", default="")
    p_q.add_argument("--type", default="sc")
    p_q.add_argument("--stem", default="")
    p_q.add_argument("--option", action="append", dest="option", default=[],
                     help="选项，格式 'A=描述'，可重复")
    p_q.add_argument("--answer", default="")
    p_q.add_argument("--score", type=float, default=1.0)
    p_q.add_argument("--subject", default="")
    p_q.add_argument("--section", default="")
    p_q.add_argument("--group", default="")
    p_q.add_argument("--explanation", default="")
    p_q.add_argument("--data", default=None)

    p_exam = sub.add_parser("exam", help="考试管理")
    p_exam.add_argument("exam_action", choices=["list", "create", "update", "remove",
                                                "prepare", "start", "stop", "auto", "assign", "unassign"])
    p_exam.add_argument("--id", default="")
    p_exam.add_argument("--name", default="")
    p_exam.add_argument("--title", default="")
    p_exam.add_argument("--duration", type=int, default=None)
    p_exam.add_argument("--mode", default=None, choices=["unified", "individual"])
    p_exam.add_argument("--auto", action="store_true", default=None)
    p_exam.add_argument("--no-auto", dest="no_auto", action="store_true", default=False)
    p_exam.add_argument("--off", action="store_true", default=False)
    p_exam.add_argument("--questions", default="")
    p_exam.add_argument("--assign", default="")
    p_exam.add_argument("--start-time", dest="start_time", default=None, help="可选开始时间（时间戳或字符串）")
    p_exam.add_argument("--end-time", dest="end_time", default=None, help="可选结束时间（时间戳或字符串）")
    p_exam.add_argument("--tickets", default="")
    p_exam.add_argument("--on", action="store_true", default=False)
    p_exam.add_argument("--data", default=None)

    p_sess = sub.add_parser("session", help="考试记录/单独开考管理")
    p_sess.add_argument("session_action", choices=["list", "start", "stop", "score"])
    p_sess.add_argument("--ticket", default="")
    p_sess.add_argument("--exam", default="")
    p_sess.add_argument("--data", default=None)

    p_settings = sub.add_parser("settings", help="系统模式设置")
    p_settings.add_argument("settings_action", choices=["get", "set"])
    p_settings.add_argument("--mode", choices=["simulation", "standard"], default="simulation")
    p_settings.add_argument("--data", default=None)

    p_data = sub.add_parser("data", help="数据管理")
    p_data.add_argument("data_action", choices=["export", "import", "paths"])
    p_data.add_argument("--output", default="exam_data_export.json")
    p_data.add_argument("--input", default="exam_data_export.json")
    p_data.add_argument("--data", default=None)

    p_seed = sub.add_parser("seed-demo", help="从前端镜像导入示例试题")
    p_seed.add_argument("--data", default=None)

    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return
    store = DataStore(args.data)
    if args.command == "server":
        port = args.port_pos if args.port_pos is not None else args.port
        run_server(port, args.data, args.host)
    elif args.command == "candidate":
        cmd_candidate(args, store)
    elif args.command == "question":
        cmd_question(args, store)
    elif args.command == "exam":
        cmd_exam(args, store)
    elif args.command == "session":
        cmd_session(args, store)
    elif args.command == "settings":
        cmd_settings(args, store)
    elif args.command == "data":
        cmd_data(args, store)
    elif args.command == "seed-demo":
        cmd_seed_demo(args, store)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

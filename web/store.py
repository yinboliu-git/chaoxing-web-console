# -*- coding: utf-8 -*-
"""SQLite 持久化：任务历史与章节明细。

只存任务元数据与结果，不存账号密码。数据库位于 ``~/.chaoxing/web.db``。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from api.config_store import app_home

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    courses TEXT NOT NULL DEFAULT '[]',
    options TEXT NOT NULL DEFAULT '{}',
    summary TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    course TEXT,
    title TEXT,
    status TEXT,
    tries INTEGER DEFAULT 0,
    UNIQUE(job_id, course, title)
);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id);
"""


def db_path():
    """数据库文件路径。"""
    return app_home() / "web.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """打开一个连接并保证提交/关闭。"""
    conn = sqlite3.connect(str(db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表，并清理上次进程残留的 running 状态。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "UPDATE jobs SET status='interrupted', finished_at=? WHERE status IN ('running','queued')",
            (_now(),),
        )


def _now() -> str:
    """当前时间（本地时区 ISO 字符串）。"""
    return datetime.now().isoformat(timespec="seconds")


def create_job(courses: list[dict[str, Any]], options: dict[str, Any]) -> int:
    """创建任务记录并返回 id。"""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jobs(status, created_at, courses, options) VALUES (?,?,?,?)",
            ("queued", _now(), json.dumps(courses, ensure_ascii=False), json.dumps(options, ensure_ascii=False)),
        )
        return int(cursor.lastrowid)


def update_job(job_id: int, **fields: Any) -> None:
    """更新任务字段（status/started_at/finished_at/summary/error）。"""
    if not fields:
        return
    columns = ", ".join(f"{name}=?" for name in fields)
    values = list(fields.values())
    values.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id=?", values)


def upsert_item(job_id: int, course: str, title: str, status: str, tries: int = 0) -> None:
    """写入/更新章节状态。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO job_items(job_id, course, title, status, tries) VALUES (?,?,?,?,?) "
            "ON CONFLICT(job_id, course, title) DO UPDATE SET status=excluded.status, tries=excluded.tries",
            (job_id, course, title, status, tries),
        )


def get_job(job_id: int) -> Optional[dict[str, Any]]:
    """读取单个任务。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_row(row) if row else None


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    """按时间倒序列出任务。"""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_job_row(row) for row in rows]


def get_items(job_id: int) -> list[dict[str, Any]]:
    """读取任务的章节明细。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT course, title, status, tries FROM job_items WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def delete_job(job_id: int) -> None:
    """删除任务及其明细。"""
    with _connect() as conn:
        conn.execute("DELETE FROM job_items WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    """把数据库行转成字典并反序列化 JSON 字段。"""
    item = dict(row)
    for key in ("courses", "options", "summary"):
        if item.get(key):
            try:
                item[key] = json.loads(item[key])
            except json.JSONDecodeError:
                item[key] = None
    return item

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def data_dir() -> Path:
    # 每次调用读环境变量，测试可用 monkeypatch 隔离库路径
    return Path(os.environ.get("DATA_DIR", str(_DEFAULT_DATA_DIR)))


def fixtures_dir() -> Path:
    return Path(os.environ.get("FIXTURES_DIR", str(_DEFAULT_FIXTURES_DIR)))


def db_path() -> Path:
    return data_dir() / "app.db"


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]

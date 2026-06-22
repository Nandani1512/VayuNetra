"""SQLite consent + preference store for the Telegram bot.

Stored fields are intentionally minimal (chat_id, language, city,
location-precision, opt-in flag) to honour the consent flow described in
plan §8.4. No PII beyond chat_id and a coarse location is persisted.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_DB = Path(__file__).resolve().parent / "users.sqlite"


@dataclass(frozen=True)
class UserPrefs:
    chat_id: int
    lang: str
    city: str
    precision: str  # 'ward' | 'pincode' | 'exact'
    pincode: str | None
    lat: float | None
    lon: float | None
    vuln_tier: str
    opted_in: bool


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id   INTEGER PRIMARY KEY,
    lang      TEXT NOT NULL DEFAULT 'en',
    city      TEXT NOT NULL DEFAULT 'delhi',
    precision TEXT NOT NULL DEFAULT 'pincode',
    pincode   TEXT,
    lat       REAL,
    lon       REAL,
    vuln_tier TEXT NOT NULL DEFAULT 'general',
    opted_in  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(chat_id: int, **fields: object) -> None:
    if not fields:
        with session() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (chat_id) VALUES (?)",
                (chat_id,),
            )
        return
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    sets = ", ".join(f"{k}=excluded.{k}" for k in fields.keys())
    sql = (
        f"INSERT INTO users (chat_id, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(chat_id) DO UPDATE SET {sets}, updated_at=datetime('now')"
    )
    with session() as c:
        c.execute(sql, (chat_id, *fields.values()))


def get(chat_id: int) -> UserPrefs | None:
    with session() as c:
        row = c.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None:
        return None
    return UserPrefs(
        chat_id=row["chat_id"],
        lang=row["lang"],
        city=row["city"],
        precision=row["precision"],
        pincode=row["pincode"],
        lat=row["lat"],
        lon=row["lon"],
        vuln_tier=row["vuln_tier"],
        opted_in=bool(row["opted_in"]),
    )


def opted_in() -> list[UserPrefs]:
    with session() as c:
        rows = c.execute("SELECT * FROM users WHERE opted_in=1").fetchall()
    return [
        UserPrefs(
            chat_id=r["chat_id"],
            lang=r["lang"],
            city=r["city"],
            precision=r["precision"],
            pincode=r["pincode"],
            lat=r["lat"],
            lon=r["lon"],
            vuln_tier=r["vuln_tier"],
            opted_in=True,
        )
        for r in rows
    ]

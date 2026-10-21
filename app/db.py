"""Banco SQLite simples (biblioteca padrão do Python)."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    ig_user_id TEXT,
    username TEXT,
    media_id TEXT,
    text TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT UNIQUE,
    ig_user_id TEXT,
    username TEXT,
    comment_text TEXT,
    rule_name TEXT,
    public_reply TEXT,
    dm_text TEXT,
    status TEXT,           -- pending | sent | dry_run | rejected | failed
    reply_done INTEGER DEFAULT 0,
    dm_done INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    ig_user_id TEXT PRIMARY KEY,
    state TEXT,            -- dm_sent | link_sent | opted_out
    comment_id TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS media (
    media_id TEXT PRIMARY KEY,
    caption TEXT,
    media_type TEXT,
    product_type TEXT,
    permalink TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    level TEXT,
    message TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    path = get_settings().db_path
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def log(level: str, message: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO logs (ts, level, message) VALUES (?, ?, ?)", (now(), level, message))


def recent_logs(limit: int = 50, offset: int = 0):
    with connect() as c:
        return c.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()


def count_logs() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM logs").fetchone()["n"]


def register_comment(comment_id, ig_user_id, username, media_id, text) -> bool:
    """Retorna True se o comentário é novo (evita processar o mesmo evento duas vezes)."""
    with connect() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO comments VALUES (?, ?, ?, ?, ?, ?)",
            (comment_id, ig_user_id, username, media_id, text, now()),
        )
        return cur.rowcount == 1


def add_queue_item(comment_id, ig_user_id, username, comment_text, rule_name, public_reply, dm_text) -> int:
    with connect() as c:
        cur = c.execute(
            """INSERT INTO queue (comment_id, ig_user_id, username, comment_text, rule_name,
               public_reply, dm_text, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (comment_id, ig_user_id, username, comment_text, rule_name,
             public_reply, dm_text, now(), now()),
        )
        return cur.lastrowid


def get_item(item_id: int):
    with connect() as c:
        return c.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()


def list_items(statuses, limit: int = 100, offset: int = 0):
    marks = ",".join("?" for _ in statuses)
    with connect() as c:
        return c.execute(
            f"SELECT * FROM queue WHERE status IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",
            (*statuses, limit, offset),
        ).fetchall()


def count_items(statuses) -> int:
    marks = ",".join("?" for _ in statuses)
    with connect() as c:
        return c.execute(
            f"SELECT COUNT(*) AS n FROM queue WHERE status IN ({marks})", tuple(statuses)
        ).fetchone()["n"]


def update_item(item_id: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE queue SET {cols} WHERE id = ?", (*fields.values(), item_id))


def count_sent_last_hour() -> int:
    with connect() as c:
        row = c.execute(
            """SELECT COUNT(*) AS n FROM queue
               WHERE status IN ('sent', 'dry_run')
               AND updated_at >= datetime('now', '-1 hour')"""
        ).fetchone()
        return row["n"]


def dm_sent_recently(ig_user_id: str, days: int, exclude_item: int = 0) -> bool:
    """A pessoa já recebeu uma DM inicial nossa nos últimos `days` dias?
    (DM pulada fica com dm_text vazio e não conta.)"""
    if days <= 0:
        return False
    with connect() as c:
        row = c.execute(
            """SELECT 1 FROM queue
               WHERE ig_user_id = ? AND dm_done = 1 AND dm_text <> '' AND id <> ?
               AND updated_at >= datetime('now', ?) LIMIT 1""",
            (ig_user_id, exclude_item, f"-{int(days)} days"),
        ).fetchone()
        return row is not None


def get_media(media_id: str):
    with connect() as c:
        return c.execute("SELECT * FROM media WHERE media_id = ?", (media_id,)).fetchone()


def save_media(media_id: str, caption, media_type, product_type, permalink) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO media VALUES (?, ?, ?, ?, ?, ?)",
            (media_id, caption or "", media_type or "", product_type or "", permalink or "", now()),
        )


def post_snippets(comment_ids) -> dict:
    """comment_id -> legenda do post comentado (para mostrar no painel)."""
    ids = list(comment_ids)
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with connect() as c:
        rows = c.execute(
            f"""SELECT c.comment_id, m.caption FROM comments c
                JOIN media m ON m.media_id = c.media_id
                WHERE c.comment_id IN ({marks}) AND m.caption <> ''""",
            tuple(ids),
        ).fetchall()
    return {r["comment_id"]: r["caption"] for r in rows}


def get_contact(ig_user_id: str):
    with connect() as c:
        return c.execute("SELECT * FROM contacts WHERE ig_user_id = ?", (ig_user_id,)).fetchone()


def set_contact(ig_user_id: str, state: str, comment_id: str = "") -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO contacts (ig_user_id, state, comment_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ig_user_id) DO UPDATE SET state = excluded.state,
               updated_at = excluded.updated_at""",
            (ig_user_id, state, comment_id, now()),
        )


def delete_user_data(identifier: str) -> dict:
    """Apaga tudo de uma pessoa (LGPD): comentários, fila, contato e linhas do registro que a citam.
    `identifier` pode ser o @usuario (com ou sem @) ou o ID numérico da conta."""
    ident = (identifier or "").strip().lstrip("@")
    if not ident:
        return {"comments": 0, "queue": 0, "contacts": 0, "logs": 0}
    with connect() as c:
        ids = {r["ig_user_id"] for r in c.execute(
            "SELECT ig_user_id FROM comments WHERE lower(username) = lower(?) OR ig_user_id = ?", (ident, ident))}
        ids |= {r["ig_user_id"] for r in c.execute(
            "SELECT ig_user_id FROM queue WHERE lower(username) = lower(?) OR ig_user_id = ?", (ident, ident))}
        ids.add(ident)
        marks = ",".join("?" for _ in ids)
        out = {}
        out["comments"] = c.execute(
            f"DELETE FROM comments WHERE ig_user_id IN ({marks}) OR lower(username) = lower(?)", (*ids, ident)).rowcount
        out["queue"] = c.execute(
            f"DELETE FROM queue WHERE ig_user_id IN ({marks}) OR lower(username) = lower(?)", (*ids, ident)).rowcount
        out["contacts"] = c.execute(f"DELETE FROM contacts WHERE ig_user_id IN ({marks})", tuple(ids)).rowcount
        out["logs"] = 0
        for needle in {ident.lower(), *(i.lower() for i in ids)}:
            out["logs"] += c.execute("DELETE FROM logs WHERE instr(lower(message), ?) > 0", (needle,)).rowcount
        return out

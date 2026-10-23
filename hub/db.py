import sqlite3
import threading
import uuid
import os
import re
from pathlib import Path

DB_PATH = Path(os.environ.get("AGENT_CONTROL_DB", Path(__file__).parent / "hub.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    token      TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents (
    app_id       TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL REFERENCES devices(device_id),
    agent_name   TEXT NOT NULL,
    display_name TEXT NOT NULL,
    engine_type  TEXT NOT NULL CHECK(engine_type IN ('cli','serve')),
    exec_path    TEXT NOT NULL,
    work_dir     TEXT,
    session_id   TEXT,
    app_secret   TEXT NOT NULL,
    enabled      INTEGER DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    app_id            TEXT NOT NULL REFERENCES agents(app_id),
    device_id         TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','running','done','failed','timeout')),
    result            TEXT,
    error             TEXT,
    reply_message_id  TEXT,
    reply_chat_id     TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    started_at        TEXT,
    finished_at       TEXT
);

CREATE TABLE IF NOT EXISTS processed_messages (
    app_id      TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    handled_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (app_id, message_id)
);
CREATE TABLE IF NOT EXISTS workspace_selections (
    app_id TEXT NOT NULL,
    sender_open_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY (app_id, sender_open_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    code TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    app_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    permission_id TEXT NOT NULL,
    sender_open_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    reply_message_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','expired')),
    expires_at INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (task_id, session_id, permission_id)
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "work_dir" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN work_dir TEXT")
            if "sender_open_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN sender_open_id TEXT")
        finally:
            conn.close()


def upsert_device(device_id, name, token):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO devices (device_id, name, token) VALUES (?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, token=excluded.token""",
                (device_id, name, token),
            )
        finally:
            conn.close()


def upsert_agent(app_id, device_id, agent_name, display_name, engine_type, exec_path,
                 app_secret, work_dir=None, session_id=None, enabled=1):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO agents (app_id, device_id, agent_name, display_name,
                                       engine_type, exec_path, work_dir, session_id,
                                       app_secret, enabled)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(app_id) DO UPDATE SET
                     device_id=excluded.device_id, agent_name=excluded.agent_name,
                     display_name=excluded.display_name, engine_type=excluded.engine_type,
                     exec_path=excluded.exec_path, work_dir=excluded.work_dir,
                     session_id=excluded.session_id, app_secret=excluded.app_secret,
                     enabled=excluded.enabled""",
                (app_id, device_id, agent_name, display_name, engine_type, exec_path,
                 work_dir, session_id, app_secret, enabled),
            )
        finally:
            conn.close()


def get_agent(app_id):
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM agents WHERE app_id=?", (app_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_agents():
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT * FROM agents").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def set_display_name(app_id, display_name):
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE agents SET display_name=? WHERE app_id=?",
                         (display_name, app_id))
        finally:
            conn.close()


def message_seen(app_id, message_id):
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE app_id=? AND message_id=?",
                (app_id, message_id)).fetchone()
            return row is not None
        finally:
            conn.close()


def mark_message_processed(app_id, message_id):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO processed_messages (app_id, message_id) VALUES (?,?)",
                (app_id, message_id))
        finally:
            conn.close()


def get_workspace_selection(app_id, sender_open_id):
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT alias FROM workspace_selections WHERE app_id=? AND sender_open_id=?",
                (app_id, sender_open_id),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


def set_workspace_selection(app_id, sender_open_id, alias):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO workspace_selections (app_id,sender_open_id,alias)
                   VALUES (?,?,?) ON CONFLICT(app_id,sender_open_id)
                   DO UPDATE SET alias=excluded.alias""",
                (app_id, sender_open_id, alias),
            )
        finally:
            conn.close()


def create_task(app_id, device_id, prompt, reply_message_id=None, reply_chat_id=None,
                work_dir=None, sender_open_id=None):
    task_id = "t_" + uuid.uuid4().hex[:12]
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO tasks (task_id, app_id, device_id, prompt,
                                      reply_message_id, reply_chat_id, work_dir,
                                      sender_open_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (task_id, app_id, device_id, prompt, reply_message_id, reply_chat_id,
                 work_dir, sender_open_id))
        finally:
            conn.close()
    return task_id


def create_approval(task_id, token, session_id, permission_id, permission, detail,
                    lifetime_seconds=300, safe_change_senders=None):
    code = uuid.uuid4().hex[:10]
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                """SELECT t.*, d.token AS device_token FROM tasks t
                   JOIN devices d ON d.device_id=t.device_id WHERE t.task_id=?""",
                (task_id,),
            ).fetchone()
            if (task is None or task["device_token"] != token or task["status"] != "running"
                    or not task["sender_open_id"] or not task["reply_chat_id"]):
                conn.execute("ROLLBACK")
                return None
            if permission == "safe_change":
                edit = re.fullmatch(r"/edit ([^:\r\n]+):\s*(.+)", task["prompt"].strip(), re.DOTALL)
                path = edit.group(1).strip() if edit else ""
                if (safe_change_senders is None
                        or task["sender_open_id"] not in safe_change_senders.get(task["app_id"], [])
                        or permission_id != "safe-edit-" + task_id
                        or not path or len(detail) > 1800
                        or not detail.startswith("--- a/" + path + "\n+++ b/" + path + "\n")):
                    conn.execute("ROLLBACK")
                    return None
            existing = conn.execute(
                "SELECT * FROM approvals WHERE task_id=? AND session_id=? AND permission_id=?",
                (task_id, session_id, permission_id),
            ).fetchone()
            if existing:
                conn.execute("COMMIT")
                return {**dict(existing), "created": False}
            conn.execute(
                """INSERT INTO approvals
                   (code,task_id,app_id,device_id,session_id,permission_id,
                    sender_open_id,chat_id,reply_message_id,permission,detail,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,strftime('%s','now')+?)""",
                (code, task_id, task["app_id"], task["device_id"], session_id,
                 permission_id, task["sender_open_id"], task["reply_chat_id"],
                 task["reply_message_id"], permission, detail[:2000], lifetime_seconds),
            )
            row = conn.execute("SELECT * FROM approvals WHERE code=?", (code,)).fetchone()
            conn.execute("COMMIT")
            return {**dict(row), "created": True}
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def decide_approval(app_id, code, sender_open_id, chat_id, decision):
    if decision not in ("approved", "rejected"):
        raise ValueError("invalid decision")
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM approvals WHERE code=? AND app_id=?", (code, app_id)).fetchone()
            if (row is None or row["sender_open_id"] != sender_open_id
                    or row["chat_id"] != chat_id or row["status"] != "pending"):
                conn.execute("ROLLBACK")
                return None
            if int(row["expires_at"]) <= int(conn.execute("SELECT strftime('%s','now')").fetchone()[0]):
                conn.execute("UPDATE approvals SET status='expired' WHERE code=?", (code,))
                conn.execute("COMMIT")
                return None
            conn.execute("UPDATE approvals SET status=? WHERE code=?", (decision, code))
            conn.execute("COMMIT")
            return decision
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def get_approval(code, token):
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT a.* FROM approvals a JOIN devices d ON d.device_id=a.device_id
                   WHERE a.code=? AND d.token=?""", (code, token)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def claim_task(device_id, token):
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            dev = conn.execute("SELECT * FROM devices WHERE device_id=?",
                               (device_id,)).fetchone()
            if dev is None or dev["token"] != token:
                conn.execute("ROLLBACK")
                return None, "invalid device or token"
            row = conn.execute(
                """SELECT * FROM tasks
                   WHERE device_id=? AND status='pending'
                   ORDER BY created_at, rowid LIMIT 1""",
                (device_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None, None
            conn.execute(
                "UPDATE tasks SET status='running', started_at=datetime('now') WHERE task_id=?",
                (row["task_id"],))
            agent = conn.execute("SELECT * FROM agents WHERE app_id=?",
                                 (row["app_id"],)).fetchone()
            conn.execute("COMMIT")
            task = dict(row)
            task["engine"] = {
                "type": agent["engine_type"],
                "command": agent["exec_path"],
                "work_dir": row["work_dir"] or agent["work_dir"],
                "base_url": agent["exec_path"],
                "session_id": agent["session_id"],
            }
            return task, None
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def report_task(task_id, token, result=None, error=None, session_id=None):
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT t.*, d.token AS device_token
                   FROM tasks t JOIN devices d ON d.device_id = t.device_id
                   WHERE t.task_id=?""", (task_id,)).fetchone()
            if row is None or row["device_token"] != token:
                return None
            if row["status"] not in ("running", "pending"):
                return None
            if error:
                conn.execute(
                    "UPDATE tasks SET status='failed', result=?, error=?, finished_at=datetime('now') WHERE task_id=?",
                    (result, error, task_id))
            else:
                conn.execute(
                    "UPDATE tasks SET status='done', result=?, error=NULL, finished_at=datetime('now') WHERE task_id=?",
                    (result, task_id))
            if session_id:
                conn.execute("UPDATE agents SET session_id=? WHERE app_id=?",
                             (session_id, row["app_id"]))
            updated = conn.execute("SELECT * FROM tasks WHERE task_id=?",
                                   (task_id,)).fetchone()
            return dict(updated)
        finally:
            conn.close()


def mark_timeout_tasks(seconds):
    out = []
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """SELECT * FROM tasks
                   WHERE status='running'
                     AND (julianday('now') - julianday(started_at)) * 86400 > ?""",
                (seconds,)).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE tasks SET status='timeout', finished_at=datetime('now') WHERE task_id=?",
                    (r["task_id"],))
            out = [dict(r) for r in rows]
        finally:
            conn.close()
    return out

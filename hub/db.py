import sqlite3
import threading
import uuid
import os
import re
import hashlib
import time
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
    card_message_id TEXT,
    permission TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','expired')),
    expires_at INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (task_id, session_id, permission_id)
);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    data BLOB NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
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
            for name, kind in (("attachment_id", "TEXT"), ("attachment_receive", "INTEGER DEFAULT 0")):
                if name not in columns:
                    conn.execute("ALTER TABLE tasks ADD COLUMN %s %s" % (name, kind))
            if "actor_role" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN actor_role TEXT DEFAULT 'editor'")
            if "execution_active" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN execution_active INTEGER DEFAULT 0")
                conn.execute("UPDATE tasks SET execution_active=1 WHERE status='running'")
            approval_columns = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
            if "card_message_id" not in approval_columns:
                conn.execute("ALTER TABLE approvals ADD COLUMN card_message_id TEXT")
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
            changed = conn.execute(
                "INSERT OR IGNORE INTO processed_messages (app_id, message_id) VALUES (?,?)",
                (app_id, message_id))
            return changed.rowcount == 1
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
                work_dir=None, sender_open_id=None, attachment_id=None, attachment_receive=False,
                actor_role="editor", enforce_limits=False):
    task_id = "t_" + uuid.uuid4().hex[:12]
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if enforce_limits:
                counts = conn.execute("""SELECT count(*), coalesce(sum(sender_open_id=?),0)
                    FROM tasks WHERE app_id=? AND status='pending'""", (sender_open_id, app_id)).fetchone()
                if counts[0] >= 20 or counts[1] >= 5:
                    raise ValueError("排队已达上限（每人 5 项、每 Agent 20 项），请等待已有任务完成")
            conn.execute(
                """INSERT INTO tasks (task_id, app_id, device_id, prompt,
                                      reply_message_id, reply_chat_id, work_dir,
                                      sender_open_id, attachment_id, attachment_receive, actor_role)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, app_id, device_id, prompt, reply_message_id, reply_chat_id,
                 work_dir, sender_open_id, attachment_id, int(attachment_receive), actor_role))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return task_id


def store_attachment(app_id, sender_id, chat_id, work_dir, name, data):
    if not sender_id or not chat_id or not data.startswith(b"%PDF-") or len(data) > 10 * 1024 * 1024:
        raise ValueError("仅支持不超过 10 MiB 的 PDF")
    ident = uuid.uuid4().hex
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            used = conn.execute("SELECT coalesce(sum(length(data)),0) FROM attachments").fetchone()[0]
            if used + len(data) > 200 * 1024 * 1024:
                raise ValueError("附件存储已达 200 MiB，请在电脑端归档清理后再上传")
            conn.execute("UPDATE attachments SET active=0 WHERE app_id=? AND sender_id=? AND chat_id=? AND work_dir=?",
                         (app_id, sender_id, chat_id, work_dir))
            conn.execute("INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (ident, app_id, sender_id, chat_id, work_dir, name,
                          hashlib.sha256(data).hexdigest(), data, 1, int(time.time())))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    return ident


def current_attachment(app_id, sender_id, chat_id, work_dir, clear=False):
    with _lock:
        conn = _connect()
        try:
            scope = (app_id, sender_id, chat_id, work_dir)
            if clear:
                conn.execute("UPDATE attachments SET active=0 WHERE app_id=? AND sender_id=? AND chat_id=? AND work_dir=?", scope)
                return None
            row = conn.execute("""SELECT id,name FROM attachments WHERE app_id=? AND sender_id=?
                AND chat_id=? AND work_dir=? AND active=1 AND created_at>? ORDER BY rowid DESC LIMIT 1""",
                               scope + (int(time.time()) - 86400,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def queue_view(app_id, device_id, sender_id):
    with _lock:
        conn = _connect()
        try:
            busy = conn.execute("SELECT count(*) FROM tasks WHERE device_id=? AND execution_active=1", (device_id,)).fetchone()[0]
            pending = conn.execute("SELECT count(*) FROM tasks WHERE device_id=? AND status='pending'", (device_id,)).fetchone()[0]
            own = conn.execute("SELECT count(*) FROM tasks WHERE app_id=? AND sender_open_id=? AND status='pending'", (app_id, sender_id)).fetchone()[0]
            return "设备执行中：%s；设备等待：%d 项；您在当前 Agent 等待：%d 项。运行任务不会被新请求抢占。" % ("是" if busy else "否", pending, own)
        finally:
            conn.close()


def user_history(app_id, sender_id):
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT task_id,status,created_at FROM tasks WHERE app_id=? AND sender_open_id=? ORDER BY rowid DESC LIMIT 8", (app_id, sender_id)).fetchall()
            return "您的最近任务：\n" + ("\n".join("%s %s %s" % tuple(row) for row in rows) or "暂无任务。")
        finally:
            conn.close()


def task_attachment(task_id, token, include_data=False):
    """Only the assigned device may fetch a running task's frozen attachment."""
    with _lock:
        conn = _connect()
        try:
            fields = "a.id,a.name,a.sha256" + (",a.data" if include_data else "")
            row = conn.execute("""SELECT """ + fields + """ FROM attachments a
                JOIN tasks t ON t.attachment_id=a.id JOIN devices d ON d.device_id=t.device_id
                WHERE t.task_id=? AND d.token=? AND t.status='running'
                AND a.app_id=t.app_id AND a.sender_id=t.sender_open_id
                AND a.chat_id=t.reply_chat_id AND a.work_dir=t.work_dir""", (task_id, token)).fetchone()
            if not row:
                return None
            result = dict(row)
            return result
        finally:
            conn.close()


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


def set_approval_card_message(code, message_id):
    if not message_id:
        return False
    with _lock:
        conn = _connect()
        try:
            updated = conn.execute(
                """UPDATE approvals SET card_message_id=?
                   WHERE code=? AND status='pending' AND card_message_id IS NULL""",
                (message_id, code),
            )
            return updated.rowcount == 1
        finally:
            conn.close()


def decide_approval(app_id, code, sender_open_id, chat_id, decision,
                    card_message_id=None):
    if decision not in ("approved", "rejected"):
        raise ValueError("invalid decision")
    with _lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT a.*, t.status AS task_status FROM approvals a
                   JOIN tasks t ON t.task_id=a.task_id
                   WHERE a.code=? AND a.app_id=?""", (code, app_id)
            ).fetchone()
            if (row is None or row["sender_open_id"] != sender_open_id
                    or row["chat_id"] != chat_id or row["status"] != "pending"
                    or (card_message_id is not None and
                        (not card_message_id or row["card_message_id"] != card_message_id))):
                conn.execute("ROLLBACK")
                return None
            if row["task_status"] != "running":
                conn.execute("UPDATE approvals SET status='expired' WHERE code=?", (code,))
                conn.execute("COMMIT")
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
            if conn.execute("SELECT 1 FROM tasks WHERE device_id=? AND execution_active=1 LIMIT 1", (device_id,)).fetchone():
                conn.execute("ROLLBACK")
                return None, None
            row = conn.execute(
                """SELECT * FROM tasks
                   WHERE device_id=? AND status='pending'
                   ORDER BY created_at, rowid LIMIT 1""",
                (device_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None, None
            conn.execute(
                "UPDATE tasks SET status='running', execution_active=1, started_at=datetime('now') WHERE task_id=?",
                (row["task_id"],))
            agent = conn.execute("SELECT * FROM agents WHERE app_id=?",
                                 (row["app_id"],)).fetchone()
            task = dict(row)
            if row["attachment_id"]:
                attachment = conn.execute("""SELECT id,name,sha256 FROM attachments
                    WHERE id=? AND app_id=? AND sender_id=? AND chat_id=? AND work_dir=?""",
                    (row["attachment_id"], row["app_id"], row["sender_open_id"],
                     row["reply_chat_id"], row["work_dir"])).fetchone()
                if attachment is None:
                    raise ValueError("attachment scope mismatch")
                task["attachment"] = dict(attachment)
            task["engine"] = {
                "type": agent["engine_type"],
                "command": agent["exec_path"],
                "work_dir": row["work_dir"] or agent["work_dir"],
                "base_url": agent["exec_path"],
                "session_id": None,
            }
            conn.execute("COMMIT")
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
            if row["status"] == "timeout" and row["execution_active"]:
                conn.execute("UPDATE tasks SET execution_active=0 WHERE task_id=?", (task_id,))
                return dict(row)
            if row["status"] != "running" or not row["execution_active"]:
                return None
            if error:
                conn.execute(
                    "UPDATE tasks SET status='failed', execution_active=0, result=?, error=?, finished_at=datetime('now') WHERE task_id=?",
                    (result, error, task_id))
            else:
                conn.execute(
                    "UPDATE tasks SET status='done', execution_active=0, result=?, error=NULL, finished_at=datetime('now') WHERE task_id=?",
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


def release_device_execution(device_id):
    """Local operator only: caller must first stop Node AND its OpenCode server."""
    with _lock:
        conn = _connect()
        try:
            changed = conn.execute("""UPDATE tasks SET execution_active=0,
                status=CASE WHEN status='running' THEN 'failed' ELSE status END,
                error=coalesce(error,'本机管理员确认执行器停止后释放占用'),
                finished_at=coalesce(finished_at,datetime('now'))
                WHERE device_id=? AND execution_active=1""", (device_id,))
            return changed.rowcount
        finally:
            conn.close()

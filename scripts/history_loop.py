#!/usr/bin/env python3
"""Write privacy-preserving operational snapshots for later continuation."""
import argparse
import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def now_utc():
    return datetime.now(timezone.utc)


def hub_health(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def task_summary(db_path):
    if not db_path.exists():
        return {"database": "missing"}
    conn = sqlite3.connect(str(db_path))
    try:
        counts = dict(conn.execute(
            "SELECT status, count(*) FROM tasks GROUP BY status"
        ).fetchall())
        latest = conn.execute(
            "SELECT task_id, status, created_at, finished_at "
            "FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 10"
        ).fetchall()
        return {
            "counts": counts,
            "latest": [
                {"task_id": row[0], "status": row[1], "created_at": row[2], "finished_at": row[3]}
                for row in latest
            ],
        }
    finally:
        conn.close()


def write_snapshot(history_dir, db_path, health_url):
    now = now_utc()
    payload = {
        "timestamp": now.isoformat(),
        "event": "health_snapshot",
        "hub": hub_health(health_url),
        "tasks": task_summary(db_path),
    }
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / ("snapshots-%s.jsonl" % now.strftime("%Y-%m-%d"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def prune(history_dir, retention_days):
    cutoff = time.time() - retention_days * 86400
    for path in history_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Agent Control history loop")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--history-dir", default=os.environ.get("AGENT_CONTROL_HISTORY", str(ROOT / "history")))
    parser.add_argument("--db", default=os.environ.get("AGENT_CONTROL_DB", str(ROOT / "hub" / "hub.db")))
    args = parser.parse_args()
    history_dir = Path(args.history_dir)
    db_path = Path(args.db)
    while True:
        payload = write_snapshot(history_dir, db_path, args.health_url)
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        prune(history_dir, max(1, args.retention_days))
        if args.once:
            break
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()

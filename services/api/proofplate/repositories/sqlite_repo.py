from __future__ import annotations

import json
import sqlite3
import threading

from .base import ConflictError, participation_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (site TEXT, kind TEXT, id TEXT, version INTEGER, doc TEXT, PRIMARY KEY (site, kind, id));
CREATE TABLE IF NOT EXISTS events (site TEXT, event_id TEXT, pkey TEXT, diner TEXT, food TEXT, event_time TEXT, doc TEXT, PRIMARY KEY (site, event_id));
CREATE INDEX IF NOT EXISTS events_by_diner ON events (site, diner, food, event_time);
CREATE TABLE IF NOT EXISTS counts (site TEXT, pkey TEXT, n INTEGER, PRIMARY KEY (site, pkey));
CREATE TABLE IF NOT EXISTS members (user_id TEXT, site TEXT, role TEXT, PRIMARY KEY (user_id, site));
"""


class SqliteRepository:
    """Durable local store for development and for running a pilot off-cloud."""

    def __init__(self, path: str = "proofplate.db") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()

    def get(self, site, kind, id):
        row = self.db.execute("SELECT doc FROM records WHERE site=? AND kind=? AND id=?", (site, kind, id)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, site, kind, id, data, *, create_only=False, expect_version=None):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT version FROM records WHERE site=? AND kind=? AND id=?", (site, kind, id)).fetchone()
                cur = row[0] if row else 0
                if create_only and row:
                    raise ConflictError(f"{kind} {id} already exists")
                if expect_version is not None and cur != expect_version:
                    raise ConflictError(f"{kind} {id} changed since it was read")
                doc = {**data, "id": id, "version": cur + 1}
                self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)", (site, kind, id, cur + 1, json.dumps(doc)))
                self.db.execute("COMMIT")
                return doc
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def list(self, site, kind):
        rows = self.db.execute("SELECT doc FROM records WHERE site=? AND kind=? ORDER BY id", (site, kind)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, site, kind, id):
        self.db.execute("DELETE FROM records WHERE site=? AND kind=? AND id=?", (site, kind, id))

    def append_event(self, site, event, expect_count):
        pkey = participation_key(event)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT doc FROM events WHERE site=? AND event_id=?", (site, event["event_id"])).fetchone()
                if row:
                    self.db.execute("ROLLBACK")
                    return json.loads(row[0]), False
                n = self.db.execute("SELECT n FROM counts WHERE site=? AND pkey=?", (site, pkey)).fetchone()
                if (n[0] if n else 0) != expect_count:
                    raise ConflictError("participation changed since it was read")
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                    site, event["event_id"], pkey, event["diner_token"], event["food_id"], event["event_time"], json.dumps(event)))
                self.db.execute("INSERT OR REPLACE INTO counts VALUES (?,?,?)", (site, pkey, expect_count + 1))
                self.db.execute("COMMIT")
                return event, True
            except ConflictError:
                self.db.execute("ROLLBACK")
                raise

    def list_events(self, site, *, diner_token=None, food_id=None):
        sql, args = "SELECT doc FROM events WHERE site=?", [site]
        if diner_token is not None:
            sql, args = sql + " AND diner=?", args + [diner_token]
        if food_id is not None:
            sql, args = sql + " AND food=?", args + [food_id]
        rows = self.db.execute(sql + " ORDER BY event_time, event_id", args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete_diner_events(self, site, token):
        with self.lock:
            cur = self.db.execute("DELETE FROM events WHERE site=? AND diner=?", (site, token))
            self.db.execute("DELETE FROM counts WHERE site=? AND pkey LIKE ?", (site, f"%|{token}|%"))
            return cur.rowcount

    def memberships(self, user_id):
        rows = self.db.execute("SELECT site, role FROM members WHERE user_id=?", (user_id,)).fetchall()
        return [{"site": s, "role": r} for s, r in rows]

    def put_membership(self, user_id, site, role):
        self.db.execute("INSERT OR REPLACE INTO members VALUES (?,?,?)", (user_id, site, role))

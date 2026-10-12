from __future__ import annotations

import copy
from typing import Optional

from .base import ConflictError, participation_key


class MemoryRepository:
    """Process-local store. Used for tests and for the stateless public demo,
    where it is rebuilt on every request from the seed plus the browser's own
    sandbox journal and then thrown away."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], dict] = {}
        self.events: dict[str, dict[str, dict]] = {}
        self.counts: dict[tuple[str, str], int] = {}
        self.members: dict[str, dict[str, str]] = {}
        self.journal: Optional[list[dict]] = None  # when a list, writes are recorded

    def _log(self, entry: dict) -> None:
        if self.journal is not None:
            self.journal.append(copy.deepcopy(entry))

    def get(self, site, kind, id):
        doc = self.records.get((site, kind, id))
        return copy.deepcopy(doc) if doc else None

    def put(self, site, kind, id, data, *, create_only=False, expect_version=None):
        cur = self.records.get((site, kind, id))
        if create_only and cur is not None:
            raise ConflictError(f"{kind} {id} already exists")
        if expect_version is not None and (cur or {}).get("version", 0) != expect_version:
            raise ConflictError(f"{kind} {id} changed since it was read")
        doc = {**copy.deepcopy(data), "id": id, "version": (cur or {}).get("version", 0) + 1}
        self.records[(site, kind, id)] = doc
        self._log({"op": "put", "kind": kind, "id": id, "data": doc})
        return copy.deepcopy(doc)

    def list(self, site, kind):
        return [copy.deepcopy(v) for (s, k, _), v in sorted(self.records.items()) if s == site and k == kind]

    def delete(self, site, kind, id):
        self.records.pop((site, kind, id), None)
        self._log({"op": "delete", "kind": kind, "id": id})

    def append_event(self, site, event, expect_count):
        bucket = self.events.setdefault(site, {})
        if event["event_id"] in bucket:
            return copy.deepcopy(bucket[event["event_id"]]), False
        key = (site, participation_key(event))
        if self.counts.get(key, 0) != expect_count:
            raise ConflictError("participation changed since it was read")
        bucket[event["event_id"]] = copy.deepcopy(event)
        self.counts[key] = expect_count + 1
        self._log({"op": "event", "event": event})
        return copy.deepcopy(event), True

    def list_events(self, site, *, diner_token=None, food_id=None):
        out = [
            copy.deepcopy(e) for e in self.events.get(site, {}).values()
            if (diner_token is None or e["diner_token"] == diner_token) and (food_id is None or e["food_id"] == food_id)
        ]
        return sorted(out, key=lambda e: (e["event_time"], e["event_id"]))

    def delete_diner_events(self, site, token):
        bucket = self.events.get(site, {})
        doomed = [k for k, e in bucket.items() if e["diner_token"] == token]
        for k in doomed:
            self.counts.pop((site, participation_key(bucket[k])), None)
            del bucket[k]
        self._log({"op": "delete_diner_events", "token": token})
        return len(doomed)

    def memberships(self, user_id):
        return [{"site": s, "role": r} for s, r in self.members.get(user_id, {}).items()]

    def put_membership(self, user_id, site, role):
        self.members.setdefault(user_id, {})[site] = role

    # ---- demo sandbox replay -------------------------------------------
    def replay(self, site: str, journal: list[dict]) -> None:
        """Apply a client-held sandbox journal. Entries were produced by this
        class, but they come back from a browser, so shapes are checked."""
        for entry in journal:
            op = entry.get("op")
            if op == "put" and isinstance(entry.get("data"), dict):
                self.records[(site, str(entry["kind"]), str(entry["id"]))] = copy.deepcopy(entry["data"])
            elif op == "delete":
                self.records.pop((site, str(entry["kind"]), str(entry["id"])), None)
            elif op == "event" and isinstance(entry.get("event"), dict):
                ev = entry["event"]
                key = (site, participation_key(ev))
                self.events.setdefault(site, {})[ev["event_id"]] = copy.deepcopy(ev)
                self.counts[key] = self.counts.get(key, 0) + 1
            elif op == "delete_diner_events":
                saved, self.journal = self.journal, None
                self.delete_diner_events(site, str(entry["token"]))
                self.journal = saved
            else:
                raise ValueError("unrecognised sandbox entry")

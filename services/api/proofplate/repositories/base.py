"""Storage contract shared by the in-memory, SQLite and DynamoDB adapters.

Records are small versioned JSON documents keyed by (site, kind, id).
Events are immutable and idempotent on event_id. Each participation
(session, diner, food) carries an event counter so that two operators writing
to the same plate at once cannot both pass validation: the second write fails
its counter check and the service re-reads and re-validates.
"""
from __future__ import annotations

from typing import Optional, Protocol


class ConflictError(Exception):
    """Optimistic-concurrency failure; the caller should re-read and retry."""


def participation_key(event: dict) -> str:
    return f"{event['session_id']}|{event['diner_token']}|{event['food_id']}"


class Repository(Protocol):
    def get(self, site: str, kind: str, id: str) -> Optional[dict]: ...
    def put(self, site: str, kind: str, id: str, data: dict, *, create_only: bool = False,
            expect_version: Optional[int] = None) -> dict: ...
    def list(self, site: str, kind: str) -> list[dict]: ...
    def delete(self, site: str, kind: str, id: str) -> None: ...
    def append_event(self, site: str, event: dict, expect_count: int) -> tuple[dict, bool]: ...
    def list_events(self, site: str, *, diner_token: Optional[str] = None,
                    food_id: Optional[str] = None) -> list[dict]: ...
    def delete_diner_events(self, site: str, token: str) -> int: ...
    def memberships(self, user_id: str) -> list[dict]: ...
    def put_membership(self, user_id: str, site: str, role: str) -> None: ...

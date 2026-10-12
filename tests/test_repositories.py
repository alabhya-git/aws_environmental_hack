"""One storage contract, three adapters: in-memory, SQLite and DynamoDB (via moto)."""
import os

import pytest

from proofplate.repositories.base import ConflictError
from proofplate.repositories.memory import MemoryRepository
from proofplate.repositories.sqlite_repo import SqliteRepository


def _dynamo():
    moto = pytest.importorskip("moto")
    import boto3

    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
    mock = moto.mock_aws()
    mock.start()
    client = boto3.client("dynamodb", region_name="ap-south-1")
    client.create_table(
        TableName="t", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
    from proofplate.repositories.dynamo_repo import DynamoRepository

    return DynamoRepository("t", client), mock


@pytest.fixture(params=["memory", "sqlite", "dynamo"])
def repo(request, tmp_path):
    if request.param == "memory":
        yield MemoryRepository()
    elif request.param == "sqlite":
        r = SqliteRepository(str(tmp_path / "t.db"))
        yield r
        r.db.close()
    else:
        r, mock = _dynamo()
        yield r
        mock.stop()


def ev(event_id, token="T-1", session="s1", food="rice", t="2026-10-11T07:00:00+00:00", type_="serve_initial", grams=180):
    return {"event_id": event_id, "session_id": session, "diner_token": token, "food_id": food, "type": type_,
            "grams": grams, "event_time": t, "origin": "measured"}


def test_records_are_versioned_and_conflicts_are_detected(repo):
    a = repo.put("site", "session", "s1", {"name": "Lunch", "n": 1})
    assert a["version"] == 1 and repo.get("site", "session", "s1")["name"] == "Lunch"
    b = repo.put("site", "session", "s1", {"name": "Lunch", "n": 2}, expect_version=1)
    assert b["version"] == 2
    with pytest.raises(ConflictError):
        repo.put("site", "session", "s1", {"n": 3}, expect_version=1)      # stale writer loses, nothing is overwritten
    assert repo.get("site", "session", "s1")["n"] == 2
    with pytest.raises(ConflictError):
        repo.put("site", "session", "s1", {"n": 9}, create_only=True)
    assert repo.get("site", "session", "missing") is None
    repo.put("site", "food", "rice", {"name": "Rice"})
    assert [r["id"] for r in repo.list("site", "session")] == ["s1"]
    repo.delete("site", "food", "rice")
    assert repo.list("site", "food") == []


def test_sites_do_not_see_each_other(repo):
    repo.put("a", "diner", "T-1", {"token": "T-1"})
    repo.append_event("a", ev("e-0000001"), expect_count=0)
    assert repo.get("b", "diner", "T-1") is None and repo.list("b", "diner") == [] and repo.list_events("b") == []


def test_event_append_is_idempotent(repo):
    first, created = repo.append_event("site", ev("e-0000001"), expect_count=0)
    again, created_again = repo.append_event("site", ev("e-0000001", grams=999), expect_count=0)
    assert created is True and created_again is False
    assert again["grams"] == 180                                  # the stored event wins, the retry changes nothing
    assert len(repo.list_events("site")) == 1


def test_concurrent_writers_to_one_plate_cannot_both_win(repo):
    repo.append_event("site", ev("e-0000001"), expect_count=0)
    with pytest.raises(ConflictError):                             # second station validated against an empty plate
        repo.append_event("site", ev("e-0000002"), expect_count=0)
    repo.append_event("site", ev("e-0000002", type_="serve_seconds", grams=30, t="2026-10-11T07:05:00+00:00"), expect_count=1)
    with pytest.raises(ConflictError):
        repo.append_event("site", ev("e-0000003", type_="return", grams=0), expect_count=1)
    assert [e["event_id"] for e in repo.list_events("site")] == ["e-0000001", "e-0000002"]
    # a different plate has its own counter
    repo.append_event("site", ev("e-0000009", token="T-2"), expect_count=0)


def test_events_are_listed_in_time_order_with_filters(repo):
    repo.append_event("site", ev("e-0000002", t="2026-10-11T07:10:00+00:00", session="s2"), expect_count=0)
    repo.append_event("site", ev("e-0000001", t="2026-10-10T07:00:00+00:00"), expect_count=0)
    repo.append_event("site", ev("e-0000003", token="T-2", food="dal"), expect_count=0)
    assert [e["event_id"] for e in repo.list_events("site", diner_token="T-1")] == ["e-0000001", "e-0000002"]
    assert [e["event_id"] for e in repo.list_events("site", food_id="dal")] == ["e-0000003"]
    assert [e["event_id"] for e in repo.list_events("site", diner_token="T-1", food_id="rice")] == ["e-0000001", "e-0000002"]
    assert len(repo.list_events("site")) == 3


def test_deleting_a_diner_removes_their_events_and_frees_the_plate(repo):
    repo.append_event("site", ev("e-0000001"), expect_count=0)
    repo.append_event("site", ev("e-0000002", token="T-2"), expect_count=0)
    assert repo.delete_diner_events("site", "T-1") == 1
    assert [e["diner_token"] for e in repo.list_events("site")] == ["T-2"]
    repo.append_event("site", ev("e-0000005"), expect_count=0)     # counters were cleared with the events


def test_memberships(repo):
    assert repo.memberships("u1") == []
    repo.put_membership("u1", "site-a", "manager")
    assert repo.memberships("u1") == [{"site": "site-a", "role": "manager"}]
    assert repo.memberships("u2") == []

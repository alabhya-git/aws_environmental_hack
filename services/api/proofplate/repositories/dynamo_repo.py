"""DynamoDB single-table adapter.

Keys (PK, SK):
  SITE#<site> | REC#<kind>#<id>                         versioned JSON record
  SITE#<site> | EVT#<diner>#<food>#<time>#<event_id>    immutable event
  SITE#<site> | IDEM#<event_id>                         idempotency anchor -> event SK
  SITE#<site> | PART#<session>|<diner>|<food>           per-plate event counter
  USER#<sub>  | SITE#<site>                             operator membership and role

An event write is one transaction: the idempotency anchor must not exist and
the plate counter must equal what the caller validated against. Reads are
strongly consistent, so the next recommendation always sees the last return.
Documents are stored as JSON strings to keep integers exact and avoid
float/Decimal conversion.
"""
from __future__ import annotations

import json
from typing import Optional

from .base import ConflictError, participation_key


class DynamoRepository:
    def __init__(self, table_name: str, client=None) -> None:
        if client is None:
            import boto3

            client = boto3.client("dynamodb")
        self.c = client
        self.table = table_name

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def _pk(site: str) -> dict:
        return {"S": f"SITE#{site}"}

    def _query(self, pk: str, prefix: str) -> list[dict]:
        items, start = [], None
        while True:
            kw = dict(
                TableName=self.table, ConsistentRead=True,
                KeyConditionExpression="PK = :p AND begins_with(SK, :s)",
                ExpressionAttributeValues={":p": {"S": pk}, ":s": {"S": prefix}},
            )
            if start:
                kw["ExclusiveStartKey"] = start
            page = self.c.query(**kw)
            items.extend(page.get("Items", []))
            start = page.get("LastEvaluatedKey")
            if not start:
                return items

    # ---- records -------------------------------------------------------
    def get(self, site, kind, id):
        r = self.c.get_item(TableName=self.table, Key={"PK": self._pk(site), "SK": {"S": f"REC#{kind}#{id}"}}, ConsistentRead=True)
        return json.loads(r["Item"]["doc"]["S"]) if "Item" in r else None

    def put(self, site, kind, id, data, *, create_only=False, expect_version=None):
        key = {"PK": self._pk(site), "SK": {"S": f"REC#{kind}#{id}"}}
        for _ in range(4):
            cur = self.c.get_item(TableName=self.table, Key=key, ConsistentRead=True).get("Item")
            cur_v = int(cur["version"]["N"]) if cur else 0
            if create_only and cur:
                raise ConflictError(f"{kind} {id} already exists")
            if expect_version is not None and cur_v != expect_version:
                raise ConflictError(f"{kind} {id} changed since it was read")
            doc = {**data, "id": id, "version": cur_v + 1}
            item = {**key, "version": {"N": str(cur_v + 1)}, "doc": {"S": json.dumps(doc)}}
            try:
                if cur:
                    self.c.put_item(TableName=self.table, Item=item, ConditionExpression="version = :v",
                                    ExpressionAttributeValues={":v": {"N": str(cur_v)}})
                else:
                    self.c.put_item(TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(PK)")
                return doc
            except self.c.exceptions.ConditionalCheckFailedException:
                if create_only or expect_version is not None:
                    raise ConflictError(f"{kind} {id} changed since it was read")
        raise ConflictError(f"{kind} {id} is being written concurrently")

    def list(self, site, kind):
        return [json.loads(i["doc"]["S"]) for i in self._query(f"SITE#{site}", f"REC#{kind}#")]

    def delete(self, site, kind, id):
        self.c.delete_item(TableName=self.table, Key={"PK": self._pk(site), "SK": {"S": f"REC#{kind}#{id}"}})

    # ---- events --------------------------------------------------------
    def append_event(self, site, event, expect_count):
        pk = self._pk(site)
        sk = f"EVT#{event['diner_token']}#{event['food_id']}#{event['event_time']}#{event['event_id']}"
        idem = {"PK": pk, "SK": {"S": f"IDEM#{event['event_id']}"}}
        counter = {"PK": pk, "SK": {"S": f"PART#{participation_key(event)}"}}
        if expect_count == 0:
            cond, vals = "attribute_not_exists(PK)", {":one": {"N": "1"}}
        else:
            cond, vals = "n = :n", {":one": {"N": "1"}, ":n": {"N": str(expect_count)}}
        try:
            self.c.transact_write_items(TransactItems=[
                {"Put": {"TableName": self.table, "Item": {**idem, "ref": {"S": sk}},
                         "ConditionExpression": "attribute_not_exists(PK)"}},
                {"Put": {"TableName": self.table, "Item": {"PK": pk, "SK": {"S": sk}, "doc": {"S": json.dumps(event)}}}},
                {"Update": {"TableName": self.table, "Key": counter, "UpdateExpression": "ADD n :one",
                            "ConditionExpression": cond, "ExpressionAttributeValues": vals}},
            ])
            return event, True
        except self.c.exceptions.TransactionCanceledException as exc:
            codes = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
            if codes and codes[0] == "ConditionalCheckFailed":  # same event_id already stored
                ref = self.c.get_item(TableName=self.table, Key=idem, ConsistentRead=True).get("Item")
                if ref:
                    got = self.c.get_item(TableName=self.table, Key={"PK": pk, "SK": ref["ref"]}, ConsistentRead=True).get("Item")
                    if got:
                        return json.loads(got["doc"]["S"]), False
            raise ConflictError("participation changed since it was read") from exc

    def list_events(self, site, *, diner_token=None, food_id=None):
        prefix = "EVT#"
        if diner_token is not None:
            prefix += f"{diner_token}#"
            if food_id is not None:
                prefix += f"{food_id}#"
        out = [json.loads(i["doc"]["S"]) for i in self._query(f"SITE#{site}", prefix)]
        if food_id is not None and diner_token is None:
            out = [e for e in out if e["food_id"] == food_id]
        return sorted(out, key=lambda e: (e["event_time"], e["event_id"]))

    def delete_diner_events(self, site, token):
        pk = self._pk(site)
        items = self._query(f"SITE#{site}", f"EVT#{token}#")
        keys = []
        for i in items:
            ev = json.loads(i["doc"]["S"])
            keys.append(i["SK"]["S"])
            keys.append(f"IDEM#{ev['event_id']}")
            keys.append(f"PART#{participation_key(ev)}")
        for sk in dict.fromkeys(keys):
            self.c.delete_item(TableName=self.table, Key={"PK": pk, "SK": {"S": sk}})
        return len(items)

    # ---- membership ----------------------------------------------------
    def memberships(self, user_id):
        return [{"site": i["SK"]["S"][5:], "role": i["role"]["S"]} for i in self._query(f"USER#{user_id}", "SITE#")]

    def put_membership(self, user_id, site, role):
        self.c.put_item(TableName=self.table, Item={"PK": {"S": f"USER#{user_id}"}, "SK": {"S": f"SITE#{site}"}, "role": {"S": role}})

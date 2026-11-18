"""Tiny typed boto3 wrapper for the `la-polla-del-miedo` DynamoDB table.

Single-table design. Key scheme:

    Prediction   pk = "MATCH#<match_id>"      sk = "PREDICTION"
    Standings    pk = "STANDINGS#<date>"      sk = "SNAPSHOT"
    Submission   pk = "MATCH#<match_id>"      sk = "SUBMISSION#<ts>"

The boto3 resource is created lazily (`_table()`), so importing this module
never touches the network or requires AWS credentials. That matters because
`jobs/*` import the store at module load, and the unit tests monkeypatch these
functions rather than reaching DynamoDB. Table name and region come from
`secrets()` so they stay in one place.

DynamoDB cannot store Python floats, so writes round-trip through `Decimal`
(`_to_dynamo`) and reads convert back to native ints/floats (`_from_dynamo`).
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

from ..config import secrets

# --- key scheme -----------------------------------------------------------

PREDICTION_SK = "PREDICTION"
STANDINGS_SK = "SNAPSHOT"


def _match_pk(match_id: str) -> str:
    return f"MATCH#{match_id}"


def _standings_pk(date: str) -> str:
    return f"STANDINGS#{date}"


def _submission_sk(ts: str) -> str:
    return f"SUBMISSION#{ts}"


# --- lazy resource --------------------------------------------------------


@lru_cache(maxsize=1)
def _table():
    """Return the boto3 Table handle, created on first use only."""
    import boto3  # imported here so module import stays cred-free

    s = secrets()
    resource = boto3.resource("dynamodb", region_name=s.aws_region)
    return resource.Table(s.dynamo_table)


# --- (de)serialization ----------------------------------------------------


def _to_dynamo(value: Any) -> Any:
    """Recursively make a value DynamoDB-safe (float -> Decimal)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        # str() keeps the exact textual value; float() would re-introduce
        # binary-float noise that Decimal would then faithfully preserve.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert DynamoDB Decimals back to int/float."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


# --- public API -----------------------------------------------------------


def put_prediction(match_id: str, prediction: dict) -> None:
    """Upsert the current model prediction for a match (idempotent)."""
    item = {
        "pk": _match_pk(match_id),
        "sk": PREDICTION_SK,
        "match_id": str(match_id),
        "prediction": _to_dynamo(prediction),
    }
    _table().put_item(Item=item)


def get_prediction(match_id: str) -> dict | None:
    """Return the stored prediction dict for a match, or None if absent."""
    resp = _table().get_item(
        Key={"pk": _match_pk(match_id), "sk": PREDICTION_SK}
    )
    item = resp.get("Item")
    if not item:
        return None
    return _from_dynamo(item.get("prediction", {}))


def put_standings(date: str, standings: list) -> None:
    """Store a standings snapshot for a given date (idempotent per date)."""
    item = {
        "pk": _standings_pk(date),
        "sk": STANDINGS_SK,
        "date": str(date),
        "standings": _to_dynamo(standings),
    }
    _table().put_item(Item=item)


def log_submission(match_id: str, score: str, ts: str) -> None:
    """Append an immutable submission record (one row per ts)."""
    item = {
        "pk": _match_pk(match_id),
        "sk": _submission_sk(ts),
        "match_id": str(match_id),
        "score": str(score),
        "ts": str(ts),
    }
    _table().put_item(Item=item)

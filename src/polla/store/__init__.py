"""Persistence layer for la-polla-del-miedo (DynamoDB)."""
from __future__ import annotations

from .dynamo import (
    get_prediction,
    log_submission,
    put_prediction,
    put_standings,
)

__all__ = [
    "get_prediction",
    "log_submission",
    "put_prediction",
    "put_standings",
]

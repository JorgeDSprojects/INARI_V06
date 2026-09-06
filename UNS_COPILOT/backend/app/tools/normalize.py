from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_json_safe(value: Any) -> Any:
    """Convert raw asyncpg row values into JSON-native ones.

    Tool results are serialised with `json.dumps` before being handed to the
    model as a `tool` message, and asyncpg hands back Python types that
    `json.dumps` refuses: `datetime` for TIMESTAMPTZ columns (`time`,
    `bucket`) and `Decimal` for NUMERIC ones (`value_numeric`, `range_min`,
    `range_max`, `avg_value`, `min_value`, `max_value`).

    Normalising here rather than via `json.dumps(default=str)` matters: a
    Decimal stringified by `default=str` reaches the model as `"22.0"` (a
    quoted string it then has to parse), whereas converting to `float` gives
    it a real JSON number it can reason about arithmetically.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    return value


def row_to_json_safe(row: Mapping) -> dict:
    """`dict(row)` for an asyncpg result row, with every value normalised."""
    return {key: to_json_safe(value) for key, value in row.items()}

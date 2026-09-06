from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


class GetCatalogParams(BaseModel):
    topic_filter: str | None = None
    signal_type: Literal["raw", "kpi"] | None = None


class GetLatestValueParams(BaseModel):
    topic: str
    signal_key: str


class QueryReadingsParams(BaseModel):
    topic: str
    signal_key: str
    from_time: datetime
    to_time: datetime
    agg: Literal["raw", "1m", "1h"] = "raw"


class ListEventsParams(BaseModel):
    topic_filter: str | None = None
    event_key: str | None = None
    from_time: datetime
    to_time: datetime


def check_raw_range(params: QueryReadingsParams, max_raw_range_hours: int) -> None:
    """Guardrail from spec Section 4: a raw-resolution query spanning too
    long a window could pull unbounded 1Hz+ data into a (possibly small,
    local-model) context window. Aggregated queries (1m/1h) are exempt --
    they already return a bounded number of buckets regardless of span."""
    if params.agg != "raw":
        return
    span = params.to_time - params.from_time
    if span > timedelta(hours=max_raw_range_hours):
        raise ValueError(
            f"raw query range of {span} exceeds the {max_raw_range_hours}h limit for agg='raw'; "
            "use agg='1m' or agg='1h' for a wider window"
        )

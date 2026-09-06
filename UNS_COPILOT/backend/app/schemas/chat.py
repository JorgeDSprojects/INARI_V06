from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserRead(_Base):
    id: int
    display_name: str
    created_at: datetime

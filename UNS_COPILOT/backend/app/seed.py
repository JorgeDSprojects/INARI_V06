from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import User


async def seed_default_users(session: AsyncSession, names: list[str]) -> None:
    """Idempotent: does nothing if the users table already has any rows,
    so a restart never duplicates or resets the selector's user list."""
    existing = (await session.execute(select(User.id).limit(1))).first()
    if existing is not None:
        return
    for name in names:
        name = name.strip()
        if name:
            session.add(User(display_name=name))
    await session.commit()

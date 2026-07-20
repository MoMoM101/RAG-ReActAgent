"""Database persistence primitives for the user profile."""

from datetime import UTC, datetime

from sqlalchemy import select

from memory.profile_core import empty_profile
from models.database import session_scope
from models.orm import UserProfile


async def load_profile() -> dict:
    async with session_scope() as session:
        result = await session.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return row.profile_data if row else empty_profile()


async def save_profile(data: dict) -> None:
    async with session_scope() as session:
        result = await session.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            row.profile_data = data
            row.version += 1
            row.generated_at = datetime.now(UTC)
        else:
            session.add(UserProfile(profile_data=data, memory_ids=[], version=1))
        await session.commit()

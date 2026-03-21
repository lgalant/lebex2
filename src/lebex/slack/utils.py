from asyncpg.exceptions import UniqueViolationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SlackEventInDB


async def is_duplicate_event(
    event_id: str, event_body: dict, db_session: AsyncSession
) -> bool:
    slack_event_indb = SlackEventInDB(
        event_id=event_id,
        event_body=event_body,
    )
    try:
        async with db_session.begin():
            db_session.add(slack_event_indb)
        return False
    except (IntegrityError, UniqueViolationError):
        return True

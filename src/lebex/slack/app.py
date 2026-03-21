import datetime
import logging
import os
import uuid
from collections.abc import Awaitable
from collections.abc import Callable

import httpx
from async_lru import alru_cache
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.say.async_say import AsyncSay
from slack_sdk.web.async_client import AsyncSlackResponse
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from lebane.client import LebaneClient
from lebex.app.main import aanswer
from lebex.core.models import LebexMessageInDB
from lebex.core.settings import Settings

from .schemas.slack_event import SlackEvent
from .utils import is_duplicate_event


logger = logging.getLogger(__name__)


app = AsyncApp(
    # Token fails if None, annoying when testing
    token=os.getenv("SLACK_BOT_TOKEN", "xoxb-fake"),
)


async def save_lebex_message(
    content: str,
    sender: str,
    receiver: str,
    thread_id: str,
    db_session: AsyncSession,
):
    try:
        unique_id = str(uuid.uuid4())
        lebex_message_in_db = LebexMessageInDB(
            version=1,
            platform="SLACK",
            ocurred_at=datetime.datetime.now(tz=datetime.UTC),
            unique_id=unique_id,
            thread_id=thread_id,
            sender=sender,
            receiver=receiver,
            content=content,
            created_by="lebex",
            updated_by="lebex",
        )
        async with db_session.begin():
            db_session.add(lebex_message_in_db)
    except Exception:
        logger.warn(
            "Failed to save LebexMessage with unique_id of %s",
            unique_id,
            exc_info=True,
        )


async def save_lebex_message_from_slack(
    event: SlackEvent, thread_id: str, db_session: AsyncSession
) -> None:
    try:
        lebex_message_in_db = LebexMessageInDB(
            version=1,
            platform="SLACK",
            ocurred_at=datetime.datetime.fromtimestamp(
                float(event.event.ts), tz=datetime.UTC
            ),
            unique_id=event.event_id,
            thread_id=thread_id,
            sender=event.event.user,
            receiver=event.event.channel,
            content=event.event.text,
            created_by="lebex",
            updated_by="lebex",
        )
        async with db_session.begin():
            db_session.add(lebex_message_in_db)
    except Exception:
        logger.warn(
            "Failed to save LebexMessage with unique_id of %s",
            event.event_id,
            exc_info=True,
        )


async def send_message(
    say: AsyncSay,
    content: str,
    receiver: str,
    thread_id: str,
    dbsessionmaker: async_sessionmaker[AsyncSession],
):
    response = await say.client.auth_test()
    sender = response["user_id"]
    await say(text=content, channel=receiver)
    async with dbsessionmaker() as db_session:
        await save_lebex_message(
            content=content,
            sender=sender,
            receiver=receiver,
            thread_id=thread_id,
            db_session=db_session,
        )


@alru_cache(maxsize=1000, ttl=3600)
async def get_user_profile(
    user: str, client: AsyncWebClient
) -> AsyncSlackResponse:
    return await client.users_profile_get(user=user)


@app.event("message")
async def handle_message(
    body: dict,
    client: AsyncWebClient,
    ack: AsyncAck,
    say: AsyncSay,
    request: Request,
    dbsessionmaker: async_sessionmaker[AsyncSession],
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    lsessionmaker: Callable[[str], Awaitable[LebaneClient]],
    ldbsessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    await ack()
    assert dbsessionmaker is not None
    assert checkpointer is not None
    assert store is not None
    assert lsessionmaker is not None

    if "bot_id" in body["event"]:
        return  # skip bot messages

    if (
        "thread_ts" in body["event"]
        and body["event"]["thread_ts"] != body["event"]["ts"]
    ):
        logger.info("Ignored thread message: %s", body["event"]["text"])
        return  # skip messages on threads

    body["event"].setdefault("subtype", None)
    slack_event = SlackEvent(**body)

    event_id = slack_event.event_id
    async with dbsessionmaker() as db_session:
        if not event_id or await is_duplicate_event(
            event_id=event_id, event_body=body, db_session=db_session
        ):
            logger.info(f"Duplicate Slack event {event_id}, skipping.")
            return

    event = slack_event.event

    try:
        result = await get_user_profile(user=event.user, client=client)
        phone = result["profile"]["phone"]
        assert phone is not None and phone.strip() != ""
    except (KeyError, AssertionError) as exc:
        await say(f"Cabeza, tenes que agregar el teléfono a Slack: {str(exc)}")
        return
    except Exception as exc:
        await say(f"Failed to get user phone: {str(exc)}")
        return

    try:
        _lsessionmaker = await lsessionmaker(phone)
    except httpx.HTTPStatusError:
        await say(
            "Fallo de autenticación con Lebane, verifique su nro de teléfono"
        )
        return

    async with dbsessionmaker() as db_session:
        await save_lebex_message_from_slack(
            event=slack_event, thread_id=phone, db_session=db_session
        )

    response = await aanswer(
        text=event.text.strip("<>"),  # TODO: Strip for links :/
        configurable={
            "thread_id": phone,
            "dbsessionmaker": dbsessionmaker,
            "checkpointer": checkpointer,
            "store": store,
            "lsessionmaker": _lsessionmaker,
            "ldbsessionmaker": ldbsessionmaker,
            "settings": settings,
        },
    )

    if isinstance(response, str):
        await send_message(
            say=say,
            content=response,
            receiver=event.channel,
            thread_id=phone,
            dbsessionmaker=dbsessionmaker,
        )
    elif isinstance(response, list):
        for message in response:
            if isinstance(message, str):
                await send_message(
                    say=say,
                    content=message,
                    receiver=event.channel,
                    thread_id=phone,
                    dbsessionmaker=dbsessionmaker,
                )
            elif isinstance(message, dict):
                if "file" in message and message["file"]:
                    async with httpx.AsyncClient() as httpclient:
                        fresponse = await httpclient.get(url=message["file"])

                    await client.files_upload_v2(
                        channel=event.channel,
                        content=fresponse.content,
                        initial_comment=message["text"],
                        thread_ts=event.thread_ts,
                    )
                else:
                    await send_message(
                        say=say,
                        content=message["text"],
                        receiver=event.channel,
                        thread_id=phone,
                        dbsessionmaker=dbsessionmaker,
                    )
    else:
        raise Exception("aanswer response was of non supported type")

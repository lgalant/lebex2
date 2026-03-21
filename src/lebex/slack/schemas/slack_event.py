from pydantic import BaseModel

from .message_event import MessageEvent


class SlackEvent(BaseModel):
    event_id: str
    event: MessageEvent

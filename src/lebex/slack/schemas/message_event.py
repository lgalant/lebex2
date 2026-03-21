import re
from typing import Annotated
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import UUID4
from pydantic import Field

from lebex.api.core.schemas.base import Base


TeamID = Annotated[str, Field(pattern=re.compile(r"T[A-Z0-9]+"))]
UserID = Annotated[str, Field(pattern=re.compile(r"U[A-Z0-9]+"))]


class BaseMessageEvent(Base):
    type: Literal["message"]
    client_msg_id: UUID4
    parent_user_id: Optional[UserID] = None
    user: UserID
    team: TeamID
    text: str
    blocks: list
    ts: str
    thread_ts: Optional[str] = None
    channel: str
    event_ts: str
    channel_type: Optional[str] = None


class File(Base):
    id: str
    created: int
    name: str
    mimetype: str
    filetype: str
    user: Optional[UserID]
    url_private: str
    url_private_download: Optional[str] = None
    title: Optional[str] = None
    permalink: Optional[str] = None
    size: Optional[int] = None


class PlainMessageEvent(BaseMessageEvent):
    subtype: Literal[None] = None


class FileShareMessageEvent(BaseMessageEvent):
    subtype: Literal["file_share"]
    files: list[File]


MessageEvent = Annotated[
    Union[PlainMessageEvent, FileShareMessageEvent],
    Field(discriminator="subtype"),
]

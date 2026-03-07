from typing import Annotated
from typing import TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import trim_messages
from langgraph.graph.message import Messages
from langgraph.graph.message import add_messages


class LebaneUserContext(TypedDict, total=False):
    user_id: int
    phone: str
    email: str
    location: None | str
    country: None | str
    timezone: str
    organization_id: int
    permissions: list[str]
    projects: list[int]


def add_and_trim_messages(left: Messages, right: Messages) -> Messages:
    return trim_messages(
        messages=add_messages(left, right),
        strategy="last",
        token_counter=len,
        max_tokens=100,
        start_on="human",
        include_system=True,
    )


class AppState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_and_trim_messages]
    question_intent: str | None
    lebane_user_context: LebaneUserContext | None
    core_label: str | None
    agentic: bool

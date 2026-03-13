from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage


def get_recent_human_messages(
    messages: list[BaseMessage], k=1
) -> list[HumanMessage]:
    return [
        message
        for message in reversed(messages)
        if isinstance(message, HumanMessage)
    ][:k]


def get_last_user_message(messages: list[BaseMessage]) -> HumanMessage | None:
    for message in get_recent_human_messages(messages=messages, k=1):
        return message
    return None


def get_recent_ai_messages(
    messages: list[BaseMessage], k=1
) -> list[AIMessage]:
    return [
        message
        for message in reversed(messages)
        if isinstance(message, AIMessage)
    ][:k]


def get_last_ai_message(messages: list[BaseMessage]) -> AIMessage | None:
    for message in get_recent_ai_messages(messages=messages, k=1):
        return message
    return None

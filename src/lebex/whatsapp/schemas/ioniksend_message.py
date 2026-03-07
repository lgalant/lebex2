from lebex.api.core.schemas.base import Base


class IoniksendMessage(Base):
    message_from: str
    chatbot_num: str
    client_num: str
    datetime: str
    unique_id: str
    body: str

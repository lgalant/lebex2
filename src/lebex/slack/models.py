import datetime

from sqlalchemy import JSON
from sqlalchemy import Column
from sqlalchemy import Index
from sqlalchemy import String
from sqlalchemy import func
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from lebex.core.models import BaseInDB
from lebex.core.models import UTCDateTime


class SlackEventInDB(BaseInDB):
    __tablename__ = "slack_event"
    __table_args__ = (
        Index("ix_slack_event_event_id", "event_id", unique=True),
    )

    event_id = Column(
        String,
        nullable=False,
        comment="Event ID from Slack",
    )
    received_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        comment="Timestamp of record creation",
    )
    event_body = Column(
        JSON,
        nullable=False,
        comment="Event body from Slack",
    )

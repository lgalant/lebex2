import datetime

from sqlalchemy import TIMESTAMP
from sqlalchemy import Integer
from sqlalchemy import SmallInteger
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """
    TIMESTAMP that is guaranteed to be timezone‑aware in Python code.
    • Accepts naive or aware datetimes on INSERT/UPDATE:
        - Naive → assume UTC
        - Aware → convert to UTC
    • On SELECT, always returns tz‑aware UTC datetime.
    """

    impl = TIMESTAMP(timezone=True)
    cache_ok = True  # SQLAlchemy 2.x requirement for baked queries

    # ----- Python → DB ----------------------------------------------------
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            value = value.replace(tzinfo=datetime.UTC)
        return value.astimezone(datetime.UTC)

    # ----- DB → Python ----------------------------------------------------
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            # psycopg2 and SQLite often return naive UTC
            return value.replace(tzinfo=datetime.UTC)
        return value.astimezone(datetime.UTC)


class BaseInDB(DeclarativeBase):
    __abstract__ = True

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="Unique ID",
    )


class BaseWithAuditInDB(BaseInDB):
    __abstract__ = True

    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        insert_default=func.now(),
        comment="Timestamp of record creation",
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
        comment="Timestamp of record last update",
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="User or system that created the record",
    )
    updated_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="User or system that last updated the record",
    )


class LebexMessageInDB(BaseWithAuditInDB):
    __tablename__ = "lebex_message"

    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    platform: Mapped[str] = mapped_column(String(255), nullable=False)
    unique_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ocurred_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, nullable=False
    )
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    receiver: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

from typing import Optional

from sqlalchemy import Boolean
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from lebane.core.models import Base
from lebane.core.types.sa_value_enum import ValueEnum
from lebane.user.types import UserState


class UserInDB(Base):
    __tablename__ = "usuario"

    id: Mapped[Optional[int]] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column("nombre", String, nullable=False)
    second_name: Mapped[str] = mapped_column(
        "segundo_nombre", String, nullable=False
    )
    surname: Mapped[str] = mapped_column("apellido", String, nullable=False)
    second_surname: Mapped[str] = mapped_column(
        "segundo_apellido", String, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        "organizacion_id", Integer, nullable=False
    )
    username: Mapped[str] = mapped_column(
        "correo_electronico", String, nullable=False
    )
    state: Mapped[UserState] = mapped_column(
        "estado", ValueEnum(UserState), nullable=False
    )

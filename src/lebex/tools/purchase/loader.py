import datetime
import enum
import logging

from sqlalchemy import Enum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from lebane.core.models import Base
from lebane.requisition.schemas import RequisitionCreate
from lebane.requisition.schemas.create import RequisitionItemCreate
from lebane.requisition.schemas.types import ItemType
from lebane.requisition.schemas.types import UnitOfMeasurement
from lebane.requisition.schemas.types import UnitOfMeasurementType


logger = logging.getLogger(__name__)


class ProjectState(enum.StrEnum):
    APPROVED = "APROBADO"
    CLOSED = "CERRADO"
    COMPLETED = "COMPLETADO"
    DELETED = "ELIMINADO"
    STARTED = "INICIADO"
    PRE_STARTED = "PRE_INICIADO"


class ProjectInDB(Base):
    __tablename__ = "proyecto"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column("nombre", String, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        "organizacion_id", Integer, nullable=False
    )
    state: Mapped[ProjectState] = mapped_column(
        "estado",
        Enum(
            ProjectState,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )


class CategoryInDB(Base):
    __tablename__ = "rubro"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column("nombre", String, nullable=False)

    # backref for convenience
    items: Mapped[list["ItemInDB"]] = relationship(back_populates="category")


class ItemInDB(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        "rubro_id", ForeignKey("rubro.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column("nombre", String, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        "organizacion_id", Integer, nullable=False
    )
    search: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[ItemType] = mapped_column(
        "tipo",
        Enum(
            ItemType,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
    category: Mapped["CategoryInDB"] = relationship(back_populates="items")


def build_requisition(
    project_id: int | None,
    responsible_id: int | None,
    items: list[dict],
) -> RequisitionCreate:
    item_schemas = []
    for item in items:
        delivery_date = item.get("delivery_date")
        if delivery_date:
            expected_at = datetime.datetime.fromisoformat(delivery_date).replace(
                tzinfo=datetime.timezone.utc,
                hour=0, minute=0, second=0, microsecond=0,
            )
        else:
            expected_at = (
                datetime.datetime.now(tz=datetime.timezone.utc)
                + datetime.timedelta(days=1)
            ).replace(hour=0, minute=0, second=0, microsecond=0)

        def _coerce_enum(enum_cls, value, default):
            try:
                return enum_cls(value)
            except (ValueError, KeyError):
                return default

        item_schemas.append(
            RequisitionItemCreate(
                item=item["item_id"],
                category=item["category_id"],
                kind=_coerce_enum(ItemType, item.get("kind"), ItemType.MATERIAL),
                quantity=item["quantity"],
                unit_of_measurement_type=_coerce_enum(
                    UnitOfMeasurementType,
                    item.get("unit_of_measurement_type"),
                    UnitOfMeasurementType.UNITS,
                ),
                unit_of_measurement=_coerce_enum(
                    UnitOfMeasurement,
                    item.get("unit_of_measurement"),
                    UnitOfMeasurement.UNITS,
                ),
                expected_at=expected_at,
                description=item.get("description"),
            )
        )

    # LG OJO que el responsable quedo como opciona, ver !!
    responsibles = [responsible_id] if responsible_id is not None else []
    requisition = RequisitionCreate.model_construct(
        project=project_id,
        responsibles=responsibles,
        items=item_schemas,
    )
    return requisition

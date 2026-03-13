import enum
import logging
from collections.abc import Callable

import rapidfuzz.process
import rapidfuzz.utils
import sqlalchemy as sa
from sqlalchemy import Enum
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from lebane.core.models import Base
from lebane.requisition.schemas import RequisitionCreate
from lebane.requisition.schemas.create import RequisitionItemCreate
from lebane.requisition.schemas.types import ItemType
from lebane.user.models import UserInDB
from lebane.user.types import UserState

from .extraction import RequisitionExtracted


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


async def search_project_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> ProjectInDB:
    stmt = sa.select(ProjectInDB).where(
        ProjectInDB.state.not_in((ProjectState.DELETED, ProjectState.CLOSED)),
        ProjectInDB.organization_id == organization_id,
    )
    result = await dbsession.execute(statement=stmt)
    active_projects = {project.name: project for project in result.scalars()}
    match = rapidfuzz.process.extractOne(
        name,
        active_projects.keys(),
        processor=rapidfuzz.utils.default_process,
        score_cutoff=70,
    )
    if match:
        matched_name, _, _ = match
        return active_projects[matched_name]
    return None


async def search_responsible_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> UserInDB:
    stmt = sa.select(UserInDB).where(
        UserInDB.active.is_(True),
        UserInDB.state.in_(
            (UserState.ACTIVE, UserState.CHANGE_PASSWORD_REQUIRED)
        ),
        UserInDB.organization_id == organization_id,
    )
    result = await dbsession.execute(statement=stmt)

    def full_name(user: UserInDB) -> str:
        return " ".join(
            part
            for part in [
                user.name,
                user.second_name,
                user.surname,
                user.second_surname,
            ]
            if part
        )

    active_users = {full_name(user=user): user for user in result.scalars()}
    matched_name, _, _ = rapidfuzz.process.extractOne(
        name,
        active_users.keys(),
        processor=rapidfuzz.utils.default_process,
    )
    return active_users[matched_name]


# LG TODO - Este hace un query por cada item, deberia traer todos una sola vez
async def search_item_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> ItemInDB:
    stmt = sa.select(ItemInDB).where(
        ItemInDB.organization_id == organization_id,
    )
    result = await dbsession.execute(statement=stmt)
    items = {item.name: item for item in result.scalars()}
    matched_name, _, _ = rapidfuzz.process.extractOne(
        name,
        items.keys(),
        processor=rapidfuzz.utils.default_process,
    )
    return items[matched_name]


async def load_requisition(
    extracted_requisition: RequisitionExtracted,
    ldbsessionmaker: Callable[[], AsyncSession],
    organization_id: int,
) -> RequisitionCreate:
    requisition = RequisitionCreate.model_construct(
        criticality=extracted_requisition.criticality
    )
    async with ldbsessionmaker() as dbsession:
        project_indb = None
        if extracted_requisition.project:
            project_indb = await search_project_by_name(
                name=extracted_requisition.project,
                organization_id=organization_id,
                dbsession=dbsession,
            )
        requisition.project = project_indb.id if project_indb else None

        requisition.responsibles = [
            (
                await search_responsible_by_name(
                    name=extracted_requisition.responsibles[0],
                    organization_id=organization_id,
                    dbsession=dbsession,
                )
            ).id
        ]

        requisition.items = []
        for eitem in extracted_requisition.items or []:
            item = await search_item_by_name(
                name=eitem.name,
                organization_id=organization_id,
                dbsession=dbsession,
            )
            requisition.items.append(
                RequisitionItemCreate(
                    description=eitem.description,
                    item=item.id,
                    category=item.category_id,
                    kind=item.kind or "MATERIALES",
                    quantity=eitem.quantity,
                    unit_of_measurement_type=eitem.unit_of_measurement_type,
                    unit_of_measurement=eitem.unit_of_measurement,
                    expected_at=eitem.expected_at,
                )
            )

    return RequisitionCreate.model_validate(requisition.model_dump())

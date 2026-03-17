"""Read-only lookup tools that give the LLM the available projects, items and
users so it can resolve exact IDs before creating a requisition."""
import logging

import sqlalchemy as sa
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy.orm import selectinload

from lebane.user.models import UserInDB
from lebane.user.types import UserState

from .loader import CategoryInDB
from .loader import ItemInDB
from .loader import ProjectInDB
from .loader import ProjectState

logger = logging.getLogger(__name__)


@tool
async def list_available_projects(config: RunnableConfig) -> list[dict]:
    """Retorna los proyectos activos de la organización con su id y nombre.

    Llamá esta tool ANTES de crear una requisición para obtener el project_id
    correcto. Retorna una lista de objetos {id, name}.
    """
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not ldbsessionmaker:
        return []

    async with ldbsessionmaker() as dbsession:
        stmt = sa.select(ProjectInDB).where(
            ProjectInDB.state.not_in(
                (ProjectState.DELETED, ProjectState.CLOSED)
            ),
            ProjectInDB.organization_id == organization_id,
        )
        result = await dbsession.execute(stmt)
        projects = result.scalars().all()

    return [{"id": p.id, "name": p.name} for p in projects]


@tool
async def list_available_items(config: RunnableConfig) -> list[dict]:
    """Retorna todos los ítems disponibles para la organización.

    Llamá esta tool ANTES de crear una requisición para obtener el item_id,
    category_id y kind correctos de cada ítem pedido.
    Retorna una lista de objetos {id, name, category_id, category_name, kind}.
    """
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not ldbsessionmaker:
        return []

    async with ldbsessionmaker() as dbsession:
        stmt = (
            sa.select(ItemInDB)
            .where(ItemInDB.organization_id == organization_id)
            .options(selectinload(ItemInDB.category))
        )
        result = await dbsession.execute(stmt)
        items = result.scalars().all()

    return [
        {
            "id": item.id,
            "name": item.name,
            "category_id": item.category_id,
            "category_name": item.category.name if item.category else None,
            "kind": item.kind,
        }
        for item in items
    ]


@tool
async def list_responsible_users(config: RunnableConfig) -> list[dict]:
    """Retorna los usuarios activos de la organización que pueden ser
    responsables de una requisición.

    Llamá esta tool para obtener el responsible_id correcto.
    Retorna una lista de objetos {id, full_name, email}.
    """
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not ldbsessionmaker:
        return []

    async with ldbsessionmaker() as dbsession:
        stmt = sa.select(UserInDB).where(
            UserInDB.active.is_(True),
            UserInDB.state.in_(
                (UserState.ACTIVE, UserState.CHANGE_PASSWORD_REQUIRED)
            ),
            UserInDB.organization_id == organization_id,
        )
        result = await dbsession.execute(stmt)
        users = result.scalars().all()

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

    return [
        {"id": u.id, "full_name": full_name(u), "email": u.username}
        for u in users
    ]


LOOKUP_TOOLS = [list_available_projects, list_available_items, list_responsible_users]

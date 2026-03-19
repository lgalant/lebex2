"""Write tools for purchase and mutation operations.

Write tools must ask for explicit user confirmation with interrupt()
before performing any mutation.
"""
import logging

import sqlalchemy as sa
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt
from rapidfuzz import fuzz
from sqlalchemy.orm import selectinload

from lebane.client import LebaneClient
from lebane.errors import LebaneError
from lebane.errors import LebaneTimeoutError
from lebane.user.models import UserInDB
from lebane.user.types import UserState

from .loader import ItemInDB
from .loader import ProjectInDB
from .loader import ProjectState
from .loader import build_requisition

logger = logging.getLogger(__name__)

_MIN_SCORE = 60


def _best_match(query: str, candidates: list[tuple]) -> tuple | None:
    """Retorna el candidato con mayor score usando partial_ratio, o None si ninguno supera _MIN_SCORE.

    candidates: lista de (id, name, *extra)
    """
    best = None
    best_score = _MIN_SCORE - 1
    q = query.lower()
    for candidate in candidates:
        name = candidate[1].lower()
        # Containment exacto → score máximo
        score = 100.0 if q in name or name in q else fuzz.partial_ratio(q, name)
        if score > best_score:
            best_score = score
            best = candidate
    return best


def _no_lebane() -> str:
    return "Hubo un problema al conectar con Lebane."


def _full_name(u: UserInDB) -> str:
    return " ".join(p for p in [u.name, u.second_name, u.surname, u.second_surname] if p)


async def _resolve_project(
    dbsession, organization_id: int, description: str
) -> tuple[int, str] | str:
    """Resuelve project_id a partir de una descripción. Retorna (id, name) o un mensaje de error."""
    result = await dbsession.execute(
        sa.select(ProjectInDB).where(
            ProjectInDB.organization_id == organization_id,
            ProjectInDB.state.not_in((ProjectState.DELETED, ProjectState.CLOSED)),
        )
    )
    projects = result.scalars().all()
    match = _best_match(description, [(p.id, p.name) for p in projects])
    if not match:
        return f"No encontré ningún proyecto parecido a '{description}'. Revisá el nombre e intentá de nuevo."
    logger.info("Proyecto resuelto: '%s' → id=%s nombre='%s'", description, match[0], match[1])
    return match[0], match[1]


async def _resolve_responsible(
    dbsession, organization_id: int, description: str
) -> tuple[int, str] | str:
    """Resuelve responsible_id a partir de nombre o email. Retorna (id, name) o un mensaje de error."""
    result = await dbsession.execute(
        sa.select(UserInDB).where(
            UserInDB.organization_id == organization_id,
            UserInDB.active.is_(True),
            UserInDB.state.in_((UserState.ACTIVE, UserState.CHANGE_PASSWORD_REQUIRED)),
        )
    )
    users = result.scalars().all()
    match = _best_match(description, [(u.id, _full_name(u)) for u in users]) or \
            _best_match(description, [(u.id, u.username) for u in users])
    if not match:
        return f"No encontré ningún usuario parecido a '{description}'. Revisá el nombre e intentá de nuevo."
    logger.info("Responsable resuelto: '%s' → id=%s nombre='%s'", description, match[0], match[1])
    return match[0], match[1]


async def _resolve_items(
    dbsession, organization_id: int, items: list[dict]
) -> list[dict] | str:
    """Resuelve item_id, category_id y kind para cada ítem. Retorna lista resuelta o un mensaje de error."""
    result = await dbsession.execute(
        sa.select(ItemInDB)
        .where(ItemInDB.organization_id == organization_id)
        .options(selectinload(ItemInDB.category))
    )
    all_items = result.scalars().all()
    candidates = [(i.id, i.name, i.category_id, i.kind) for i in all_items]

    resolved: list[dict] = []
    unresolved: list[str] = []
    for item in items:
        item_desc = item.get("item_description", "")
        match = _best_match(item_desc, candidates)
        if not match:
            unresolved.append(item_desc)
            continue
        item_id, item_name, category_id, kind = match[0], match[1], match[2], match[3]
        logger.info("Ítem resuelto: '%s' → id=%s nombre='%s'", item_desc, item_id, item_name)
        resolved.append({
            **item,
            "item_id": item_id,
            "category_id": category_id,
            "kind": kind,
            "description": item.get("description") or item_name,
        })

    if unresolved:
        return (
            f"No encontré ítems parecidos a: {', '.join(repr(u) for u in unresolved)}. "
            "Revisá los nombres e intentá de nuevo."
        )
    return resolved


def _item_label(item: dict) -> str:
    name = item.get("description") or item.get("item_description") or f"ítem #{item.get('item_id')}"
    qty = f"{item.get('quantity')} {item.get('unit_of_measurement', '')}".strip()
    date = f" para el {item['delivery_date']}" if item.get("delivery_date") else ""
    return f"  • {qty} de {name}{date}"


@tool
async def create_requisition(
    project_description: str,
    responsible_description: str | None,
    items: list[dict],
    config: RunnableConfig,
) -> str:
    """Crea una Orden de Pedido (requisición) en Lebane para un proyecto.

    Busca automáticamente los IDs de proyecto, responsable e ítems a partir de
    descripciones en lenguaje natural usando búsqueda fuzzy. NO necesitás llamar
    tools previas para resolver IDs.

    Args:
        project_description: Nombre o descripción del proyecto (obligatorio, puede ser aproximado).
        responsible_description: Nombre o email del responsable (puede ser aproximado).
                                  None si no hay responsable.
        items: Lista de ítems a pedir. Cada ítem debe tener:
            - item_description (str): nombre o descripción del ítem (puede ser aproximado)
            - quantity (float): cantidad
            - unit_of_measurement_type (str): tipo de unidad. Valores válidos:
              UNITS, WEIGHT, VOLUME, LENGTH, AREA, OTHER. Default: "UNITS".
            - unit_of_measurement (str): unidad de medida. Valores válidos:
              UNITS, KG, LT, M, M2, OTHER. Default: "UNITS".
            - description (str, opcional): descripción libre adicional
            - delivery_date (str, opcional): fecha YYYY-MM-DD
    """
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not lsessionmaker:
        return _no_lebane()
    if not ldbsessionmaker:
        return "No hay conexión a la base de datos de Lebane."

    async with ldbsessionmaker() as dbsession:
        project_result = await _resolve_project(dbsession, organization_id, project_description)
        if isinstance(project_result, str):
            return project_result
        project_id, project_name = project_result

        responsible_id: int | None = None
        responsible_name: str = "(sin responsable)"
        if responsible_description:
            responsible_result = await _resolve_responsible(dbsession, organization_id, responsible_description)
            if isinstance(responsible_result, str):
                return responsible_result
            responsible_id, responsible_name = responsible_result

        items_result = await _resolve_items(dbsession, organization_id, items)
        if isinstance(items_result, str):
            return items_result
        resolved_items = items_result

    items_text = "\n".join(_item_label(i) for i in resolved_items)
    confirmation = interrupt(
        f"¿Confirmás la siguiente Orden de Pedido?\n"
        f"Proyecto: {project_name}\n"
        f"Responsable: {responsible_name}\n\n"
        f"{items_text}\n\n"
        f"Respondé *SI* para confirmar."
    )
    if str(confirmation).strip().upper() != "SI":
        return "Orden de pedido cancelada."

    requisition = build_requisition(
        project_id=project_id,
        responsible_id=responsible_id,
        items=resolved_items,
    )

    async with lsessionmaker() as lebane_client:
        assert isinstance(lebane_client, LebaneClient)
        try:
            requisition_id = await lebane_client.requisitions.create(data=requisition)
        except LebaneTimeoutError:
            return "La llamada a Lebane tomó demasiado tiempo."
        except LebaneError as exc:
            logger.error(exc.response.text)
            return _no_lebane()

    return f"✅ Orden de Pedido creada exitosamente (ID: {requisition_id})."


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

WRITE_TOOLS = [create_requisition]

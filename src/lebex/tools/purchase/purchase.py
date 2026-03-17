"""Write tools for purchase and mutation operations.

Write tools must ask for explicit user confirmation with interrupt()
before performing any mutation.
"""
import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

from lebane.client import LebaneClient
from lebane.errors import LebaneError
from lebane.errors import LebaneTimeoutError
from lebane.requisition.schemas.types import ItemType
from lebane.requisition.schemas.types import UnitOfMeasurement
from lebane.requisition.schemas.types import UnitOfMeasurementType

from .loader import build_requisition

logger = logging.getLogger(__name__)

_KIND_VALUES = ", ".join(f'"{v.value}"' for v in ItemType)
_UOM_TYPE_VALUES = ", ".join(f'"{v.value}"' for v in UnitOfMeasurementType)
_UOM_VALUES = ", ".join(f'"{v.value}"' for v in UnitOfMeasurement)

_CREATE_REQUISITION_DOC = f"""Crea una Orden de Pedido (requisición) en Lebane para un proyecto.

IMPORTANTE: Antes de llamar esta tool debés:
1. Llamar a `list_available_projects` para obtener el project_id correcto.
2. Llamar a `list_available_items` para obtener item_id, category_id y kind
   de cada ítem pedido.
3. Llamar a `list_responsible_users` para obtener el responsible_id.

Args:
    project_id: ID del proyecto (obtenido de list_available_projects).
                None si no aplica proyecto.
    responsible_id: ID del usuario responsable (de list_responsible_users).
                     None si no hay responsable disponible.
    items: Lista de ítems a pedir. Cada ítem debe tener:
        - item_id (int): ID del ítem (de list_available_items)
        - category_id (int): ID del rubro (de list_available_items)
        - kind (str): tipo de ítem. Valores válidos: {_KIND_VALUES}
        - quantity (float): cantidad
        - unit_of_measurement_type (str): tipo de unidad. Valores válidos:
          {_UOM_TYPE_VALUES}. Default: "{UnitOfMeasurementType.UNITS.value}".
        - unit_of_measurement (str): unidad de medida. Valores válidos:
          {_UOM_VALUES}. Default: "{UnitOfMeasurement.UNITS.value}".
        - description (str, opcional): descripción libre
        - delivery_date (str, opcional): fecha YYYY-MM-DD
"""


def _no_lebane() -> str:
    return "Hubo un problema al conectar con Lebane."


@tool
async def create_requisition(
    project_id: int | None,
    responsible_id: int | None,
    items: list[dict],
    config: RunnableConfig,
) -> str:
    """Crea una Orden de Pedido (requisición) en Lebane para un proyecto.

    IMPORTANTE: Antes de llamar esta tool debés:
    1. Llamar a `list_available_projects` para obtener el project_id correcto.
    2. Llamar a `list_available_items` para obtener item_id, category_id y kind
       de cada ítem pedido.
    3. Llamar a `list_responsible_users` para obtener el responsible_id.

    Args:
        project_id: ID del proyecto (obtenido de list_available_projects).
                    None si no aplica proyecto.
        responsible_id: ID del usuario responsable (de list_responsible_users).
                         None si no hay responsable disponible.
        items: Lista de ítems a pedir. Cada ítem debe tener:
            - item_id (int): ID del ítem (de list_available_items)
            - category_id (int): ID del rubro (de list_available_items)
            - kind (str): tipo de ítem. Valores válidos: {_KIND_VALUES}
            - quantity (float): cantidad
            - unit_of_measurement_type (str): tipo de unidad. Valores válidos:
              {_UOM_TYPE_VALUES}. Default: "{UnitOfMeasurementType.UNITS.value}".
            - unit_of_measurement (str): unidad de medida. Valores válidos:
              {_UOM_VALUES}. Default: "{UnitOfMeasurement.UNITS.value}".
            - description (str, opcional): descripción libre
            - delivery_date (str, opcional): fecha YYYY-MM-DD
    """
    lsessionmaker = config["configurable"].get("lsessionmaker")

    if not lsessionmaker:
        return _no_lebane()

    # Build a human-readable summary for confirmation
    def _item_label(item: dict) -> str:
        name = item.get("description") or f"ítem #{item.get('item_id')}"
        qty = f"{item.get('quantity')} {item.get('unit_of_measurement', '')}".strip()
        date = f" para el {item['delivery_date']}" if item.get("delivery_date") else ""
        return f"  • {qty} de {name}{date}"

    items_text = "\n".join(_item_label(i) for i in items)
    confirmation = interrupt(
        f"¿Confirmás la siguiente Orden de Pedido (proyecto ID: {project_id})?\n\n"
        f"{items_text}\n\n"
        f"Respondé *SI* para confirmar."
    )
    if str(confirmation).strip().upper() != "SI":
        return "Orden de pedido cancelada."

    requisition = build_requisition(
        project_id=project_id,
        responsible_id=responsible_id,
        items=items,
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

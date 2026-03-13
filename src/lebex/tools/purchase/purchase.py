"""Write tools for purchase and mutation operations.

Write tools must ask for explicit user confirmation with interrupt()
before performing any mutation.
"""
import logging
import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

from lebane.client import LebaneClient
from lebane.errors import LebaneError
from lebane.errors import LebaneTimeoutError

from .loader import load_requisition
from .extraction import RequisitionExtracted, RequisitionItemExtracted

logger = logging.getLogger(__name__)


def _no_lebane() -> str:
    return "Hubo un problema al conectar con Lebane."


@tool
async def create_requisition(
    project_name: str,
    items: list[dict],
    config: RunnableConfig,
) -> str:
    """Crea una Orden de Pedido (requisición) en Lebane para un proyecto.

    Usá esta tool cuando el usuario quiera pedir materiales, insumos o servicios
    para un proyecto. Antes de llamarla, asegurate de tener:
    - project_name: nombre del proyecto destino
    - items: lista de ítems, cada uno con:
        - description (str): descripción del material o servicio
        - quantity (float): cantidad
        - unit (str): unidad de medida (ej: "m³", "cajas", "unidades")
        - delivery_date (str, opcional): fecha de entrega deseada en formato YYYY-MM-DD

    Siempre pedí confirmación al usuario antes de crear la orden.

    Args:
        project_name: Nombre del proyecto para el cual se hace el pedido.
        items: Lista de ítems a pedir, cada uno con description, quantity, unit y
               opcionalmente delivery_date.
    """
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not lsessionmaker or not ldbsessionmaker:
        return _no_lebane()

    # Build a human-readable summary for confirmation
    items_text = "\n".join(
        f"  • {item.get('quantity')} {item.get('unit')} de {item.get('description')}"
        + (f" para el {item.get('delivery_date')}" if item.get("delivery_date") else "")
        for item in items
    )
    confirmation = interrupt(
        f"¿Confirmás la siguiente Orden de Pedido para *{project_name}*?\n\n"
        f"{items_text}\n\n"
        f"Respondé *SI* para confirmar."
    )
    if str(confirmation).strip().upper() != "SI":
        return "Orden de pedido cancelada."

    # Map tool args → RequisitionExtracted so we can reuse load_requisition
    extracted = RequisitionExtracted(
        project=project_name,
        items=[
            RequisitionItemExtracted(
                name=item.get("description", "GENERAL"),
                description=item.get("description"),
                quantity=item.get("quantity", 0),
                expected_at=(
                    datetime.datetime.fromisoformat(item["delivery_date"]).replace(
                        tzinfo=datetime.timezone.utc,
                        hour=0, minute=0, second=0, microsecond=0,
                    )
                    if item.get("delivery_date")
                    else (
                        datetime.datetime.now(tz=datetime.timezone.utc)
                        + datetime.timedelta(days=1)
                    ).replace(hour=0, minute=0, second=0, microsecond=0)
                ),
            )
            for item in items
        ],
    )

    requisition = await load_requisition(
        extracted_requisition=extracted,
        ldbsessionmaker=ldbsessionmaker,
        organization_id=organization_id,
    )

    async with lsessionmaker() as lebane_client:
        assert isinstance(lebane_client, LebaneClient)
        try:
            requisition = await lebane_client.requisitions.create(data=requisition)
        except LebaneTimeoutError:
            return "La llamada a Lebane tomó demasiado tiempo."
        except LebaneError as exc:
            logger.error(exc.response.text)
            return _no_lebane()

    return f"✅ Orden de Pedido creada exitosamente para el proyecto *{project_name}*."


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

WRITE_TOOLS = [create_requisition]

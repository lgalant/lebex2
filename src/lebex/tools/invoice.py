import enum
import logging
import urllib.parse
from typing import Optional

import httpx
import rapidfuzz.process
import rapidfuzz.utils
import sqlalchemy as sa
from jinja2 import Template
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt
from sqlalchemy import Enum
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from lebex.utils.graph import get_recent_human_messages


logger = logging.getLogger(__name__)


class InvoiceMessage(AIMessage): ...


INVOICE_TEMPLATE = Template("""Las facturas se cargaron con éxito:

*Factura #{{ numero }}*

Organización: {{ organizacion.nombre }}

*Artículos:*
{% for it in items %}- {{ it.nombre }} (Cantidad: {{ it.cantidad }})
{% endfor %}""")  # noqa: E501


def is_document_uri(text: str):
    return (
        text.endswith(".pdf")
        or text.endswith(".jpg")
        or text.endswith(".jpeg")
    )


class ProjectState(enum.StrEnum):
    APPROVED = "APROBADO"
    CLOSED = "CERRADO"
    COMPLETED = "COMPLETADO"
    DELETED = "ELIMINADO"
    STARTED = "INICIADO"
    PRE_STARTED = "PRE_INICIADO"


class Base(DeclarativeBase):
    pass


class ProjectInDB(Base):
    __tablename__ = "proyecto"

    id: Mapped[Optional[int]] = mapped_column(Integer, primary_key=True)
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


async def search_project_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> ProjectInDB | None:
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


async def create_provider_invoice_node(state, config):
    """Create Provider Invoice on Lebane"""
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker or not lsessionmaker:
        logger.warning(
            "No Lebane sessionmaker configured - db: %s http: %s",
            bool(lsessionmaker),
            bool(ldbsessionmaker),
        )
        return {
            "messages": AIMessage(
                content="Hubo un problema al conectar con Lebane."
            )
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    recent_human_messges = get_recent_human_messages(state["messages"], k=2)
    attachment_url = None
    projectindb = None
    for human_message in recent_human_messges:
        if is_document_uri(text=str(human_message.content)):
            attachment_url = str(human_message.content)
        else:
            async with ldbsessionmaker() as dbsession:
                projectindb = await search_project_by_name(
                    name=str(human_message.content),
                    organization_id=organization_id,
                    dbsession=dbsession,
                )

    if not attachment_url:
        return {
            "messages": [
                AIMessage(
                    content="Falta el documento de la factura, "
                    "¿podrías enviarlo?\n"
                    "Puedes elegir el proyecto enviando: "
                    "`factura <proyecto>`.\n"
                    "Si no lo tienes ahora, podemos continuar "
                    "con otra consulta."
                )
            ]
        }

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url=attachment_url)
            response.raise_for_status()
        except httpx.ReadTimeout:
            return {
                "messages": AIMessage(
                    content="La llamada para descargar el "
                    "archivo tomo demasiado tiempo."
                )
            }
        except httpx.HTTPError as exc:
            logger.error(exc.response.text)
            return {
                "messages": AIMessage(
                    content="Hubo un problema al descargar el documento."
                )
            }

        attachment_content = response.content
        attachment_mimetype = response.headers["Content-Type"]
        attachment_name = urllib.parse.unquote(
            attachment_url.rsplit("/", 1)[-1]
        )

    params = {"tipoComprobante": "FACTURA"}
    if projectindb is not None:
        params["proyectoId"] = projectindb.id

    async with lsessionmaker() as lebane_client:
        lebane_client = lebane_client._session
        assert isinstance(lebane_client, httpx.AsyncClient)

        try:
            response = await lebane_client.post(
                "/orden-de-pago/upload-imported-doc",
                params=params,
                files={
                    "file": (
                        attachment_name,
                        attachment_content,
                        attachment_mimetype,
                    )
                },
                timeout=20,
            )
            response.raise_for_status()
        except httpx.ReadTimeout:
            return {
                "messages": [
                    AIMessage(
                        content="La llamada a Lebane tomo demasiado tiempo."
                    )
                ]
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                logger.error(exc.response.text)
                return {
                    "messages": [
                        AIMessage(
                            content="Hubo un problema al conectar con Lebane."
                        )
                    ]
                }

        data = response.json()

    message = "\n------------------------------".join(
        INVOICE_TEMPLATE.render(**invoice)
        for invoice in (data.get("facturas", []) or [])
        if invoice
    )
    if "errores" in data and data["errores"]:
        if message:
            message += "\n\n"
        message += "*Errores:*\n"
        message += "\n".join(
            error for error in (data.get("errores", []) or []) if error
        )

    if response.status_code == 422:
        return {"messages": [AIMessage(content=message)]}

    return {"messages": [InvoiceMessage(content=message)]}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool
async def create_invoice(
    url: str,
    project_name: str | None,
    config: RunnableConfig,
) -> str:
    """Crea una factura en Lebane a partir de una URL de documento (PDF, JPG, JPEG).

    Usá esta tool cuando el usuario quiera registrar una factura enviando
    un archivo o URL. Antes de llamarla, asegurate de tener:
    - url: URL del documento de la factura
    - project_name: nombre del proyecto asociado (opcional)

    Siempre pedí confirmación al usuario antes de crear la factura.

    Args:
        url: URL del documento de la factura (PDF, JPG o JPEG).
        project_name: Nombre del proyecto al que pertenece la factura (opcional).
    """
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")

    if not lsessionmaker:
        return "Hubo un problema al conectar con Lebane."

    confirmation = interrupt(
        f"¿Confirmás que querés registrar la siguiente factura?\n\n"
        f"  • URL: {url}\n"
        + (f"  • Proyecto: {project_name}\n" if project_name else "")
        + "\nRespondé *SI* para confirmar."
    )
    if str(confirmation).strip().upper() != "SI":
        return "Creación de factura cancelada."

    # Resolve project id if name provided
    params: dict = {"tipoComprobante": "FACTURA"}
    if project_name and ldbsessionmaker and organization_id:
        async with ldbsessionmaker() as dbsession:
            projectindb = await search_project_by_name(
                name=project_name,
                organization_id=organization_id,
                dbsession=dbsession,
            )
        if projectindb is not None:
            params["proyectoId"] = projectindb.id

    # Download the document
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url=url)
            response.raise_for_status()
        except httpx.ReadTimeout:
            return "La descarga del archivo tomó demasiado tiempo."
        except httpx.HTTPError as exc:
            logger.error(str(exc))
            return "Hubo un problema al descargar el documento."

        attachment_content = response.content
        attachment_mimetype = response.headers.get("Content-Type", "application/octet-stream")
        attachment_name = urllib.parse.unquote(url.rsplit("/", 1)[-1])

    # Post to Lebane
    async with lsessionmaker() as lebane_client:
        session = lebane_client._session
        assert isinstance(session, httpx.AsyncClient)
        try:
            response = await session.post(
                "/orden-de-pago/upload-imported-doc",
                params=params,
                files={
                    "file": (
                        attachment_name,
                        attachment_content,
                        attachment_mimetype,
                    )
                },
                timeout=20,
            )
            response.raise_for_status()
        except httpx.ReadTimeout:
            return "La llamada a Lebane tomó demasiado tiempo."
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                logger.error(exc.response.text)
                return "Hubo un problema al conectar con Lebane."

    data = response.json()
    message = "\n------------------------------".join(
        INVOICE_TEMPLATE.render(**invoice)
        for invoice in (data.get("facturas", []) or [])
        if invoice
    )
    if "errores" in data and data["errores"]:
        if message:
            message += "\n\n"
        message += "*Errores:*\n"
        message += "\n".join(
            error for error in (data.get("errores", []) or []) if error
        )

    return message or "✅ Factura procesada."


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

WRITE_TOOLS = [create_invoice]

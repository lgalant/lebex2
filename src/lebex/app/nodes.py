import itertools
import logging

import babel.dates
import babel.numbers
import httpx
import jinja2
import more_itertools
import rapidfuzz.process
import rapidfuzz.utils
import sqlalchemy as sa
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from lebex.utils.graph import get_last_user_message

from .inference import InsightClassifier
from .types import InsightState


logger = logging.getLogger(__file__)


class Base(DeclarativeBase):
    pass


class SupplierInDB(Base):
    __tablename__ = "proyecto_proveedor"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column("nombre", sa.String, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        "organizacion_id", sa.Integer, nullable=True
    )


async def search_supplier_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> SupplierInDB | None:
    stmt = sa.select(SupplierInDB).where(
        SupplierInDB.organization_id == organization_id,
    )
    result = await dbsession.execute(statement=stmt)
    active = {row.name: row for row in result.scalars()}
    match = rapidfuzz.process.extractOne(
        name,
        active.keys(),
        processor=rapidfuzz.utils.default_process,
        score_cutoff=70,
    )
    if match:
        matched_name, _, _ = match
        return active[matched_name]
    return None


logger = logging.getLogger(__file__)


TABULAR_TEMPLATE = jinja2.Template(
    """{% for row in rows %}{% for key, value in row.items() -%}
*{{ key }}:* {{ value }}
{% endfor %}{% if not loop.last %}------------------------------{% endif %}
{% endfor %}"""  # noqa: E501
)


def monospace(*, text):
    return f"```\n{text}\n```"


def render_messages_from_data(data: list) -> list[AIMessage]:
    """Render up to 100 records in chunks of 10"""
    return [
        AIMessage(
            content=[
                {"text": TABULAR_TEMPLATE.render(rows=chunk)}
                for chunk in more_itertools.chunked(
                    iterable=itertools.islice(data, 100), n=10
                )
            ]
        )
    ]


def classify_question_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    #print("*** Classifying question with state: ", state["messages"])   
    print("*** Classify question node (insight), last user message: ", get_last_user_message(messages=state["messages"]))   
    print("*agentic", state.get("agentic", False))
    if state.get("agentic", False):
        return {"insight_label": "insight_agent"}

    user_message = get_last_user_message(messages=state["messages"])
    assert isinstance(user_message, HumanMessage)

    classifier = InsightClassifier()
    label = classifier.predict(feature=str(user_message.content))
    print("*** Classifying question predicted: ", label)  
    return {"insight_label": label}


async def get_cash_position_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    c.numero_de_cuenta AS Cuenta,
                    SUM(
                        CASE
                            WHEN m.nombre = 'USD' THEN
                                CASE WHEN c.id = fdpb.caja_origen_id
                                     THEN -fdpb.monto_moneda_extranjera
                                     ELSE  fdpb.monto_moneda_extranjera
                                END
                            ELSE
                                CASE WHEN c.id = fdpb.caja_origen_id
                                     THEN -fdpb.monto_moneda_local
                                     ELSE  fdpb.monto_moneda_local
                                END
                        END
                    ) AS monto,
                    m.nombre AS moneda,
                    'Caja' AS tipo
                FROM forma_de_pago_base fdpb
                JOIN caja c
                    ON c.id = fdpb.caja_origen_id
                    OR c.id = fdpb.caja_destino_id
                JOIN moneda m
                    ON m.id = c.moneda_id
                    WHERE fdpb.organizacion_id = :organization_id
                GROUP BY c.id, c.numero_de_cuenta, m.nombre

                UNION ALL

                SELECT
                    cb.numero_de_cuenta AS Cuenta,
                    SUM(
                        CASE
                            WHEN m.nombre = 'USD' THEN
                                CASE
                                WHEN cb.id = fdpb.cuenta_bancaria_origen_id
                                     THEN -fdpb.monto_moneda_extranjera
                                     ELSE  fdpb.monto_moneda_extranjera
                                END
                            ELSE
                                CASE
                                WHEN cb.id = fdpb.cuenta_bancaria_origen_id
                                     THEN -fdpb.monto_moneda_local
                                     ELSE  fdpb.monto_moneda_local
                                END
                        END
                    ) AS monto,
                    m.nombre AS moneda,
                    'Banco' AS tipo
                FROM forma_de_pago_base fdpb
                JOIN cuenta_bancaria cb
                    ON cb.id = fdpb.cuenta_bancaria_origen_id
                    OR cb.id = fdpb.cuenta_bancaria_destino_id
                JOIN moneda m
                    ON m.id = cb.moneda_id
                WHERE fdpb.organizacion_id = :organization_id
                GROUP BY cb.id, cb.numero_de_cuenta, m.nombre

                ORDER BY tipo, moneda
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Cuenta": row[0],
            "Tipo": row[3],
            "Total": babel.numbers.format_currency(
                number=row[1],
                currency=row[2],
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_clients_with_debt_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    cli.nombre AS nombre,
                    cli.apellido AS apellido,
                    m.iso AS moneda,
                    SUM(t.total_saldo) AS monto,
                    cli.id AS cliente_id
                FROM (
                    -- Cuotas (con saldo > 0 y vencidas)
                    SELECT
                        ci.cliente_proyecto_item_id AS cpi_id,
                        ci.moneda_id AS moneda_id,
                        ci.saldo AS total_saldo
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN cuota_ingreso ci
                        ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda
                        ON moneda.id = ci.moneda_id
                    LEFT JOIN cliente_proyecto cp
                        ON cp.id = cpi.cliente_proyecto_id
                    LEFT JOIN proyecto proj
                        ON proj.id = cp.proyecto_id
                    WHERE ci.fecha < CURDATE()
                      AND ci.saldo > 0
                      AND proj.organizacion_id = :organization_id
                    UNION ALL
                    -- Contados (1 sola cuota) con saldo > 0 y vencidos
                    SELECT
                        cpi.id AS cpi_id,
                        cpi.moneda_id AS moneda_id,
                        cpi.saldo AS total_saldo
                    FROM cliente_proyecto_item cpi
                    WHERE cpi.cantidad_cuotas = 0
                      AND cpi.fecha_primera_cuota < CURDATE()
                      AND cpi.saldo > 0
                ) AS t
                JOIN cliente_proyecto_item cpi
                    ON cpi.id = t.cpi_id
                JOIN cliente_proyecto cp
                    ON cp.id = cpi.cliente_proyecto_id
                JOIN cliente cli
                    ON cli.id = cp.cliente_id
                JOIN moneda m
                    ON m.id = t.moneda_id
                WHERE cli.organizacion_id = :organization_id
                GROUP BY cli.id, cli.nombre, cli.apellido, m.iso
                ORDER BY cli.apellido, cli.nombre,cli.id, m.iso;
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Nombre": row[0],
            "Apellido": row[1],
            "Monto": babel.numbers.format_currency(
                number=row[3],
                currency=row[2] or "ARS",
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_clients_balance_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    # TODO: interesting columns: project, client_type
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    CONCAT(c.nombre, ' ', c.apellido) AS cliente,
                    SUM(
                        CASE WHEN m.id = 1
                        THEN cpi.saldo ELSE 0
                        END
                    ) AS saldo_extranjera,
                    SUM(
                        CASE WHEN m.id != 1
                        THEN cpi.saldo ELSE 0
                        END
                    ) AS saldo_local,
                    MAX(CASE WHEN m.id = 1 THEN NULL ELSE m.nombre END)
                FROM cliente_proyecto_item cpi
                JOIN cliente_proyecto cp ON cpi.cliente_proyecto_id = cp.id
                JOIN cliente c ON cp.cliente_id = c.id
                JOIN moneda m ON m.id = cpi.moneda_id
                WHERE cpi.saldo > 0
                AND c.organizacion_id = :organization_id
                GROUP BY c.id
                ORDER BY
                    SUM(CASE WHEN m.id = 1 THEN cpi.saldo ELSE 0 END) DESC,
                    SUM(CASE WHEN m.id != 1 THEN cpi.saldo ELSE 0 END) DESC
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Cliente": row[0],
            "Saldo USD": babel.numbers.format_currency(
                number=row[1],
                currency="USD",
                locale="es_AR",
            ),
            "Saldo Local": babel.numbers.format_currency(
                number=row[2],
                currency=row[3] or "ARS",
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_clients_with_debt_by_project_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    CONCAT(c.nombre, ' ', c.apellido) AS cliente,
                    p.nombre,
                    SUM(
                        CASE WHEN m.id = 1
                        THEN cpi.saldo ELSE 0
                        END
                    ) AS saldo_extranjera,
                    SUM(
                        CASE WHEN m.id != 1
                        THEN cpi.saldo ELSE 0
                        END
                    ) AS saldo_local,
                    MAX(CASE WHEN m.id = 1 THEN NULL ELSE m.nombre END),
                    SUM(cpi.saldo)
                FROM cliente_proyecto_item cpi
                JOIN cliente_proyecto cp ON cpi.cliente_proyecto_id = cp.id
                JOIN proyecto p ON p.id = cp.proyecto_id
                JOIN cliente c ON cp.cliente_id = c.id
                JOIN moneda m ON m.id = cpi.moneda_id
                WHERE cpi.saldo > 0
                AND c.organizacion_id = :organization_id
                GROUP BY c.id, p.id
                ORDER BY
                    SUM(CASE WHEN m.id = 1 THEN cpi.saldo ELSE 0 END) DESC,
                    SUM(CASE WHEN m.id != 1 THEN cpi.saldo ELSE 0 END) DESC
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Cliente": row[0],
            "Proyecto": row[1],
            "Saldo USD": babel.numbers.format_currency(
                number=row[2],
                currency="USD",
                locale="es_AR",
            ),
            "Saldo Local": babel.numbers.format_currency(
                number=row[3],
                currency=row[4] or "ARS",
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_pending_invoices_by_supplier_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    # TODO: same as get_expected_payments_today
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    op.tipo AS tipo,
                    rs.nombre AS proveedor,
                    CONCAT(op.punto_de_venta, "-", op.numero) AS numero,
                    op.monto_totalapagar AS monto,
                    moneda.iso AS moneda,
                    op.fecha_vencimiento_pago,
                    op.saldo AS saldo
                FROM orden_de_pago op
                LEFT JOIN moneda
                    ON moneda.id = op.moneda_id
                LEFT JOIN razon_social rs
                    ON rs.id = op.razon_social_proveedor_id
                WHERE op.tipo IN ('COMPROBANTE', 'FACTURA', 'NOTA_DE_DEBITO')
                  AND op.estado <> 'PAGADO'
                  AND op.organizacion_id = :organization_id
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Proveedor": row[1] or "",
            "Tipo": row[0] or "",
            "Nro.": row[2] or "",
            "Fecha de Vencimiento": babel.dates.format_date(
                row[5],
                locale="es_AR",
            )
            or "",
            "Monto": babel.numbers.format_currency(
                number=row[3],
                currency=row[4],
                locale="es_AR",
            )
            or "",
            "Saldo": babel.numbers.format_currency(
                number=row[6],
                currency=row[4],
                locale="es_AR",
            )
            or "",
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_income_projection_next_month_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    moneda_iso,
                    SUM(total_saldo) AS total_saldo
                FROM (
                    -- Cuotas ingreso
                    SELECT
                        SUM(ci.saldo) AS total_saldo,
                        moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    left join cuota_ingreso ci
                    ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE
                        cpi.cantidad_cuotas > 0 and
                        ci.saldo > 0
                        AND (ci.fecha BETWEEN
                            CURDATE() AND
                            DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                        AND cpi.organizacion_id = :organization_id
                    UNION ALL
                    -- Contados
                    SELECT
                        SUM(cpi.saldo) AS total_saldo,
                        moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE
                        cpi.cantidad_cuotas = 0
                        AND (cpi.fecha_primera_cuota BETWEEN
                            CURDATE() AND
                            DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                        AND cpi.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                ) AS t
                GROUP BY moneda_iso
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Monto": babel.numbers.format_currency(
                number=row[1],
                currency=row[0],
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_investor_clients_by_project_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    p.nombre,
                    CONCAT(c.nombre, ' ', c.apellido) AS cliente
                FROM cliente_proyecto cp
                JOIN proyecto p
                    ON p.id = cp.proyecto_id
                JOIN cliente c
                    ON cp.cliente_id = c.id
                WHERE p.organizacion_id = :organization_id
                AND cp.tipo_cliente = "INVERSOR"
                ORDER BY p.id, cp.id
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {"Proyecto": row[0], "Inversor": row[1]}

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_new_prospects_this_week_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                select
                    prospecto.nombre,
                    prospecto.apellido,
                    prospecto.correo_electronico,
                    prospecto.telefono,
                    usuario.nombre as responsable
                from prospecto left join
                    usuario on usuario.id = prospecto.responsable_id
                WHERE prospecto.organizacion_id = :organization_id and
                    (fecha_de_creacion BETWEEN
                    DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                    DATE_ADD(DATE_SUB(
                        CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY),
                             INTERVAL 6 DAY
                    ))
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Prospecto": (row[0] or "") + " " + (row[1] or ""),
            "Teléfono": row[3],
            "Email": row[2],
            "Responsable": row[4],
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_expected_payments_today_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    # TODO: same as get_expected_payments_today
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    p.nombre,
                    op.numero,
                    op.fecha_vencimiento_pago,
                    op.monto,
                    m.nombre
                FROM orden_de_pago op
                JOIN proveedor_razones_sociales_proveedor rsp
                    ON op.razon_social_proveedor_id =
                        rsp.razones_sociales_proveedor_id
                JOIN proveedor p
                    ON rsp.proveedor_id = p.id
                JOIN moneda m
                    ON m.id = op.moneda_id
                WHERE p.organizacion_id = :organization_id
                    AND op.tipo = 'FACTURA'
                    AND op.estado = 'SOLICITADO'
                    AND fecha_vencimiento_pago <= CURDATE()
                GROUP BY p.id, op.id
                ORDER BY
                    p.id,
                    op.fecha_vencimiento_pago ASC,
                    m.id ASC,
                    op.monto DESC
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Proveedor": row[0],
            "Nro. Factura": row[1],
            "Fecha de Vencimiento": babel.dates.format_date(
                row[2],
                locale="es_AR",
            ),
            "Monto": babel.numbers.format_currency(
                number=row[3],
                currency=row[4],
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_income_projection_this_week_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    moneda_iso,
                    SUM(total_saldo) AS total_saldo
                FROM (
                    -- Cuotas ingreso
                    SELECT
                        SUM(ci.saldo) AS total_saldo,
                        moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    left join cuota_ingreso ci
                        ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda ON moneda.id = ci.moneda_id
                    LEFT JOIN cliente_proyecto cp
                        ON cp.id = cpi.cliente_proyecto_id
                    LEFT JOIN proyecto proj ON proj.id = cp.proyecto_id
                    WHERE
                        (ci.fecha BETWEEN
                            DATE_SUB(
                                CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                            DATE_ADD(DATE_SUB(
                                CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY),
                                     INTERVAL 6 DAY))
                            and proj.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                    UNION ALL
                    -- Contados
                    SELECT
                        SUM(cpi.saldo) AS total_saldo,
                        moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE
                        cpi.cantidad_cuotas = 0
                        AND (cpi.fecha_primera_cuota BETWEEN
                            DATE_SUB(
                                CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                            DATE_ADD(DATE_SUB(
                                CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY),
                                     INTERVAL 6 DAY))
                        AND cpi.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                ) AS t
                GROUP BY moneda_iso
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Monto": babel.numbers.format_currency(
                number=row[1],
                currency=row[0],
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_pending_invoices_by_supplier_this_week_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    op.tipo AS tipo,
                    rs.nombre AS proveedor,
                    CONCAT(op.punto_de_venta, "-", op.numero) AS numero,
                    op.monto_totalapagar AS monto,
                    moneda.iso AS moneda,
                    op.fecha_vencimiento_pago,
                    op.saldo AS saldo
                FROM orden_de_pago op
                LEFT JOIN moneda
                    ON moneda.id = op.moneda_id
                LEFT JOIN razon_social rs
                    ON rs.id = op.razon_social_proveedor_id
                WHERE op.fecha_vencimiento_pago BETWEEN
                          DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)
                      AND DATE_ADD(
                          DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY),
                          INTERVAL 6 DAY
                      )
                  AND op.tipo IN ('COMPROBANTE', 'FACTURA', 'NOTA_DE_DEBITO')
                  AND op.estado <> 'PAGADO'
                  AND op.organizacion_id = :organization_id
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Proveedor": row[1],
            "Tipo": row[0],
            "Nro.": row[2],
            "Fecha de Vencimiento": babel.dates.format_date(
                row[5],
                locale="es_AR",
            ),
            "Monto": babel.numbers.format_currency(
                number=row[3],
                currency=row[4],
                locale="es_AR",
            ),
            "Saldo": babel.numbers.format_currency(
                number=row[6],
                currency=row[4],
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_expenses_by_project_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN egreso.moneda_id <> 1
                                THEN egreso.monto_moneda_local
                            ELSE 0
                        END
                    ) AS total_moneda_local,
                    SUM(
                        CASE
                            WHEN egreso.moneda_id = 1
                                THEN egreso.monto_moneda_extranjera
                            ELSE 0
                        END
                    ) AS total_moneda_extranjera,
                    proyecto.nombre
                FROM egreso_generico egreso
                LEFT JOIN proyecto
                    ON proyecto.id = egreso.proyecto_id
                    WHERE proyecto.organizacion_id = :organization_id
                GROUP BY proyecto.nombre
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Proyecto": row[2],
            "Total USD": babel.numbers.format_currency(
                number=row[1],
                currency="USD",
                locale="es_AR",
            ),
            "Total Local": babel.numbers.format_currency(
                number=row[0],
                currency="ARS",
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_supplier_summary_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    print("*** Getting supplier summary with state: ", state["messages"])
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker or not lsessionmaker:
        logger.warning(
            "No Lebane sessionmaker configured - db: %s http: %s",
            bool(lsessionmaker),
            bool(ldbsessionmaker),
        )
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    user_message = get_last_user_message(messages=state["messages"])
    assert user_message is not None

    organization_id = state["lebane_user_context"]["organization_id"]
    async with ldbsessionmaker() as dbsession:
        supplier_indb = await search_supplier_by_name(
            name=str(user_message.content),
            organization_id=organization_id,
            dbsession=dbsession,
        )

    if supplier_indb is None:
        return {
            "messages": [
                AIMessage(
                    content="Intenta enviando: "
                    "`reporte de proveedor, <nombre>`"
                )
            ]
        }

    async with lsessionmaker() as client:
        client = client._session
        assert isinstance(client, httpx.AsyncClient)

        try:
            response = await client.get(
                url="/proyecto-proveedor/general/cuentas-corrientes",
                params={
                    "pagina": "0",
                    "cantidad": "50",
                    "search": supplier_indb.name,
                },
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
            logger.error(exc.response.text)
            return {
                "messages": [
                    AIMessage(
                        content="Hubo un problema al conectar con Lebane."
                    )
                ]
            }

    result = response.json().get("content", [])

    def to_usd(value):
        return babel.numbers.format_currency(
            number=value,
            currency="USD",
            locale="es_AR",
        )

    def to_ars(value):
        return babel.numbers.format_currency(
            number=value,
            currency="ARS",
            locale="es_AR",
        )

    def humanize(row):
        totalizador = row["totalizador"]
        data = {
            "Proveedor": row["proveedor"]["nombre"],
            "Cuenta Corriente": row["nombre"],
            "Proyecto": row["proyectoNombre"],
        }
        if any(
            [
                totalizador[key] > 0
                for key in totalizador.keys()
                if "local" in key.lower()
            ]
        ):
            data.update(
                {
                    "Presupuesto Base Local": to_ars(
                        totalizador["presupuestoBaseLocal"]
                    ),
                    "Saldo Base Local": to_ars(totalizador["saldoBaseLocal"]),
                    "Pagado Total Local": to_ars(
                        totalizador["pagadoBaseLocal"]
                    ),
                    "Deuda Final Local": to_ars(
                        totalizador["deudaFinalLocal"]
                    ),
                }
            )
        if any(
            [
                totalizador[key] > 0
                for key in totalizador.keys()
                if "extranjera" in key.lower()
            ]
        ):
            data.update(
                {
                    "Presupuesto Base Extranjera": to_usd(
                        totalizador["presupuestoBaseExtranjera"]
                    ),
                    "Saldo Base Extranjera": to_usd(
                        totalizador["saldoBaseExtranjera"]
                    ),
                    "Pagado Total Extranjera": to_usd(
                        totalizador["pagadoBaseExtranjera"]
                    ),
                    "Deuda Final Extranjera": to_usd(
                        totalizador["deudaFinalExtranjera"]
                    ),
                }
            )
        if len(data) == 3:
            data["Nota"] = "No hay informacion financiera disponible."
        return data

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_avg_sale_price_per_sqm_by_project_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    p.nombre,
                    ROUND(
                        SUM(a.total_usd) / NULLIF(SUM(m.m2), 0), 2
                    ) AS usd_por_m2
                FROM proyecto p
                JOIN cliente_proyecto cp
                  ON cp.proyecto_id = p.id
                LEFT JOIN (
                    -- total en USD por cliente_proyecto
                    SELECT  cpi.cliente_proyecto_id,
                            SUM(
                              CASE
                                WHEN mon.iso = 'USD' THEN cpi.monto
                                ELSE cpi.monto / NULLIF(cpi.tipo_de_cambio, 0)
                              END
                            ) AS total_usd
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda mon ON mon.id = cpi.moneda_id
                    GROUP BY cpi.cliente_proyecto_id
                ) a  ON a.cliente_proyecto_id = cp.id
                LEFT JOIN (
                    -- metros por cliente_proyecto
                    -- (sumar UNA sola vez por unidad)
                    SELECT  cpuni.cliente_proyecto_id,
                            SUM(u.metros_totales) AS m2
                    FROM cliente_proyecto_unidades cpuni
                    JOIN unidad u ON u.id = cpuni.unidad_id
                    GROUP BY cpuni.cliente_proyecto_id
                ) m  ON m.cliente_proyecto_id = cp.id
                WHERE p.organizacion_id = :organization_id
                GROUP BY p.nombre
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Nombre": row[0] or "",
            "Monto": babel.numbers.format_currency(
                number=row[1] or 0,
                currency="USD",
                locale="es_AR",
            ),
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_checks_due_this_week_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                select
                    cheque.numero as numero,
                    cheque.monto as monto,
                    cheque.fecha_de_pago as fecha_de_vencimiento,
                    moneda.iso as moneda,
                    inf.nombre as instituion
                from cheque left join
                moneda ON moneda.id = cheque.moneda_id left join
                institucion_financiera inf
                    on inf.id = cheque.institucion_financiera_id
                where
                estado in ('ENTREGADO','EMITIDO') and
                organizacion_id = :organization_id
                and(cheque.fecha_de_pago BETWEEN
                DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                DATE_ADD(DATE_SUB(
                    CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY),
                         INTERVAL 6 DAY))
                LIMIT 50
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Institución": row[4] or "",
            "Nro.": row[0] or "",
            "Fecha de Vencimiento": babel.dates.format_date(
                row[2],
                locale="es_AR",
            )
            or "",
            "Monto": babel.numbers.format_currency(
                number=row[1] or 0,
                currency=row[3],
                locale="es_AR",
            )
            or "",
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}


async def get_available_units_for_sale_node(
    state: InsightState, config: RunnableConfig
) -> InsightState:
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker:
        logger.warning("No Lebane dbsessionmaker configured")
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    organization_id = state["lebane_user_context"]["organization_id"]

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                select
                    unidad.numero as numero_de_unidad,
                    unidad.metros_totales as metros_totales,
                    unidad.precio as precio,
                    moneda.iso as moneda ,
                    unidad.tipo as tipo,
                    proyecto.nombre as proyecto
                from unidad left join
                proyecto on proyecto.id = unidad.proyecto_id left join
                moneda on moneda.id = unidad.moneda_id
                where proyecto.organizacion_id = :organization_id
                and unidad.estado = 'DISPONIBLE'
                """
            ).bindparams(organization_id=organization_id)
        )

    def humanize(row):
        return {
            "Proyecto": row[5] or "",
            "Nro.": row[0] or "",
            "Tipo": row[4] or "",
            "Metros Totales": row[1] or "",
            "Precio": babel.numbers.format_currency(
                number=row[2] or 0,
                currency=row[3],
                locale="es_AR",
            )
            or "",
        }

    data = [humanize(row) for row in result]
    if not data:
        return {"messages": [AIMessage(content="No hay datos")]}

    return {"messages": render_messages_from_data(data=data)}

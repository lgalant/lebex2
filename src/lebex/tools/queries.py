"""LangChain tools wrapping Lebane ERP database queries.

Each tool receives DB session factories and organization context via
RunnableConfig (config["configurable"]) — the same mechanism used by the
existing graph nodes in nodes.py.

Read tools: return formatted data directly.
Write tools: use interrupt() to ask for explicit user confirmation before
             executing any mutation.
"""
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
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

logger = logging.getLogger(__name__)


class _Base(DeclarativeBase):
    pass


class _SupplierInDB(_Base):
    __tablename__ = "proyecto_proveedor"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column("nombre", sa.String, nullable=False)
    organization_id: Mapped[int] = mapped_column("organizacion_id", sa.Integer, nullable=True)


async def _search_supplier_by_name(
    name: str, organization_id: int, dbsession: AsyncSession
) -> _SupplierInDB | None:
    stmt = sa.select(_SupplierInDB).where(_SupplierInDB.organization_id == organization_id)
    result = await dbsession.execute(statement=stmt)
    active = {row.name: row for row in result.scalars()}
    match = rapidfuzz.process.extractOne(
        name, active.keys(),
        processor=rapidfuzz.utils.default_process,
        score_cutoff=70,
    )
    if match:
        matched_name, _, _ = match
        return active[matched_name]
    return None


TABULAR_TEMPLATE = jinja2.Template(
    """{% for row in rows %}{% for key, value in row.items() -%}
*{{ key }}:* {{ value }}
{% endfor %}{% if not loop.last %}------------------------------{% endif %}
{% endfor %}"""
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _render(data: list) -> str:
    """Format up to 100 records as tabular markdown text."""
    chunks = list(more_itertools.chunked(itertools.islice(data, 100), 10))
    if not chunks:
        return "No hay datos."
    return "\n\n".join(TABULAR_TEMPLATE.render(rows=chunk) for chunk in chunks)


def _no_db() -> str:
    return "Hubo un problema al conectar con Lebane."


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------

@tool
async def get_cash_position(config: RunnableConfig) -> str:
    """Consulta la posición de caja: saldos en cuentas bancarias y cajas por moneda."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

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
                    ON c.id = fdpb.caja_origen_id OR c.id = fdpb.caja_destino_id
                JOIN moneda m ON m.id = c.moneda_id
                WHERE fdpb.organizacion_id = :organization_id
                GROUP BY c.id, c.numero_de_cuenta, m.nombre

                UNION ALL

                SELECT
                    cb.numero_de_cuenta AS Cuenta,
                    SUM(
                        CASE
                            WHEN m.nombre = 'USD' THEN
                                CASE WHEN cb.id = fdpb.cuenta_bancaria_origen_id
                                     THEN -fdpb.monto_moneda_extranjera
                                     ELSE  fdpb.monto_moneda_extranjera
                                END
                            ELSE
                                CASE WHEN cb.id = fdpb.cuenta_bancaria_origen_id
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
                JOIN moneda m ON m.id = cb.moneda_id
                WHERE fdpb.organizacion_id = :organization_id
                GROUP BY cb.id, cb.numero_de_cuenta, m.nombre
                ORDER BY tipo, moneda
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Cuenta": row[0],
            "Tipo": row[3],
            "Total": babel.numbers.format_currency(row[1], currency=row[2], locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_clients_with_debt(config: RunnableConfig) -> str:
    """Lista los clientes con cuotas o saldos vencidos impagos."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

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
                    SELECT
                        ci.cliente_proyecto_item_id AS cpi_id,
                        ci.moneda_id AS moneda_id,
                        ci.saldo AS total_saldo
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN cuota_ingreso ci
                        ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda ON moneda.id = ci.moneda_id
                    LEFT JOIN cliente_proyecto cp ON cp.id = cpi.cliente_proyecto_id
                    LEFT JOIN proyecto proj ON proj.id = cp.proyecto_id
                    WHERE ci.fecha < CURDATE()
                      AND ci.saldo > 0
                      AND proj.organizacion_id = :organization_id
                    UNION ALL
                    SELECT
                        cpi.id AS cpi_id,
                        cpi.moneda_id AS moneda_id,
                        cpi.saldo AS total_saldo
                    FROM cliente_proyecto_item cpi
                    WHERE cpi.cantidad_cuotas = 0
                      AND cpi.fecha_primera_cuota < CURDATE()
                      AND cpi.saldo > 0
                ) AS t
                JOIN cliente_proyecto_item cpi ON cpi.id = t.cpi_id
                JOIN cliente_proyecto cp ON cp.id = cpi.cliente_proyecto_id
                JOIN cliente cli ON cli.id = cp.cliente_id
                JOIN moneda m ON m.id = t.moneda_id
                WHERE cli.organizacion_id = :organization_id
                GROUP BY cli.id, cli.nombre, cli.apellido, m.iso
                ORDER BY cli.apellido, cli.nombre, cli.id, m.iso
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Nombre": row[0],
            "Apellido": row[1],
            "Monto": babel.numbers.format_currency(row[3], currency=row[2] or "ARS", locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_clients_balance(config: RunnableConfig) -> str:
    """Muestra el balance de cuenta de cada cliente en USD y moneda local."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    CONCAT(c.nombre, ' ', c.apellido) AS cliente,
                    SUM(CASE WHEN m.id = 1 THEN cpi.saldo ELSE 0 END) AS saldo_extranjera,
                    SUM(CASE WHEN m.id != 1 THEN cpi.saldo ELSE 0 END) AS saldo_local,
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

    data = [
        {
            "Cliente": row[0],
            "Saldo USD": babel.numbers.format_currency(row[1], currency="USD", locale="es_AR"),
            "Saldo Local": babel.numbers.format_currency(row[2], currency=row[3] or "ARS", locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_clients_with_debt_by_project(config: RunnableConfig) -> str:
    """Muestra los saldos pendientes de clientes desglosados por proyecto."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    CONCAT(c.nombre, ' ', c.apellido) AS cliente,
                    p.nombre,
                    SUM(CASE WHEN m.id = 1 THEN cpi.saldo ELSE 0 END) AS saldo_extranjera,
                    SUM(CASE WHEN m.id != 1 THEN cpi.saldo ELSE 0 END) AS saldo_local,
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

    data = [
        {
            "Cliente": row[0],
            "Proyecto": row[1],
            "Saldo USD": babel.numbers.format_currency(row[2], currency="USD", locale="es_AR"),
            "Saldo Local": babel.numbers.format_currency(row[3], currency=row[4] or "ARS", locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_pending_invoices_by_supplier(config: RunnableConfig) -> str:
    """Lista todas las facturas y comprobantes de proveedores pendientes de pago."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

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
                LEFT JOIN moneda ON moneda.id = op.moneda_id
                LEFT JOIN razon_social rs ON rs.id = op.razon_social_proveedor_id
                WHERE op.tipo IN ('COMPROBANTE', 'FACTURA', 'NOTA_DE_DEBITO')
                  AND op.estado <> 'PAGADO'
                  AND op.organizacion_id = :organization_id
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proveedor": row[1] or "",
            "Tipo": row[0] or "",
            "Nro.": row[2] or "",
            "Fecha de Vencimiento": babel.dates.format_date(row[5], locale="es_AR") or "",
            "Monto": babel.numbers.format_currency(row[3], currency=row[4], locale="es_AR") or "",
            "Saldo": babel.numbers.format_currency(row[6], currency=row[4], locale="es_AR") or "",
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_income_projection_next_month(config: RunnableConfig) -> str:
    """Proyección de ingresos esperados en los próximos 30 días, por moneda."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT moneda_iso, SUM(total_saldo) AS total_saldo
                FROM (
                    SELECT SUM(ci.saldo) AS total_saldo, moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN cuota_ingreso ci ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE cpi.cantidad_cuotas > 0
                      AND ci.saldo > 0
                      AND (ci.fecha BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                      AND cpi.organizacion_id = :organization_id
                    UNION ALL
                    SELECT SUM(cpi.saldo) AS total_saldo, moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE cpi.cantidad_cuotas = 0
                      AND (cpi.fecha_primera_cuota BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                      AND cpi.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                ) AS t
                GROUP BY moneda_iso
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {"Monto": babel.numbers.format_currency(row[1], currency=row[0], locale="es_AR")}
        for row in result
    ]
    return _render(data)


@tool
async def get_expected_payments_today(config: RunnableConfig) -> str:
    """Lista las facturas de proveedores con vencimiento hoy o anteriores pendientes de pago."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    p.nombre, op.numero, op.fecha_vencimiento_pago,
                    op.monto, m.nombre
                FROM orden_de_pago op
                JOIN proveedor_razones_sociales_proveedor rsp
                    ON op.razon_social_proveedor_id = rsp.razones_sociales_proveedor_id
                JOIN proveedor p ON rsp.proveedor_id = p.id
                JOIN moneda m ON m.id = op.moneda_id
                WHERE p.organizacion_id = :organization_id
                  AND op.tipo = 'FACTURA'
                  AND op.estado = 'SOLICITADO'
                  AND fecha_vencimiento_pago <= CURDATE()
                GROUP BY p.id, op.id
                ORDER BY p.id, op.fecha_vencimiento_pago ASC, m.id ASC, op.monto DESC
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proveedor": row[0],
            "Nro. Factura": row[1],
            "Fecha de Vencimiento": babel.dates.format_date(row[2], locale="es_AR"),
            "Monto": babel.numbers.format_currency(row[3], currency=row[4], locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_income_projection_this_week(config: RunnableConfig) -> str:
    """Proyección de ingresos esperados durante la semana actual, por moneda."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT moneda_iso, SUM(total_saldo) AS total_saldo
                FROM (
                    SELECT SUM(ci.saldo) AS total_saldo, moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN cuota_ingreso ci ON ci.cliente_proyecto_item_id = cpi.id
                    LEFT JOIN moneda ON moneda.id = ci.moneda_id
                    LEFT JOIN cliente_proyecto cp ON cp.id = cpi.cliente_proyecto_id
                    LEFT JOIN proyecto proj ON proj.id = cp.proyecto_id
                    WHERE (ci.fecha BETWEEN
                        DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                        DATE_ADD(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 6 DAY))
                      AND proj.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                    UNION ALL
                    SELECT SUM(cpi.saldo) AS total_saldo, moneda.iso AS moneda_iso
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda ON moneda.id = cpi.moneda_id
                    WHERE cpi.cantidad_cuotas = 0
                      AND (cpi.fecha_primera_cuota BETWEEN
                        DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                        DATE_ADD(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 6 DAY))
                      AND cpi.organizacion_id = :organization_id
                    GROUP BY moneda.iso
                ) AS t
                GROUP BY moneda_iso
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {"Monto": babel.numbers.format_currency(row[1], currency=row[0], locale="es_AR")}
        for row in result
    ]
    return _render(data)


@tool
async def get_pending_invoices_this_week(config: RunnableConfig) -> str:
    """Facturas y comprobantes de proveedores con vencimiento durante la semana actual."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

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
                LEFT JOIN moneda ON moneda.id = op.moneda_id
                LEFT JOIN razon_social rs ON rs.id = op.razon_social_proveedor_id
                WHERE op.fecha_vencimiento_pago BETWEEN
                    DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                    DATE_ADD(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 6 DAY)
                  AND op.tipo IN ('COMPROBANTE', 'FACTURA', 'NOTA_DE_DEBITO')
                  AND op.estado <> 'PAGADO'
                  AND op.organizacion_id = :organization_id
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proveedor": row[1],
            "Tipo": row[0],
            "Nro.": row[2],
            "Fecha de Vencimiento": babel.dates.format_date(row[5], locale="es_AR"),
            "Monto": babel.numbers.format_currency(row[3], currency=row[4], locale="es_AR"),
            "Saldo": babel.numbers.format_currency(row[6], currency=row[4], locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_expenses_by_project(config: RunnableConfig) -> str:
    """Gastos totales agrupados por proyecto en USD y moneda local."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    SUM(CASE WHEN egreso.moneda_id <> 1 THEN egreso.monto_moneda_local ELSE 0 END) AS total_local,
                    SUM(CASE WHEN egreso.moneda_id = 1 THEN egreso.monto_moneda_extranjera ELSE 0 END) AS total_usd,
                    proyecto.nombre
                FROM egreso_generico egreso
                LEFT JOIN proyecto ON proyecto.id = egreso.proyecto_id
                WHERE proyecto.organizacion_id = :organization_id
                GROUP BY proyecto.nombre
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proyecto": row[2],
            "Total USD": babel.numbers.format_currency(row[1], currency="USD", locale="es_AR"),
            "Total Local": babel.numbers.format_currency(row[0], currency="ARS", locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_checks_due_this_week(config: RunnableConfig) -> str:
    """Cheques emitidos o entregados con vencimiento durante la semana actual."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    cheque.numero, cheque.monto, cheque.fecha_de_pago,
                    moneda.iso, inf.nombre AS institucion
                FROM cheque
                LEFT JOIN moneda ON moneda.id = cheque.moneda_id
                LEFT JOIN institucion_financiera inf ON inf.id = cheque.institucion_financiera_id
                WHERE estado IN ('ENTREGADO', 'EMITIDO')
                  AND organizacion_id = :organization_id
                  AND (cheque.fecha_de_pago BETWEEN
                    DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                    DATE_ADD(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 6 DAY))
                LIMIT 50
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Institución": row[4] or "",
            "Nro.": row[0] or "",
            "Fecha de Vencimiento": babel.dates.format_date(row[2], locale="es_AR") or "",
            "Monto": babel.numbers.format_currency(row[1] or 0, currency=row[3], locale="es_AR") or "",
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_avg_sale_price_per_sqm(config: RunnableConfig) -> str:
    """Precio de venta promedio por metro cuadrado desglosado por proyecto."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    p.nombre,
                    ROUND(SUM(a.total_usd) / NULLIF(SUM(m.m2), 0), 2) AS usd_por_m2
                FROM proyecto p
                JOIN cliente_proyecto cp ON cp.proyecto_id = p.id
                LEFT JOIN (
                    SELECT cpi.cliente_proyecto_id,
                           SUM(CASE WHEN mon.iso = 'USD' THEN cpi.monto
                               ELSE cpi.monto / NULLIF(cpi.tipo_de_cambio, 0) END) AS total_usd
                    FROM cliente_proyecto_item cpi
                    LEFT JOIN moneda mon ON mon.id = cpi.moneda_id
                    GROUP BY cpi.cliente_proyecto_id
                ) a ON a.cliente_proyecto_id = cp.id
                LEFT JOIN (
                    SELECT cpuni.cliente_proyecto_id, SUM(u.metros_totales) AS m2
                    FROM cliente_proyecto_unidades cpuni
                    JOIN unidad u ON u.id = cpuni.unidad_id
                    GROUP BY cpuni.cliente_proyecto_id
                ) m ON m.cliente_proyecto_id = cp.id
                WHERE p.organizacion_id = :organization_id
                GROUP BY p.nombre
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proyecto": row[0] or "",
            "USD/m²": babel.numbers.format_currency(row[1] or 0, currency="USD", locale="es_AR"),
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_investor_clients_by_project(config: RunnableConfig) -> str:
    """Lista los clientes inversores por proyecto."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT p.nombre, CONCAT(c.nombre, ' ', c.apellido) AS cliente
                FROM cliente_proyecto cp
                JOIN proyecto p ON p.id = cp.proyecto_id
                JOIN cliente c ON cp.cliente_id = c.id
                WHERE p.organizacion_id = :organization_id
                  AND cp.tipo_cliente = "INVERSOR"
                ORDER BY p.id, cp.id
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [{"Proyecto": row[0], "Inversor": row[1]} for row in result]
    return _render(data)


@tool
async def get_new_prospects_this_week(config: RunnableConfig) -> str:
    """Prospectos nuevos creados durante la semana actual."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    prospecto.nombre, prospecto.apellido,
                    prospecto.correo_electronico, prospecto.telefono,
                    usuario.nombre AS responsable
                FROM prospecto
                LEFT JOIN usuario ON usuario.id = prospecto.responsable_id
                WHERE prospecto.organizacion_id = :organization_id
                  AND (fecha_de_creacion BETWEEN
                    DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND
                    DATE_ADD(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 6 DAY))
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Prospecto": (row[0] or "") + " " + (row[1] or ""),
            "Teléfono": row[3],
            "Email": row[2],
            "Responsable": row[4],
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_available_units_for_sale(config: RunnableConfig) -> str:
    """Unidades inmobiliarias disponibles para la venta con precio y metraje."""
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    unidad.numero, unidad.metros_totales, unidad.precio,
                    moneda.iso, unidad.tipo, proyecto.nombre
                FROM unidad
                LEFT JOIN proyecto ON proyecto.id = unidad.proyecto_id
                LEFT JOIN moneda ON moneda.id = unidad.moneda_id
                WHERE proyecto.organizacion_id = :organization_id
                  AND unidad.estado = 'DISPONIBLE'
                """
            ).bindparams(organization_id=organization_id)
        )

    data = [
        {
            "Proyecto": row[5] or "",
            "Nro.": row[0] or "",
            "Tipo": row[4] or "",
            "Metros Totales": row[1] or "",
            "Precio": babel.numbers.format_currency(row[2] or 0, currency=row[3], locale="es_AR") or "",
        }
        for row in result
    ]
    return _render(data)


@tool
async def get_supplier_summary(supplier_name: str, config: RunnableConfig) -> str:
    """Resumen financiero completo de un proveedor: presupuesto, saldo y deuda por proyecto.

    Args:
        supplier_name: Nombre (o parte del nombre) del proveedor a consultar.
    """
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    lsessionmaker = config["configurable"].get("lsessionmaker")
    organization_id = config["configurable"].get("organization_id")
    if not ldbsessionmaker or not lsessionmaker:
        return _no_db()

    async with ldbsessionmaker() as dbsession:
        supplier = await _search_supplier_by_name(
            name=supplier_name,
            organization_id=organization_id,
            dbsession=dbsession,
        )

    if supplier is None:
        return f"No encontré un proveedor con nombre '{supplier_name}'. Verificá el nombre e intentá de nuevo."

    async with lsessionmaker() as client:
        http_client = client._session
        assert isinstance(http_client, httpx.AsyncClient)

        try:
            response = await http_client.get(
                url="/proyecto-proveedor/general/cuentas-corrientes",
                params={"pagina": "0", "cantidad": "50", "search": supplier.name},
            )
            response.raise_for_status()
        except httpx.ReadTimeout:
            return "La llamada a Lebane tomó demasiado tiempo."
        except httpx.HTTPStatusError as exc:
            logger.error(exc.response.text)
            return _no_db()

    result = response.json().get("content", [])

    def to_usd(v):
        return babel.numbers.format_currency(v, currency="USD", locale="es_AR")

    def to_ars(v):
        return babel.numbers.format_currency(v, currency="ARS", locale="es_AR")

    def humanize(row):
        t = row["totalizador"]
        d = {
            "Proveedor": row["proveedor"]["nombre"],
            "Cuenta Corriente": row["nombre"],
            "Proyecto": row["proyectoNombre"],
        }
        if any(t[k] > 0 for k in t if "local" in k.lower()):
            d.update({
                "Presupuesto Base Local": to_ars(t["presupuestoBaseLocal"]),
                "Saldo Base Local": to_ars(t["saldoBaseLocal"]),
                "Pagado Total Local": to_ars(t["pagadoBaseLocal"]),
                "Deuda Final Local": to_ars(t["deudaFinalLocal"]),
            })
        if any(t[k] > 0 for k in t if "extranjera" in k.lower()):
            d.update({
                "Presupuesto Base Extranjera": to_usd(t["presupuestoBaseExtranjera"]),
                "Saldo Base Extranjera": to_usd(t["saldoBaseExtranjera"]),
                "Pagado Total Extranjera": to_usd(t["pagadoBaseExtranjera"]),
                "Deuda Final Extranjera": to_usd(t["deudaFinalExtranjera"]),
            })
        if len(d) == 3:
            d["Nota"] = "No hay información financiera disponible."
        return d

    data = [humanize(row) for row in result]
    return _render(data)


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

READ_TOOLS = [
    get_cash_position,
    get_clients_with_debt,
    get_clients_balance,
    get_clients_with_debt_by_project,
    get_pending_invoices_by_supplier,
    get_income_projection_next_month,
    get_expected_payments_today,
    get_income_projection_this_week,
    get_pending_invoices_this_week,
    get_expenses_by_project,
    get_checks_due_this_week,
    get_avg_sale_price_per_sqm,
    get_investor_clients_by_project,
    get_new_prospects_this_week,
    get_available_units_for_sale,
    get_supplier_summary,
]

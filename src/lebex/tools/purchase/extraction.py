import datetime

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import Field

from lebane.requisition.schemas.types import RequisitionCriticality
from lebane.requisition.schemas.types import UnitOfMeasurement
from lebane.requisition.schemas.types import UnitOfMeasurementType


'''class RequisitionItemExtracted(BaseModel):
    name: str = Field(
        default="GENERAL",
        description="Name of the item with details like type or measurementes"
        " we are looking for  in its singular form for better results."
        " e.g. clavos de 3x52 -> clavo 3x52, CLAVOS -> CLAVO",
    )
    description: None | str = Field(
        default=None,
        description="Text section where the Item values were extracted from. "
        "Don't include things like 'I want' or 'request' "
        "e.g. quiero pedir 130 hojas de papel para el martes -> "
        "130 hojas de papel para el martes; "
        "quiero pedir 20 lapiceras -> 20 lapiceras",
    )
    quantity: int = 0
    unit_of_measurement_type: UnitOfMeasurementType = (
        UnitOfMeasurementType.UNITS
    )
    unit_of_measurement: UnitOfMeasurement = UnitOfMeasurement.UNITS
    expected_at: AwareDatetime = Field(
        default=(
            datetime.datetime.now(tz=datetime.timezone.utc)
            + datetime.timedelta(days=1)
        ).replace(hour=0, minute=0, second=0, microsecond=0),
        description="Timezone aware datetime the item is expected, "
        "by default tomorrow is assumed",
    )


class RequisitionExtracted(BaseModel):
    project: None | str = Field(
        default="",
        description="If not explicitly defined is probably an address",
    )
    criticality: None | RequisitionCriticality = RequisitionCriticality.NORMAL
    responsibles: None | list[str] = Field(
        default_factory=lambda: [""],
        description="Responsible of the Requisition",
    )
    items: None | list[RequisitionItemExtracted] = Field(
        default_factory=lambda: [RequisitionItemExtracted()],
        description="Responsible of the Requisition",
    )
'''
'''
async def extract_requisition(
    text: str, context: None | dict = None
) -> RequisitionExtracted:
    if not context:
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        context = {"now": now, "today": now.strftime("%A %d de %B de %Y")}

    llm = init_chat_model(
        model="gpt-5-mini-2025-08-07",
        model_provider="openai",
        temperature=0,
    )
    llm_structured = llm.with_structured_output(RequisitionExtracted)
    response = await llm_structured.ainvoke(
        input=[
            SystemMessage(
                content="""
                Extract the information from the user message.
                Dates could be relative, please interpret them based on context
                Dates should respect the context timezone.
                Keep string casing intact.
                DON'T set information that is not present on the message.
                FOCUS, DO NOT HALLUCINATE INFORMATION.

                ## CONTEXT
                now: {now}
                today: {today}
                """.format(**context)
            ),
            HumanMessage(content=text),
        ]
    )
    assert isinstance(response, RequisitionExtracted)
    for item in response.items or []:
        assert isinstance(item, RequisitionItemExtracted)
        item.expected_at = item.expected_at.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    return RequisitionExtracted.model_validate(
        response.model_dump(exclude_none=True, exclude_unset=True)
    )
'''
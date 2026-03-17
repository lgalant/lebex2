from pydantic import AliasChoices
from pydantic import AwareDatetime
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_serializer

from lebane.core.schemas import Base
from lebane.core.schemas import Selector

from .types import ItemType
from .types import RequisitionCriticality
from .types import RequisitionItemState
from .types import UnitOfMeasurement
from .types import UnitOfMeasurementType


class RequisitionItemCreate(Base):
    item: Selector | int = Field(
        ...,
        description="Root item associated",
        validation_alias=AliasChoices("item", "itemId"),
        serialization_alias="itemId",
    )
    category: Selector | int = Field(
        ...,
        description="Category associated",
        validation_alias=AliasChoices("category", "rubroId"),
        serialization_alias="rubroId",
    )
    kind: ItemType = Field(
        ...,
        description="Discriminator for service and material",
        validation_alias=AliasChoices("kind", "tipo"),
        serialization_alias="tipo",
    )
    state: RequisitionItemState = Field(
        default=RequisitionItemState.DRAFT,
        validation_alias=AliasChoices("state", "estado"),
        serialization_alias="estado",
    )
    quantity: int = Field(
        ...,
        validation_alias=AliasChoices("quantity", "cantidad"),
        serialization_alias="cantidad",
    )
    unit_of_measurement_type: UnitOfMeasurementType = Field(
        ...,
        validation_alias=AliasChoices(
            "unit_of_measurement_type", "tipoUnidadDeMedida"
        ),
        serialization_alias="tipoUnidadDeMedida",
    )
    unit_of_measurement: UnitOfMeasurement = Field(
        ...,
        validation_alias=AliasChoices("unit_of_measurement", "unidadDeMedida"),
        serialization_alias="unidadDeMedida",
    )
    expected_at: AwareDatetime = Field(
        ...,
        validation_alias=AliasChoices("expected_at", "fechaDeEntrega"),
        serialization_alias="fechaDeEntrega",
    )
    description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("description", "descripcion"),
        serialization_alias="descripcion",
    )

    @field_serializer("item", "category")
    def serialize_selector(self, value: Selector | int) -> int:
        if isinstance(value, int):
            return value
        return value.id


class RequisitionCreate(Base):
    model_config = ConfigDict(extra="forbid")

    project: None | Selector | int = Field(
        ...,
        description="Project where it will be created",
        validation_alias=AliasChoices("project", "proyectoId"),
        serialization_alias="proyectoId",
    )
    state: RequisitionItemState = Field(
        default=RequisitionItemState.DRAFT,
        validation_alias=AliasChoices("state", "estado"),
        serialization_alias="estado",
    )
    criticality: RequisitionCriticality = Field(
        default=RequisitionCriticality.NORMAL,
        validation_alias=AliasChoices("criticality", "tipoCriticidad"),
        serialization_alias="tipoCriticidad",
    )
    responsibles: list[Selector] | list[int] = Field(
        ...,
        description="List of responsible users",
        validation_alias=AliasChoices("responsibles", "responablesIds"),
        serialization_alias="responablesIds",
        min_length=1,
    )
    items: list[RequisitionItemCreate] = Field(
        ...,
        description="List of items associated",
        min_length=1,
    )

    @field_serializer("project")
    def serialize_selector(self, value: None | Selector | int) -> None | int:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        return value.id

    @field_serializer("responsibles")
    def serialize_selector_list(
        self, value: list[Selector] | list[int]
    ) -> list[int]:
        assert isinstance(value, list)
        result = []
        for v in value:
            if isinstance(v, int):
                result.append(v)
            elif isinstance(v, Selector):
                result.append(v.id)
            else:
                raise ValueError(f"Unsupported type: {v}")
        return result

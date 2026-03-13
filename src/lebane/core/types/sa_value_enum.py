import enum
from typing import Type

from sqlalchemy import Enum


class ValueEnum(Enum):
    def __init__(self, enum_cls: Type[enum.Enum]) -> None:
        if not issubclass(enum_cls, enum.Enum):
            raise TypeError(
                "ValueEnum expects an Enum class, "
                f"got {type(enum).__name__} instead"
            )

        super().__init__(enum_cls, values_callable=self._values_callable)

    @staticmethod
    def _values_callable(enum_cls):
        return [e.value for e in enum_cls]

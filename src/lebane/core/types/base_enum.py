import enum


class BaseEnum(enum.StrEnum):
    @classmethod
    def _missing_(cls, value):
        """Called when value is not found in Enum."""
        if isinstance(value, str):
            if value in cls.__members__:
                return cls[value]
        return None

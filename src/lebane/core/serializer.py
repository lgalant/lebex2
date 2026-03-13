from typing import Any

from pydantic import BaseModel


class Serializer:
    def model_to_jsondict(self, model: BaseModel) -> dict[str, Any]:
        return model.model_dump(mode="json", by_alias=True, exclude_none=True)

    def model_to_json(self, model: BaseModel) -> str:
        return model.model_dump_json(
            indent=0, by_alias=True, exclude_none=True
        )

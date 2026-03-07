from pydantic import BaseModel
from pydantic import ConfigDict


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

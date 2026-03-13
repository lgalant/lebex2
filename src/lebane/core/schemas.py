import httpx
from pydantic import BaseModel
from pydantic import ConfigDict


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _response: None | httpx.Response = None


class Selector(Base):
    model_config = ConfigDict(extra="ignore")

    id: int

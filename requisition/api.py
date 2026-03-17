import urllib.parse

import httpx

from lebane.core.serializer import Serializer
from lebane.errors import translate_httpx_errors

from .schemas import RequisitionCreate


class RequisitionAPIRaw:
    base_url = "/orden-de-pedido/"

    def __init__(self, session: httpx.AsyncClient):
        self._session = session

    def build_url(self, url: str) -> str:
        return urllib.parse.urljoin(self.base_url, url)

    @translate_httpx_errors
    async def get(self, id_: int) -> httpx.Response:
        response = await self._session.get(self.build_url(str(id_)))
        response.raise_for_status()
        return response

    @translate_httpx_errors
    async def create(self, data: dict) -> httpx.Response:
        response = await self._session.post(
            self.build_url("add-bot?email=false"), json=data
        )
        response.raise_for_status()
        return response


class RequisitionAPI:
    def __init__(self, session: httpx.AsyncClient, keep_raw: bool = False):
        self.raw = RequisitionAPIRaw(session=session)
        self.serializer = Serializer()
        self.keep_raw = keep_raw

    async def get(self, id_: int, keep_raw: None | bool = None) -> RequisitionCreate:
        response = await self.raw.get(id_=id_)
        parsed = RequisitionCreate.model_validate(response.json())

        store_raw = keep_raw if keep_raw is not None else self.keep_raw
        if store_raw:
            parsed._response = response
        return parsed

    async def create(
        self, data: RequisitionCreate, keep_raw: None | bool = None
    ) -> int:
        serialized = self.serializer.model_to_jsondict(data)
        response = await self.raw.create(data=serialized)
        body = response.json()
        if "id" not in body:
            raise ValueError(f"Response body does not contain 'id': {body}")
        return int(body["id"])

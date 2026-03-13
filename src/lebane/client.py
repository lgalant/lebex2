import httpx

from .requisition.api import RequisitionAPI


class LebaneClient:
    def __init__(
        self, base_url: str, token: str, keep_raw: bool = False
    ) -> None:
        self.base_url = base_url
        self.token = token
        self._session: None | httpx.AsyncClient = None
        self.keep_raw = keep_raw

    async def __aenter__(self) -> "LebaneClient":
        self._session = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
        )

        self.requisitions = RequisitionAPI(
            session=self._session, keep_raw=self.keep_raw
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            await self._session.aclose()

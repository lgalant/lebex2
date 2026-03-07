import base64
import os
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Coroutine

import httpx
from async_lru import alru_cache
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine

from lebane.client import LebaneClient

from .settings import Settings


def encrypt_phone(phone: str, secret: bytes) -> str:
    aesgcm = AESGCM(secret)
    iv = os.urandom(12)  # 12 bytes for GCM
    data = phone.encode("utf-8")

    # Encrypt returns ciphertext + tag
    ciphertext = aesgcm.encrypt(iv, data, associated_data=None)

    combined = iv + ciphertext  # prepend IV
    encoded = base64.urlsafe_b64encode(combined).rstrip(b"=").decode("utf-8")
    return encoded


@alru_cache(maxsize=1000, ttl=3600)
async def get_token(*, phone: str, secret: bytes, base_url: str):
    encrypted_phone = encrypt_phone(phone=phone, secret=secret)
    async with httpx.AsyncClient(base_url=base_url) as client:
        response = await client.post(
            f"/auth/whatsapp/{encrypted_phone}",
            json={"access_token": "no need"},
        )
        response.raise_for_status()
        return response.json()["data"]["token"]  # Some user data


def get_async_tokenmaker(
    settings: Settings,
) -> Callable[[str], Coroutine[Any, Any, str]]:
    async def tokenmaker(phone: str) -> str:
        return await get_token(
            phone=phone,
            secret=base64.b64decode(settings.LEBANE_PHONE_SECRET),
            base_url=settings.LEBANE_BASE_URL,
        )

    return tokenmaker


# LEBANE_BASE_URL - Conexion con backend API 
def get_lebane_async_sessionmaker(
    settings: Settings,
) -> Callable[[str], Awaitable[str]]:
    async_tokenmaker = get_async_tokenmaker(settings=settings)

    async def phone_sessionmaker(phone: str):
        token = await async_tokenmaker(phone)

        def sessionmaker() -> LebaneClient:
            return LebaneClient(base_url=settings.LEBANE_BASE_URL, token=token)

        return sessionmaker

    return phone_sessionmaker


def get_async_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.LEBANE_DB_URI),
        pool_size=10,
        pool_recycle=18000,
        max_overflow=5,
        echo=settings.LEBANE_DB_DEBUG,
    )


# LEBANE_DB_URI - Conexion con MYSql en AWS
def get_lebane_db_async_sessionmaker(
    settings: Settings,
) -> async_sessionmaker[AsyncSession]:
    engine = get_async_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False)

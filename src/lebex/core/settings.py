import functools
from typing import Annotated

from pydantic import Field
from pydantic import MySQLDsn
from pydantic import PostgresDsn
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


SqliteDsn = Annotated[
    str,
    Field(
        description="DSN for aiosqlite (e.g. sqlite+aiosqlite:///./db.sqlite3)",
        pattern=r"^sqlite\+aiosqlite://.+$",
    ),
]

'''pydantic-settings + BaseSettings

Settings hereda de BaseSettings (de la librería pydantic-settings).
model_config = SettingsConfigDict(env_prefix="LEBEX_") le dice que lea variables de entorno con 
el prefijo LEBEX_.
Al instanciar Settings(), pydantic-settings busca automáticamente cada campo en las variables de 
entorno. Por ejemplo:
DB_URI → busca LEBEX_DB_URI
LEBANE_BASE_URL → busca LEBEX_LEBANE_BASE_URL
Carga del .env

pydantic-settings por defecto no carga un archivo .env automáticamente. 
Para habilitarlo hay que agregar env_file=".env" en el model_config:
model_config = SettingsConfigDict(env_prefix="LEBEX_", env_file=".env")
'''

class Settings(BaseSettings):
    """App settings pulled from environment variables prefixed with LEBEX_"""

    model_config = SettingsConfigDict(env_prefix="LEBEX_", env_file=".env", frozen=True, extra="ignore")

    DB_URI: PostgresDsn | SqliteDsn
    DB_DEBUG: bool = False

    CHECKPOINT_DB_URI: PostgresDsn | SqliteDsn
    STORE_DB_URI: PostgresDsn | SqliteDsn

    LEBANE_BASE_URL: str
    LEBANE_PHONE_SECRET: str
    LEBANE_DB_URI: MySQLDsn | SqliteDsn
    LEBANE_DB_DEBUG: bool = False
    LEBANE_JWT_SECRET: str

    IONIKSEND_APIKEY: str
    IONIKSEND_APITOKEN: str
    IONIKSEND_ROUTE: str

    @field_validator("DB_URI", "CHECKPOINT_DB_URI", mode="after")
    @classmethod
    def convert_to_str(cls, v):
        return str(v)


@functools.lru_cache(maxsize=1)
def get_settings():
    return Settings()

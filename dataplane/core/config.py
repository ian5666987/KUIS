"""
Settings for the FastAPI data plane, read from the same .env file Django
uses (architecture plan §1 — one repo, one dependency file, one env).

PHASE 0: only what /health needs. `database_url` and `jwt_secret` are wired
up but unused until Phase 1 (auth) and Phase 2 (repositories) actually read
them — see dataplane/main.py.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    debug: bool = False

    # Composed from Django's DB_* vars rather than a separate DATAPLANE_DB_URL
    # — same Postgres instance, same schema, Django's migrations own it
    # (architecture plan §2).
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    db_host: str = "localhost"
    db_port: str = "5432"

    # Phase 1: shared secret Django's SIMPLE_JWT["SIGNING_KEY"] also uses, so
    # FastAPI can verify tokens statelessly. Not read anywhere yet.
    jwt_secret: str = ""

    redis_url: str = "redis://localhost:6379/0"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()

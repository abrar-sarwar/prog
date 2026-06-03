"""Environment-driven configuration for prog.

All secrets are loaded from a local .env file via python-dotenv. The
``DATABASE_URL`` value is passed through unchanged - it must include the
``+asyncpg`` driver qualifier (e.g. ``postgresql+asyncpg://...``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load the env file named by ENV_FILE (e.g. ".env.dev" for the isolated test
# database), defaulting to ".env" so the production "prog" setup is unaffected.
load_dotenv(os.getenv("ENV_FILE", ".env"))


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    discord_token: str
    test_guild_id: int
    database_url: str


def _require(key: str) -> str:
    """Return the env var ``key`` or raise if missing/empty."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def load_config() -> Config:
    """Load and validate configuration from the process environment."""
    return Config(
        discord_token=_require("DISCORD_TOKEN"),
        test_guild_id=int(_require("TEST_GUILD_ID")),
        database_url=_require("DATABASE_URL"),
    )

"""Entrypoint for prog.

Configures logging, builds the bot, loads cogs, and starts the event loop.

Run with::

    source venv/bin/activate
    python main.py
"""

from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from config import load_config
from db.engine import dispose_engine

log = logging.getLogger("prog")


def setup_logging() -> None:
    """Configure root logger to log to stdout with timestamps."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # discord.py is chatty at DEBUG; INFO is plenty.
    logging.getLogger("discord").setLevel(logging.INFO)


# Cogs to load on startup. Empty stubs (still being built) are intentionally
# excluded - importing an unfinished extension would crash on load.
COGS: list[str] = [
    "cogs.onboarding",
    "cogs.message_xp",
    "cogs.voice_xp",
    "cogs.rewards",
    "cogs.commands",
    "cogs.admin",
    "cogs.leaderboard_channel",
]


class ProgBot(commands.Bot):
    """The prog Discord bot."""

    def __init__(self, test_guild_id: int) -> None:
        intents = discord.Intents.default()
        # Privileged intents (must also be enabled in the Developer Portal):
        intents.members = True
        intents.message_content = True
        # Non-privileged but required for the voice XP loop:
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.test_guild_id = test_guild_id

    async def setup_hook(self) -> None:
        """Load cogs and sync slash commands to the dev guild before login."""
        for cog in COGS:
            await self.load_extension(cog)
            log.info("loaded cog %s", cog)
        # Mirror global app commands into the dev guild for instant sync.
        guild = discord.Object(id=self.test_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info(
            "synced %d slash command(s) to guild %s", len(synced), self.test_guild_id
        )

    async def on_ready(self) -> None:
        """Log the connected identity once the gateway handshake completes."""
        assert self.user is not None
        log.info(
            "connected as %s (%s); serving %d guild(s)",
            self.user,
            self.user.id,
            len(self.guilds),
        )

    async def close(self) -> None:
        """Dispose the DB engine on shutdown, then delegate to the base close."""
        log.info("shutting down: disposing DB engine")
        await dispose_engine()
        await super().close()


def main() -> None:
    """Bot entrypoint."""
    setup_logging()
    cfg = load_config()
    bot = ProgBot(test_guild_id=cfg.test_guild_id)
    # log_handler=None tells discord.py not to install its own handler so the
    # config above takes effect.
    bot.run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

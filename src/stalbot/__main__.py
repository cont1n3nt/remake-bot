"""Entry point: `python -m stalbot`."""

import logging
import sys

from pydantic import ValidationError

from stalbot.bootstrap import build_bot
from stalbot.config.settings import Settings

logger = logging.getLogger(__name__)


def run() -> None:
    """Load and validate configuration, then start the bot."""
    # Minimal fallback formatting for errors raised before build_bot() gets a
    # chance to install the real structlog/JSON configuration below.
    logging.basicConfig(level=logging.INFO)

    try:
        settings = Settings()  # type: ignore[call-arg]  # values come from .env
    except ValidationError as exc:
        logger.error("invalid .env configuration:\n%s", exc)
        sys.exit(1)

    bot = build_bot(settings)
    bot.run(settings.discord_token.get_secret_value())


if __name__ == "__main__":
    run()

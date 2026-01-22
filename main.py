import discord

from bot.core.bot import Bot
from bot.utils.settings import settings
from bot.utils.logger import logger

intents = discord.Intents.default()
intents.message_content = True

bot = Bot(
    intents=intents,
    help_command=None,
)


def main() -> None:
    try:
        logger.info('Bot is starting up...')
        bot.run(settings.discord_token)
    except KeyboardInterrupt:
        logger.info('Bot is shutting down...')
    finally:
        logger.info('Bot has exited...')


if __name__ == "__main__":
    main()

from bot.core.bot import Bot
from bot.cogs.redacted_messages.redacted_messages_listener import RedactedMessagesListener


async def setup(bot: Bot) -> None:
    await bot.add_cog(RedactedMessagesListener(bot))

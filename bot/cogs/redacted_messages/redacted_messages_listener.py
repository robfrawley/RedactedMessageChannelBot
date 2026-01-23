from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import io
import aiohttp
from PIL import Image

import discord
from discord.ext import commands

from bot.db.repos.user_warning_repo import user_warning_repo
from bot.models.user_warning import UserWarning
from bot.utils.helpers import (
    pil_to_discord_file,
    redacted_document_image,
    redact_asset,
    fetch_bytes,
    extract_gif_url_only,
)
from bot.utils.settings import settings
from bot.utils.logger import logger


class RedactedMessagesListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.is_actionable_redacted_message(message):
            return

        logger.debug(
            f"Processing message from {message.author.id}..."
        )

        try:
            await self.handle_redacted_message_generation(message)
        except Exception as e:
            logger.error(f"Error processing redacted message {message.id}: {e}")
        finally:
            try:
                await message.delete()
                await self.handle_redacted_message_mentions_warnings(message)
            except Exception:
                pass
            finally:
                await user_warning_repo.purge_older_than(24 * 60 * 60)


    def is_actionable_redacted_message(self, message: discord.Message) -> bool:
        if message.is_system():
            return False

        if message.guild is None:
            return False

        if self.bot.user is None:
            return False

        if message.author.bot:
            return False

        if message.channel.id != settings.redacted_channel_id:
            return False

        return True

    async def handle_redacted_message_mentions_warnings(self, message: discord.Message, period_seconds: int = 24 * 60 * 60) -> None:
        if message.mentions or message.role_mentions:
            embed = discord.Embed(
                title="Avoid mentioning users in redacted messages!",
                description=(
                    "Please do not mention users in messages sent to the redacted message channel. "
                    "Since messages are redacted and their content is removed, mentions leads to "
                    "phantom pings, which can be confusing and disruptive for users. Thank you for "
                    "your understanding.\n\n"
                    "While the message content has not been logged, this infraction has been recorded. "
                    "More than three infractions in a 24-hour period may result in a warning."
                ),
                color=discord.Color.red(),
                timestamp=datetime.now(tz=settings.bot_time_zone),
            )

            embed.footer.text = f"Message ID: {message.id} | Guild: {message.guild.name if message.guild else 'Unknown'}"

            await message.channel.send(
                content=f"{message.author.mention}",
                embed=embed,
                delete_after=settings.warnings_post_delete_delay_seconds,
            )

            warning_record = UserWarning(
                user_id=message.author.id,
                warning="Mentioned users in redacted message.",
                updated_at=datetime.now(tz=settings.bot_time_zone),
            )

            await user_warning_repo.add(warning_record)

            prior_warnings: list[UserWarning] = await user_warning_repo.get_since(
                user_id=message.author.id,
                period_seconds=period_seconds,
            )

            logger.debug(
                f"User {message.author.id} has {len(prior_warnings)} warnings for mentions in the last {period_seconds // 3600} hours."
            )

            if len(prior_warnings) >= 3:
                await self.send_mentions_warning(message, prior_warnings, period_seconds)

    async def send_mentions_warning(self, message: discord.Message, prior_warnings: list[UserWarning], period_seconds: int) -> None:
        if settings.log_channel_id is None:
            logger.warning("Log channel ID is not set; cannot send mentions warning.")
            return

        log_channel = self.bot.get_channel(settings.log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            logger.warning("Log channel is not a text channel; cannot send mentions warning.")
            return

        embed = discord.Embed(
            title="User Exceeded Redacted Message Warning Threshold",
            description=(
                f"User {message.author.mention} exceeded the warning threshold for posting "
                f"more than three messages containing **user or role mentions** in the redacted "
                f"messages channel within a {period_seconds // 3600} hour period!\n\n"
                f"Consider taking appropriate action as per the server's moderation policies."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(tz=settings.bot_time_zone),
        )

        embed.add_field(name="User Mention", value=message.author.mention)
        embed.add_field(name="Username", value=str(message.author))
        embed.add_field(name="User ID", value=str(message.author.id))
        embed.add_field(name="Warnings Last " + str(period_seconds // 3600) + "h", value=f"**{len(prior_warnings)}**")
        embed.add_field(name="Guild", value=message.guild.name if message.guild else "Unknown")
        embed.add_field(name="Guild ID", value=message.guild.id if message.guild else "Unknown")

        await log_channel.send(
            content=f"<@&{settings.log_mention_role_id}>" if settings.log_mention_role_id else None,
            embed=embed,
        )

    async def handle_redacted_message_generation(
        self,
        message: discord.Message,
        exposed_fraction: float = 0.05,
        window_count: int = 100,
    ) -> None:
        files: list[discord.File] = []

        # Treat "content is only a .gif URL" as an attachment-like asset instead of rendering text.
        gif_url = extract_gif_url_only(message.content)

        # Redacted text image (skip if it's only a gif URL)
        if message.content.strip() and not gif_url:
            doc_img = redacted_document_image(
                message.content,
                username=str(message.author),
                seed=message.id,
            )
            files.append(pil_to_discord_file(doc_img, filename=f"redacted_{message.id}_text.png"))

        # Redacted "gif url only" content as an attachment-like asset (first frame, then redact)
        if gif_url:
            seed_url = (message.id * 127) + 0
            try:
                logger.debug(f"B1: Redacting GIF URL content of message {message.id}: {gif_url}...")
                async with aiohttp.ClientSession() as session:
                    data = await fetch_bytes(session, gif_url)

                img = redact_asset(
                    data,
                    seed=seed_url,
                    exposed_fraction=exposed_fraction,
                    window_count=window_count,
                )
                files.append(pil_to_discord_file(img, filename=f"redacted_{message.id}_urlgif.png"))
            except Exception as e:
                logger.debug(f"B2: Redacting GIF URL content of message {message.id}: {gif_url} (fallback). Exception: {e}...")
                label = (
                    "ATTACHMENT REDACTED\n\n"
                    f"NAME: [GIF URL]\n"
                    f"SIZE: [REDACTED]\n"
                    "TYPE: image/gif\n"
                    f"URL: {gif_url}"
                )
                doc_img = redacted_document_image(label, seed=seed_url)
                files.append(pil_to_discord_file(doc_img, filename=f"redacted_{message.id}_urlgif.png"))

        # Redacted attachments (images / gifs / videos => take first frame then redact)
        for i, att in enumerate(message.attachments):
            seed_i = (message.id * 131) + i
            try:
                data = await att.read()

                try:
                    logger.debug(f"A1: Redacting attachment {att.filename} of message {message.id}...")
                    img = redact_asset(
                        data,
                        seed=seed_i,
                        exposed_fraction=exposed_fraction,
                        window_count=window_count,
                    )
                    files.append(pil_to_discord_file(img, filename=f"redacted_{message.id}_att{i+1}.png"))
                    logger.debug(f"A2: Redacted attachment {att.filename} of message {message.id}.")
                except Exception as e:
                    logger.debug(f"A3: Attachment {att.filename} redaction fallback for message {message.id}. Exception: {e}")
                    label = (
                        "ATTACHMENT REDACTED\n\n"
                        f"NAME: {att.filename}\n"
                        f"SIZE: {att.size} bytes\n"
                        f"TYPE: {att.content_type or '[REDACTED]'}"
                    )
                    doc_img = redacted_document_image(label, seed=seed_i)
                    files.append(pil_to_discord_file(doc_img, filename=f"redacted_{message.id}_att{i+1}.png"))

            except Exception as e:
                logger.debug(f"A4: Attachment read failed for {att.filename} of message {message.id}. Exception: {e}")
                label = (
                    "ATTACHMENT REDACTED\n\n"
                    f"NAME: {att.filename}\n"
                    "SIZE: [REDACTED]\n"
                    "TYPE: [REDACTED]"
                )
                doc_img = redacted_document_image(label, seed=seed_i)
                files.append(pil_to_discord_file(doc_img, filename=f"redacted_{message.id}_att{i+1}.png"))

        # Redacted stickers (use PNG rasterization to support animated stickers too)
        if message.stickers:
            async with aiohttp.ClientSession() as session:
                for j, st in enumerate(message.stickers):
                    seed_j = (message.id * 149) + j
                    try:
                        png_url = f"https://cdn.discordapp.com/stickers/{st.id}.png"
                        data = await fetch_bytes(session, png_url)

                        img = redact_asset(
                            data,
                            seed=seed_j,
                            exposed_fraction=exposed_fraction,
                            window_count=window_count,
                        )
                        files.append(pil_to_discord_file(img, filename=f"redacted_{message.id}_sticker{j+1}.png"))
                    except Exception:
                        label = f"STICKER REDACTED\n\nNAME: {st.name}\nID: {st.id}"
                        doc_img = redacted_document_image(label, seed=seed_j)
                        files.append(pil_to_discord_file(doc_img, filename=f"redacted_{message.id}_sticker{j+1}.png"))

        if not files:
            return

        # Discord max 10 files per message
        for start in range(0, len(files), 10):
            chunk = files[start : start + 10]
            await message.channel.send(
                files=chunk,
                allowed_mentions=discord.AllowedMentions.none(),
                delete_after=settings.redacted_post_delete_delay_seconds, # type: ignore
            )

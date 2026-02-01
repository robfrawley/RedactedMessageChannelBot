from __future__ import annotations

import asyncio

import aiohttp
import discord
from discord.ext import commands
from PIL import Image

from bot.utils.settings import settings
from bot.utils.logger import logger
from bot.utils.helpers import (
    redacted_document_image,
    redact_asset,
    pil_to_discord_file,
    fetch_bytes,
)


class RedactedMessagesListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Limit concurrent CPU-heavy renders
        self._render_sem = asyncio.Semaphore(4)

    # ─────────────────────────────────────────────
    # Threaded helpers (CPU-bound PIL work)
    # ─────────────────────────────────────────────

    async def _redacted_document_image_threaded(
        self,
        text: str,
        *,
        username: str | None = None,
        seed: int,
    ) -> Image.Image:
        return await asyncio.to_thread(
            redacted_document_image,
            text,
            username=username,
            seed=seed,
        )

    async def _redact_asset_threaded(
        self,
        data: bytes,
        *,
        seed: int,
        exposed_fraction: float,
        window_count: int,
    ) -> Image.Image:
        return await asyncio.to_thread(
            redact_asset,
            data,
            seed=seed,
            exposed_fraction=exposed_fraction,
            window_count=window_count,
        )

    async def _pil_to_discord_file_threaded(
        self,
        img: Image.Image,
        *,
        filename: str,
    ) -> discord.File:
        return await asyncio.to_thread(
            pil_to_discord_file,
            img,
            filename=filename,
        )

    # ─────────────────────────────────────────────
    # Listener
    # ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.is_actionable_redacted_message(message):
            return

        try:
            await self.handle_redacted_message_generation(message)
        except Exception:
            logger.error(
                f"Unhandled exception while redacting message {message.id}"
            )
        finally:
            try:
                await message.delete()
            except:
                pass

    # ─────────────────────────────────────────────
    # Core logic
    # ─────────────────────────────────────────────

    async def handle_redacted_message_generation(
        self,
        message: discord.Message,
        *,
        exposed_fraction: float = 0.05,
        window_count: int = 100,
    ) -> None:
        async with self._render_sem:
            files: list[discord.File] = []

            content = message.content.strip()

            gif_url = (
                content.lower().endswith(".gif")
                and content.startswith("http")
                and " " not in content
            )

            # ─── Text content ──────────────────────

            if content and not gif_url:
                doc_img = await self._redacted_document_image_threaded(
                    content,
                    username=str(message.author),
                    seed=message.id,
                )
                files.append(
                    await self._pil_to_discord_file_threaded(
                        doc_img,
                        filename=f"redacted_{message.id}_text.png",
                    )
                )

            # ─── GIF URL-only content ──────────────

            if gif_url:
                seed_url = message.id ^ 0xA5A5
                try:
                    async with aiohttp.ClientSession() as session:
                        data = await fetch_bytes(session, content)

                    img = await self._redact_asset_threaded(
                        data,
                        seed=seed_url,
                        exposed_fraction=exposed_fraction,
                        window_count=window_count,
                    )
                    files.append(
                        await self._pil_to_discord_file_threaded(
                            img,
                            filename=f"redacted_{message.id}_urlgif.png",
                        )
                    )
                except Exception as e:
                    logger.debug(
                        f"GIF URL fallback for message {message.id}: {e}"
                    )
                    label = (
                        "GIF REDACTED\n\n"
                        "TYPE: image/gif\n"
                        f"URL: {content}"
                    )
                    doc_img = await self._redacted_document_image_threaded(
                        label,
                        seed=seed_url,
                    )
                    files.append(
                        await self._pil_to_discord_file_threaded(
                            doc_img,
                            filename=f"redacted_{message.id}_urlgif.png",
                        )
                    )

            # ─── Attachments ──────────────────────

            for i, att in enumerate(message.attachments):
                seed_i = message.id + i + 1
                try:
                    data = await att.read()
                    try:
                        img = await self._redact_asset_threaded(
                            data,
                            seed=seed_i,
                            exposed_fraction=exposed_fraction,
                            window_count=window_count,
                        )
                        files.append(
                            await self._pil_to_discord_file_threaded(
                                img,
                                filename=f"redacted_{message.id}_att{i+1}.png",
                            )
                        )
                    except Exception as e:
                        logger.debug(
                            f"Attachment redact fallback {att.filename}: {e}"
                        )
                        label = (
                            "ATTACHMENT REDACTED\n\n"
                            f"NAME: {att.filename}\n"
                            f"SIZE: {att.size} bytes\n"
                            f"TYPE: {att.content_type or '[REDACTED]'}"
                        )
                        doc_img = await self._redacted_document_image_threaded(
                            label,
                            seed=seed_i,
                        )
                        files.append(
                            await self._pil_to_discord_file_threaded(
                                doc_img,
                                filename=f"redacted_{message.id}_att{i+1}.png",
                            )
                        )
                except Exception as e:
                    logger.debug(
                        f"Attachment read failed {att.filename}: {e}"
                    )
                    label = (
                        "ATTACHMENT REDACTED\n\n"
                        f"NAME: {att.filename}\n"
                        "SIZE: [REDACTED]\n"
                        "TYPE: [REDACTED]"
                    )
                    doc_img = await self._redacted_document_image_threaded(
                        label,
                        seed=seed_i,
                    )
                    files.append(
                        await self._pil_to_discord_file_threaded(
                            doc_img,
                            filename=f"redacted_{message.id}_att{i+1}.png",
                        )
                    )

            # ─── Stickers ─────────────────────────

            if message.stickers:
                async with aiohttp.ClientSession() as session:
                    for j, st in enumerate(message.stickers):
                        seed_j = message.id + 100 + j
                        try:
                            png_url = f"https://cdn.discordapp.com/stickers/{st.id}.png"
                            data = await fetch_bytes(session, png_url)
                            img = await self._redact_asset_threaded(
                                data,
                                seed=seed_j,
                                exposed_fraction=exposed_fraction,
                                window_count=window_count,
                            )
                            files.append(
                                await self._pil_to_discord_file_threaded(
                                    img,
                                    filename=f"redacted_{message.id}_sticker{j+1}.png",
                                )
                            )
                        except Exception:
                            label = (
                                "STICKER REDACTED\n\n"
                                f"NAME: {st.name}\n"
                                f"ID: {st.id}"
                            )
                            doc_img = await self._redacted_document_image_threaded(
                                label,
                                seed=seed_j,
                            )
                            files.append(
                                await self._pil_to_discord_file_threaded(
                                    doc_img,
                                    filename=f"redacted_{message.id}_sticker{j+1}.png",
                                )
                            )

            if not files:
                return

            await message.channel.send(files=files)

            try:
                await message.delete()
            except discord.Forbidden:
                pass

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

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

        if not message.channel.permissions_for(message.guild.me).manage_messages:
            return False

        return True

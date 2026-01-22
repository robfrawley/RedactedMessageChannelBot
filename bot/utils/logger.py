import datetime
import sys
from zoneinfo import ZoneInfo

from discord.app_commands import AppCommand
from pydantic_settings import BaseSettings

from bot.utils.settings import settings


class ConsoleLogger:
    def __init__(self, debug_enabled: bool = True, time_zone: ZoneInfo | None = None, date_format: str | None = None) -> None:
        self.debug_enabled = debug_enabled
        self.time_zone = time_zone or ZoneInfo("UTC")
        self.date_format = date_format or "%Y-%m-%d %H:%M:%S"

    def _log(self, level: str, message: str | None, level_color: str | None = None) -> None:
        timestamp = datetime.datetime.now(tz=self.time_zone).strftime(self.date_format)
        reset_code = "\033[0m"
        level_code = f"\033[{level_color}m" if level_color else "\033[37m"
        print(f"\033[37;2m{timestamp}{reset_code} {level_code}{level.ljust(8)}{reset_code} {message}", file=sys.stdout)

    def info(self, message: str | None) -> None:
        self._log("INFO", message, level_color="34;1")

    def debug(self, message: str | None) -> None:
        if self.debug_enabled:
            self._log("DEBUG", message, level_color="93")

    def warning(self, message: str | None) -> None:
        self._log("WARN", message, level_color="91")

    def error(self, message: str | None) -> None:
        self._log("CRIT", message, level_color="91;1")

    # log complete settings dump loaded from environment
    def log_settings(self, settings: BaseSettings) -> None:
        self.info("Loaded configuration...")

        fields = settings.__class__.model_fields.keys()
        values = {field: getattr(settings, field) for field in fields}
        max_len = max(len(name) for name in fields)

        for name, value in values.items():
            label = f'"{name}"'.ljust(max_len + 2)
            self.debug(f'- {label} = "{value}"')

    # log synced commands
    def log_commands(self, synced: list[AppCommand]) -> None:
        entries: list[tuple[str, str]] = []

        for command in synced:
            scope = "global" if command.guild_id is None else f"guild={command.guild_id}"
            entries.append((f"- \"{command.name}\"", scope))

        max_len = max((len(cmd) for cmd, _ in entries), default=0)

        logger.debug(f'Synced "{len(synced)}" commands...')

        for cmd, scope in entries:
            logger.debug(f'{cmd.ljust(max_len)} ({scope})')


logger = ConsoleLogger(debug_enabled=settings.debug_mode, time_zone=settings.bot_time_zone)

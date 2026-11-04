"""Logger estruturado com Rich."""
import logging
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme


CUSTOM_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "success": "bold green",
        "deleted": "red",
        "archived": "dim",
        "kept": "green",
    }
)

console = Console(theme=CUSTOM_THEME)


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Cria e retorna um logger configurado com RichHandler.

    Args:
        name: Nome do logger (normalmente __name__)
        level: Nível de log (DEBUG, INFO, WARNING, ERROR). Usa LOG_LEVEL do .env se None.
    """
    from src.utils.config import get_settings

    if level is None:
        try:
            level = get_settings().log_level
        except Exception:
            level = "INFO"

    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger

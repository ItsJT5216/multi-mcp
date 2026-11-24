import sys
from typing import TYPE_CHECKING, Literal

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

# Global namespace for all loggers
BASE_LOGGER_NAMESPACE = "multi_mcp"

# Initialization guard to prevent duplicate configuration
_configured = False


def get_logger(name: str) -> "Logger":
    """
    Returns a loguru logger bound with the given module name.

    Example: get_logger("ClientManager") → logger with module="multi_mcp.ClientManager"

    Note: loguru uses a single global logger; binding adds contextual
    information without creating separate logger instances.
    """
    return logger.bind(module=f"{BASE_LOGGER_NAMESPACE}.{name}")


def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
) -> None:
    """
    Configures loguru globally for the entire app.

    Should be called once (e.g., in MultiMCP.__init__).
    Subsequent calls are no-ops to prevent duplicate handlers.

    Args:
        level: Logging level as a string.
    """
    global _configured
    if _configured:
        return

    # Remove default loguru handler
    logger.remove()

    # Add stderr handler with formatting similar to the old RichHandler
    logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | <cyan>{extra[module]}</cyan> | {message}",
        level=level,
        colorize=True,
        filter=lambda record: "module" in record["extra"],
    )

    # Add a fallback handler for loggers without module binding
    logger.add(
        sys.stderr,
        format="<level>{level: <8}</level> | {message}",
        level=level,
        colorize=True,
        filter=lambda record: "module" not in record["extra"],
    )

    _configured = True

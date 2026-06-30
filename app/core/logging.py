import sys

from loguru import logger

from app.core.config import get_settings


def setup_logging() -> None:
    """Configure loguru avec un format structuré et le niveau depuis la config."""
    settings = get_settings()

    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
               "<level>{message}</level>",
    )
    logger.add(
        "logs/osint_platform.log",
        rotation="10 MB",
        retention="30 days",
        level=settings.LOG_LEVEL,
    )

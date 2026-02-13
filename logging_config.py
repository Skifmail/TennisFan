from loguru import logger

logger.remove()

logger.add(
    "app.log",
    rotation="10 MB",
    level="INFO",
    enqueue=True,
)

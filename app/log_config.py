"""structlog + stdlib logging wiring.

Single entry point: `configure_logging()`. Called once on app import so
both our `structlog.get_logger()` calls and stdlib loggers (uvicorn,
sqlalchemy, aiogram, httpx, websockets) render through the same pipeline.

Renderer:
- TTY  → ConsoleRenderer (colors, key=value)
- else → JSONRenderer
Override with LOG_RENDERER=console|json.
"""

import logging
import os
import sys
from logging.config import dictConfig

import structlog

_NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "websockets": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "aiogram.event": logging.WARNING,
}

_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    _configured = True

    use_json = _pick_renderer() == "json"
    renderer = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"struct": {"()": lambda: formatter}},
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "struct",
                    "stream": "ext://sys.stderr",
                },
            },
            "root": {"handlers": ["default"], "level": level.upper()},
        },
    )

    for name, lvl in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(lvl)


def _pick_renderer() -> str:
    explicit = os.environ.get("LOG_RENDERER", "").strip().lower()
    if explicit in {"console", "json"}:
        return explicit
    return "console" if sys.stderr.isatty() else "json"

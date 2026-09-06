from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class Tracer(Protocol):
    def span(self, span_name: str, **metadata) -> AsyncIterator[None]: ...


class NoOpTracer:
    """Default tracer: does nothing, costs nothing, never touches the
    network. Used whenever Langfuse is disabled or unreachable -- tracing
    must never block or break a chat response (see Global Constraints)."""

    @asynccontextmanager
    async def span(self, span_name: str, **metadata) -> AsyncIterator[None]:
        yield


class LangfuseTracer:
    """Thin wrapper over the Langfuse low-level client. Every Langfuse call
    is isolated in its own try/except so a Langfuse outage, a wrong host,
    or an SDK error degrades to "no trace recorded", never to a broken
    chat response."""

    def __init__(self, public_key: str, secret_key: str, host: str):
        from langfuse import Langfuse  # imported lazily so importing this
        # module never requires the langfuse package to be installed/working
        # when LANGFUSE_ENABLED=false.

        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    @asynccontextmanager
    async def span(self, span_name: str, **metadata) -> AsyncIterator[None]:
        trace = None
        try:
            trace = self._client.trace(name=span_name, metadata=metadata)
        except Exception as exc:  # noqa: BLE001 - fail open, see class docstring
            logger.warning("Langfuse trace() failed, continuing without tracing: %s", exc)

        try:
            yield
        finally:
            if trace is not None:
                try:
                    trace.update(output="ok")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Langfuse trace.update() failed: %s", exc)


@lru_cache
def get_tracer() -> Tracer:
    if not settings.langfuse_enabled:
        return NoOpTracer()
    try:
        return LangfuseTracer(
            public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key, host=settings.langfuse_host,
        )
    except Exception as exc:  # noqa: BLE001 - fail open, see class docstring above
        logger.warning("Failed to construct LangfuseTracer, falling back to NoOpTracer: %s", exc)
        return NoOpTracer()

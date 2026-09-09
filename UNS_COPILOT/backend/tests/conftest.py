import asyncio

import pytest


@pytest.fixture(autouse=True)
def _dispose_shared_db_engines():
    """Dispose app.database's shared engine pools after every test.

    app/database.py creates `engine`/`silver_engine` once at import
    time and keeps them alive for the whole pytest process. Each router
    test's `client` fixture spins up its own `TestClient(app)`, and
    starlette's TestClient runs the app on a brand-new anyio worker
    thread + event loop for every `with TestClient(app) as c:` block.
    asyncpg connections are bound to the loop that created them, so a
    pooled connection opened under one test's loop is unusable once that
    loop is closed and a later test's (different) loop borrows it from
    the pool -- this reproduces regardless of whether either test itself
    fails, purely from sharing one global pool across independently
    loop-scoped TestClient instances.

    Disposing the pool after each test forces the next test's TestClient
    to open fresh, loop-matched connections instead of reusing stale
    ones left over from a previous loop.
    """
    yield
    try:
        from app.database import engine, silver_engine
    except ImportError:
        return

    async def _dispose():
        await engine.dispose()
        await silver_engine.dispose()

    try:
        asyncio.run(_dispose())
    except Exception:
        # Best-effort cleanup; a dispose failure here shouldn't mask the
        # test's own pass/fail result.
        pass

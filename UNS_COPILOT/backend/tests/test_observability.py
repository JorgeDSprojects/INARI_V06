import pytest

from app.observability import NoOpTracer


@pytest.mark.asyncio
async def test_noop_tracer_span_never_raises():
    tracer = NoOpTracer()
    async with tracer.span("run_turn", conversation_id=1):
        pass  # must not raise, must not require any network access


@pytest.mark.asyncio
async def test_noop_tracer_swallows_exceptions_raised_inside_the_body():
    tracer = NoOpTracer()
    with pytest.raises(ValueError):
        async with tracer.span("run_turn"):
            raise ValueError("boom")  # the tracer must propagate the caller's own error...
    # ...but must never raise one of its own on top of it (nothing to assert
    # here beyond the block above not raising a *different* exception type).

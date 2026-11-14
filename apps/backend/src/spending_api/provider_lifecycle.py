"""Best-effort public async cleanup for explicitly owned provider roots."""

import asyncio
import inspect

PROVIDER_CLEANUP_TIMEOUT_SECONDS = 1.0


async def close_owned_provider(provider) -> None:
    """Await at most one close attempt, without inspecting borrowed model views.

    The caller establishes ownership; this helper never unwraps models or probes
    private transports. The separate budget is cooperative: it cannot preempt a
    synchronous SDK close prefix, and a closer that suppresses cancellation can
    outlast it. A new caller cancellation may interrupt this attempt. Completion
    of this helper therefore does not guarantee completed transport shutdown.
    """
    try:
        close = getattr(provider, "aclose", None)
        if not callable(close) or not (
            inspect.iscoroutinefunction(close)
            or inspect.iscoroutinefunction(getattr(close, "__call__", None))
        ):
            return
        async with asyncio.timeout(PROVIDER_CLEANUP_TIMEOUT_SECONDS):
            await close()
    except Exception:
        # Cleanup must preserve the primary outcome. Provider error text and
        # object representations may contain credentials or customer records.
        # CancelledError is intentionally not caught here.
        pass

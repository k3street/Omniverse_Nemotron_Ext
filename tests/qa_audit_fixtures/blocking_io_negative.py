"""NEGATIVE fixtures for Q12 — must NOT be flagged."""
import asyncio
import aiohttp


async def uses_asyncio_sleep():
    """asyncio.sleep is the correct async-friendly sleep."""
    await asyncio.sleep(1)
    return {"success": True}


async def uses_aiohttp():
    """aiohttp is the async HTTP client."""
    async with aiohttp.ClientSession() as session:
        async with session.get("https://example.com") as resp:
            return await resp.text()


def sync_function_with_blocking_calls():
    """Sync function — blocking calls here are FINE."""
    import time
    time.sleep(1)
    with open("/tmp/test.txt") as fh:
        return fh.read()


async def calls_sync_helper_that_blocks():
    """The async fn itself doesn't block — even if a sync helper does.

    Q12 only flags blocking calls that appear DIRECTLY in async-fn bodies.
    The audit explicitly does NOT chase nested sync helpers — that's
    Q12.5 territory (call-graph blocking analysis) which is judgment-only.
    """
    return sync_function_with_blocking_calls()


async def uses_async_to_thread():
    """asyncio.to_thread is the safe escape hatch for blocking calls."""
    return await asyncio.to_thread(lambda: open("/tmp/x").read())

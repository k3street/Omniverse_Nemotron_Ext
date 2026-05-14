"""POSITIVE fixtures for Q12 blocking-I/O-in-async — must be flagged."""
import time
import requests


async def calls_time_sleep():
    """time.sleep inside async fn blocks the event loop."""
    time.sleep(1)  # AUDIT_EXPECT: Q12 hit
    return {"success": True}


async def calls_requests_get():
    """requests.* blocks the event loop."""
    r = requests.get("https://example.com")  # AUDIT_EXPECT: Q12 hit
    return r.text


async def calls_open():
    """`open()` is technically blocking inside an async fn."""
    with open("/tmp/test.txt") as fh:  # AUDIT_EXPECT: Q12 hit
        return fh.read()

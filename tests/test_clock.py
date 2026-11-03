import asyncio

import pytest

from dsnode.clock import RealClock


async def test_real_clock_basic():
    clock = RealClock()
    start = clock.now()
    await clock.sleep(0.01)
    assert clock.now() - start >= 0.01

    fired = asyncio.Event()
    clock.call_later(0.01, fired.set)
    assert await clock.wait_for(fired.wait(), timeout=1.0) is True

    with pytest.raises(TimeoutError):
        await clock.wait_for(asyncio.Event().wait(), timeout=0.01)

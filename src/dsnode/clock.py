"""Relógio injetável usado por todos os algoritmos para medir tempo e timeouts.

Os algoritmos nunca chamam asyncio.sleep/asyncio.wait_for/time diretamente:
usam um Clock, para que os testes troquem RealClock por FakeClock (tests/sim.py)
e controlem o tempo de forma determinística.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

T = TypeVar("T")


class Timer(Protocol):
    def cancel(self) -> None: ...


class Clock(Protocol):
    def now(self) -> float:
        """Tempo monotônico em segundos."""
        ...

    async def sleep(self, seconds: float) -> None: ...

    def call_later(self, delay: float, callback: Callable[[], None]) -> Timer: ...

    async def wait_for(self, aw: Awaitable[T], timeout: float) -> T:
        """Como asyncio.wait_for: levanta TimeoutError se estourar o prazo."""
        ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def call_later(self, delay: float, callback: Callable[[], None]) -> Timer:
        return asyncio.get_running_loop().call_later(delay, callback)

    async def wait_for(self, aw: Awaitable[T], timeout: float) -> T:
        return await asyncio.wait_for(aw, timeout)

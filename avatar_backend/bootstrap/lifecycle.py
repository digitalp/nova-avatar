"""Service lifecycle protocol — implemented by all background services."""
from __future__ import annotations
from typing import Protocol, runtime_checkable


@runtime_checkable
class Lifecycle(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

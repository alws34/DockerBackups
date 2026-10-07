"""API route modules grouped by resource."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import HTTPException

from app.core.context import BackupError


async def run_or_400[T](
    func: Callable[..., T], *args: object, errors: tuple[type[Exception], ...] = (BackupError,)
) -> T:
    """Run blocking ``func`` in a thread; the expected ``errors`` become a 400."""
    try:
        return await asyncio.to_thread(func, *args)
    except errors as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

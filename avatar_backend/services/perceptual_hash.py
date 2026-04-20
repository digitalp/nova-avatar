"""Perceptual hashing utility for motion clip deduplication."""

from __future__ import annotations

import asyncio
import functools
import io

import structlog

_LOGGER = structlog.get_logger()


def hamming_distance(h1: int, h2: int) -> int:
    """Return the number of differing bits between two 64-bit hashes."""
    return bin(h1 ^ h2).count("1")


def _compute_phash_sync(image_bytes: bytes) -> int:
    """Compute a 64-bit average perceptual hash (synchronous)."""
    import imagehash
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((8, 8))
    h = imagehash.average_hash(img, hash_size=8)
    return int(str(h), 16)


async def compute_phash(image_bytes: bytes) -> int:
    """Return a 64-bit average perceptual hash. Runs in thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(_compute_phash_sync, image_bytes)
    )

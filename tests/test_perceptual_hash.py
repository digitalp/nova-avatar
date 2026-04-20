"""Unit tests for the perceptual hash utility."""
from __future__ import annotations

import io

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("imagehash"),
    reason="imagehash not installed",
)

from avatar_backend.services.perceptual_hash import compute_phash, hamming_distance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image_bytes(color: tuple[int, int, int] = (128, 128, 128), size: tuple[int, int] = (64, 64)) -> bytes:
    """Create a minimal PNG image in memory."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# compute_phash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_phash_returns_int():
    h = await compute_phash(_make_image_bytes())
    assert isinstance(h, int)


@pytest.mark.asyncio
async def test_compute_phash_deterministic():
    data = _make_image_bytes()
    h1 = await compute_phash(data)
    h2 = await compute_phash(data)
    assert h1 == h2


@pytest.mark.asyncio
async def test_compute_phash_different_images_differ():
    h_white = await compute_phash(_make_image_bytes(color=(255, 255, 255)))
    h_black = await compute_phash(_make_image_bytes(color=(0, 0, 0)))
    # Solid white vs solid black should produce very different hashes
    assert hamming_distance(h_white, h_black) > 0


# ---------------------------------------------------------------------------
# hamming_distance
# ---------------------------------------------------------------------------

def test_hamming_distance_identical():
    assert hamming_distance(0xDEADBEEF, 0xDEADBEEF) == 0


def test_hamming_distance_one_bit():
    assert hamming_distance(0b0000, 0b0001) == 1


def test_hamming_distance_all_bits():
    # 64-bit all-ones vs all-zeros → 64 differing bits
    assert hamming_distance(0xFFFFFFFFFFFFFFFF, 0x0) == 64


def test_hamming_distance_symmetric():
    a, b = 0xABCD1234, 0x1234ABCD
    assert hamming_distance(a, b) == hamming_distance(b, a)

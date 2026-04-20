"""BlueIrisService — direct camera access fallback when HA is unavailable.

Connects to Blue Iris at BI_URL (e.g. http://192.168.0.33:81) for:
- Snapshots: /image/{camera}?q=60
- MJPEG streams: /mjpg/{camera}

Camera name mapping is configured in home_runtime.json under
"blueiris_camera_map": {"camera.ha_entity_id": "bi_short_name"}.
"""
from __future__ import annotations

import httpx
import structlog

from avatar_backend.services.home_runtime import load_home_runtime_config

_LOGGER = structlog.get_logger()


class BlueIrisService:
    def __init__(self, bi_url: str = "") -> None:
        self._bi_url = bi_url.rstrip("/")
        runtime = load_home_runtime_config()
        self._camera_map: dict[str, str] = runtime.blueiris_camera_map

    @property
    def available(self) -> bool:
        return bool(self._bi_url)

    def resolve_camera(self, ha_entity_id: str) -> str | None:
        """Map an HA camera entity ID to a Blue Iris short name."""
        return self._camera_map.get(ha_entity_id)

    async def fetch_snapshot(self, ha_entity_id: str) -> bytes | None:
        """Fetch a JPEG snapshot directly from Blue Iris."""
        if not self._bi_url:
            return None
        bi_name = self.resolve_camera(ha_entity_id)
        if not bi_name:
            return None
        url = f"{self._bi_url}/image/{bi_name}?q=60"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content and len(resp.content) > 1000:
                    _LOGGER.info("blueiris.snapshot_ok", camera=bi_name, bytes=len(resp.content))
                    return resp.content
                _LOGGER.warning("blueiris.snapshot_failed", camera=bi_name, status=resp.status_code)
        except Exception as exc:
            _LOGGER.warning("blueiris.snapshot_error", camera=bi_name, exc=str(exc)[:100])
        return None

    async def is_reachable(self) -> bool:
        """Check if Blue Iris is reachable."""
        if not self._bi_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._bi_url}/image/index?q=10")
                return resp.status_code == 200
        except Exception:
            return False

    def mjpeg_url(self, ha_entity_id: str) -> str | None:
        """Return the MJPEG stream URL for a camera."""
        if not self._bi_url:
            return None
        bi_name = self.resolve_camera(ha_entity_id)
        if not bi_name:
            return None
        return f"{self._bi_url}/mjpg/{bi_name}"

"""EnergyService — fetches and aggregates home energy data from HA."""
from __future__ import annotations

from typing import Any

import structlog

from avatar_backend.services.home_runtime import load_home_runtime_config

_LOGGER = structlog.get_logger()


class EnergyService:
    def __init__(self, ha_proxy) -> None:
        self._ha = ha_proxy
        runtime = load_home_runtime_config()
        self._summary_entities = runtime.energy_summary_entities
        self._device_entities = runtime.energy_device_entities

    async def get_summary(self) -> dict[str, Any]:
        """Fetch all summary energy data using cached HA states."""
        if not self._summary_entities:
            return {}
        states = await self._ha._get_all_states_cached()
        state_map = {s["entity_id"]: s for s in states}

        summary = {}
        for key, eid in self._summary_entities.items():
            s = state_map.get(eid)
            if s:
                val = s.get("state", "")
                unit = s.get("attributes", {}).get("unit_of_measurement", "")
                try:
                    summary[key] = {"value": round(float(val), 2), "unit": unit}
                except (ValueError, TypeError):
                    summary[key] = {"value": val, "unit": unit}
            else:
                summary[key] = {"value": None, "unit": ""}
        return summary

    async def get_device_breakdown(self) -> list[dict[str, Any]]:
        """Fetch per-device power consumption using cached HA states."""
        if not self._device_entities:
            return []
        states = await self._ha._get_all_states_cached()
        state_map = {s["entity_id"]: s for s in states}

        devices = []
        for name, eid in self._device_entities.items():
            s = state_map.get(eid)
            if s:
                try:
                    watts = round(float(s.get("state", 0)), 1)
                except (ValueError, TypeError):
                    watts = 0
                devices.append({"name": name, "entity_id": eid, "watts": watts})
        return sorted(devices, key=lambda d: d["watts"], reverse=True)

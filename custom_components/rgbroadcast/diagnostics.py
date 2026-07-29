"""Diagnostics for a RGBroadcast config entry.

Dumps the detected capability of each light and the live engine config, which
is what you need to answer "why does my light look wrong": usually a tier
misdetection or a transition claim the device does not honour.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import RGBroadcastConfigEntry
from .renderer import detect_capabilities


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RGBroadcastConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    engine = entry.runtime_data.engine

    lights: list[dict[str, Any]] = []
    for entity_id in entry.data["lights"]:
        state = hass.states.get(entity_id)
        entry_light: dict[str, Any] = {
            "entity_id": entity_id,
            "role": entry.data.get("roles", {}).get(entity_id),
            "available": state is not None,
        }
        if state is not None:
            caps = detect_capabilities(
                state,
                force_steps=engine.config.force_steps,
                force_tier=engine.config.force_tier,
            )
            entry_light["reported_color_modes"] = state.attributes.get(
                "supported_color_modes"
            )
            entry_light["supported_features"] = state.attributes.get(
                "supported_features"
            )
            entry_light["detected"] = {
                "tier": caps.tier,
                "can_fade": caps.can_fade,
                "advertises_transition": caps.advertises_transition,
                "kelvin_min": caps.kelvin_min,
                "kelvin_max": caps.kelvin_max,
            }
        lights.append(entry_light)

    return {
        "config": asdict(engine.config),
        "running": engine.running,
        "ad_active": engine.ad_active,
        "lights": lights,
    }

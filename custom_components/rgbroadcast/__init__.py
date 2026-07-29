"""The RGBroadcast integration.

One config entry drives one "television": a set of lights that share a scene
clock so they look like a single screen lighting a room. The engine lives in
:mod:`engine`; this module handles the Home Assistant plumbing around it.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BRIGHTNESS_CEILING,
    CONF_DISABLE_CCT,
    CONF_FORCE_STEPS,
    CONF_FORCE_TIER,
    CONF_LIGHTS,
    CONF_ON_STOP,
    CONF_ROLES,
    CONF_TICK_MAX,
    CONF_TICK_MIN,
    DEFAULT_BRIGHTNESS_CEILING,
    DEFAULT_ON_STOP,
    ROLE_SCREEN,
    TIER_AUTO,
)
from .engine import EngineConfig, RGBroadcastEngine

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BINARY_SENSOR,
]


@dataclass
class RGBroadcastRuntime:
    """Per-entry runtime state, hung off ``entry.runtime_data``."""

    engine: RGBroadcastEngine


type RGBroadcastConfigEntry = ConfigEntry[RGBroadcastRuntime]


def _structural_config(entry: RGBroadcastConfigEntry) -> EngineConfig:
    """Build the engine config from the entry's structural options.

    Runtime dials (style, intensity, ad breaks) are entity-backed and restore
    themselves onto this object after setup; the values here are only the
    initial fallbacks and the settings that legitimately need a reload to
    change (tick bounds, capability overrides, stop behaviour).
    """
    options = entry.options
    return EngineConfig(
        tick_min=options.get(CONF_TICK_MIN),
        tick_max=options.get(CONF_TICK_MAX),
        brightness_ceiling=options.get(
            CONF_BRIGHTNESS_CEILING, DEFAULT_BRIGHTNESS_CEILING
        ),
        force_steps=options.get(CONF_FORCE_STEPS, False),
        force_tier=options.get(CONF_FORCE_TIER, TIER_AUTO),
        disable_cct=options.get(CONF_DISABLE_CCT, False),
        on_stop=options.get(CONF_ON_STOP, DEFAULT_ON_STOP),
    )


async def async_setup_entry(hass: HomeAssistant, entry: RGBroadcastConfigEntry) -> bool:
    """Set up a RGBroadcast config entry."""
    entity_ids: list[str] = list(entry.data[CONF_LIGHTS])
    roles: dict[str, str] = {
        eid: entry.data.get(CONF_ROLES, {}).get(eid, ROLE_SCREEN) for eid in entity_ids
    }

    engine = RGBroadcastEngine(
        hass, entity_ids, roles, _structural_config(entry), device_name=entry.title
    )
    entry.runtime_data = RGBroadcastRuntime(engine=engine)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RGBroadcastConfigEntry
) -> bool:
    """Tear down a RGBroadcast config entry."""
    await entry.runtime_data.engine.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_options(
    hass: HomeAssistant, entry: RGBroadcastConfigEntry
) -> None:
    """Apply changed structural options without tearing down the running loop.

    Editing options normally reloads the entry, which would kill the
    simulation. The engine applies the new structural config in place instead,
    deciding for itself how each field takes effect. Runtime dials are owned by
    their own entities and are untouched here.
    """
    await entry.runtime_data.engine.apply_structural(_structural_config(entry))

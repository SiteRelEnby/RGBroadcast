"""Diagnostics and the on/off-degradation repair issue."""

from __future__ import annotations

import random

from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.rgbroadcast.const import DOMAIN, ISSUE_ONOFF_LIGHT
from custom_components.rgbroadcast.engine import EngineConfig, RGBroadcastEngine


def _set(hass: HomeAssistant, entity_id: str, modes: list[str]) -> None:
    hass.states.async_set(
        entity_id,
        "on",
        {
            ATTR_SUPPORTED_COLOR_MODES: modes,
            ATTR_SUPPORTED_FEATURES: LightEntityFeature.TRANSITION,
        },
    )


async def test_onoff_degradation_raises_and_clears_issue(hass: HomeAssistant) -> None:
    """A light that degrades to on/off raises a repair; recovery clears it."""
    engine = RGBroadcastEngine(
        hass,
        ["light.tv"],
        {},
        EngineConfig(),
        rng=random.Random(1),
        device_name="Living Room",
    )
    issue_id = f"{ISSUE_ONOFF_LIGHT}_light.tv"

    _set(hass, "light.tv", [ColorMode.ONOFF])
    assert engine._seed("light.tv") is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    _set(hass, "light.tv", [ColorMode.HS])
    assert engine._seed("light.tv") is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

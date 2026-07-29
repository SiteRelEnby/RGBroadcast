"""The intensity dial: the single realism knob, read live on each tick."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RGBroadcastConfigEntry
from .const import DEFAULT_INTENSITY, INTENSITY_STEP, MAX_INTENSITY, MIN_INTENSITY
from .entity import RuntimeDialEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RGBroadcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the intensity number for a config entry."""
    async_add_entities([RGBroadcastIntensityNumber(entry)])


class RGBroadcastIntensityNumber(RuntimeDialEntity, NumberEntity):
    """Scales the walk deltas and cut frequency. 1.0 is the tuned baseline."""

    _attr_translation_key = "intensity"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = MIN_INTENSITY
    _attr_native_max_value = MAX_INTENSITY
    _attr_native_step = INTENSITY_STEP
    _config_attr = "intensity"

    def __init__(self, entry: RGBroadcastConfigEntry) -> None:
        super().__init__(entry, "intensity")
        self._attr_native_value = DEFAULT_INTENSITY

    def _read_value(self) -> float:
        return self._attr_native_value

    def _write_value(self, value: Any) -> None:
        self._attr_native_value = value

    def _coerce(self, raw: str) -> float | None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        self._apply(value)

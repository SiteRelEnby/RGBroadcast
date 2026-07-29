"""Diagnostic sensor: whether an ad break is currently running.

Useful for dashboards and for automations that want to react to the ad state,
and for confirming the ad-break machine is doing what it should.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RGBroadcastConfigEntry
from .entity import RGBroadcastEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RGBroadcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ad-break diagnostic sensor for a config entry."""
    async_add_entities([RGBroadcastAdBreakSensor(entry)])


class RGBroadcastAdBreakSensor(RGBroadcastEntity, BinarySensorEntity):
    """On while the simulation is in an ad break."""

    _attr_translation_key = "ad_break"
    _attr_icon = "mdi:television-ambient-light"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: RGBroadcastConfigEntry) -> None:
        super().__init__(entry, "ad_break")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.engine.add_listener(self._on_change))

    @callback
    def _on_change(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.engine.ad_active

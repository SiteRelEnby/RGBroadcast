"""Switch entities: the main on/off and the ad-break toggle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.start import async_at_started

from . import RGBroadcastConfigEntry
from .const import DEFAULT_AD_BREAKS
from .entity import RGBroadcastEntity, RuntimeDialEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RGBroadcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches for a config entry."""
    async_add_entities([RGBroadcastSwitch(entry), RGBroadcastAdBreaksSwitch(entry)])


class RGBroadcastSwitch(RGBroadcastEntity, SwitchEntity, RestoreEntity):
    """The main switch. While on, the simulation runs."""

    _attr_name = None  # the device name is the switch name
    _attr_icon = "mdi:television-classic"

    def __init__(self, entry: RGBroadcastConfigEntry) -> None:
        super().__init__(entry, "power")
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off state and resume the simulation if it was on.

        The engine loop does not survive a restart, so a switch that was on
        before the restart has to bring it back. We defer the actual start until
        Home Assistant has fully started, so the target lights and the runtime
        dials have all been restored first.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self._attr_is_on = True

            async def _resume(_now: Any) -> None:
                await self.engine.async_start()
                self.async_write_ha_state()

            self.async_on_remove(async_at_started(self.hass, _resume))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.engine.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
        await self.engine.async_stop()


class RGBroadcastAdBreaksSwitch(RuntimeDialEntity, SwitchEntity):
    """Enables broadcast-style ad breaks. Read live on each tick."""

    _attr_translation_key = "ad_breaks"
    _attr_icon = "mdi:television-play"
    _attr_entity_category = EntityCategory.CONFIG
    _config_attr = "ad_breaks"

    def __init__(self, entry: RGBroadcastConfigEntry) -> None:
        super().__init__(entry, "ad_breaks")
        self._attr_is_on = DEFAULT_AD_BREAKS

    def _read_value(self) -> bool:
        return self._attr_is_on

    def _write_value(self, value: Any) -> None:
        self._attr_is_on = value

    def _coerce(self, raw: str) -> bool:
        return raw == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._apply(False)

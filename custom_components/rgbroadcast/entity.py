"""Shared base for RGBroadcast entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity

from . import RGBroadcastConfigEntry
from .const import DOMAIN
from .engine import RGBroadcastEngine


class RGBroadcastEntity(Entity):
    """Base entity: one device per config entry, all entities attached to it."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: RGBroadcastConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="RGBroadcast",
            model="Occupancy simulation",
            entry_type=None,
        )

    @property
    def engine(self) -> RGBroadcastEngine:
        return self._entry.runtime_data.engine


class RuntimeDialEntity(RGBroadcastEntity, RestoreEntity):
    """A runtime dial: an entity that mirrors one live ``engine.config`` field.

    These are the settings a user adjusts mid-run (style, intensity, ad breaks).
    All three share one contract: restore the last value across a restart, apply
    it to the engine, and on every change write both the entity state and the
    engine config. That contract lives here so it is defined once; a subclass
    supplies only how to read/write its own value and how to parse a stored one.
    """

    #: The ``engine.config`` attribute this dial drives.
    _config_attr: str

    def _read_value(self) -> Any:
        """Return the entity's current value."""
        raise NotImplementedError

    def _write_value(self, value: Any) -> None:
        """Store the value on the entity (without writing HA state)."""
        raise NotImplementedError

    def _coerce(self, raw: str) -> Any | None:
        """Parse a restored state string, or return None to keep the default."""
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (value := self._coerce(last.state)) is not None:
            self._write_value(value)
        setattr(self.engine.config, self._config_attr, self._read_value())

    def _apply(self, value: Any) -> None:
        """Set the value, mirror it onto the engine, and write HA state."""
        self._write_value(value)
        setattr(self.engine.config, self._config_attr, value)
        self.async_write_ha_state()

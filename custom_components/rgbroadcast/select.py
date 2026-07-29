"""The style selector. Read live on each tick, so it is adjustable mid-run."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RGBroadcastConfigEntry
from .const import DEFAULT_STYLE, STYLE_SCHEDULE
from .entity import RuntimeDialEntity
from .styles import SELECTABLE_STYLES

# The user-facing options: every selectable style, plus the schedule mode that
# hands style choice to the time-of-day rotation.
STYLE_OPTIONS: list[str] = [*SELECTABLE_STYLES, STYLE_SCHEDULE]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RGBroadcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the style select for a config entry."""
    async_add_entities([RGBroadcastStyleSelect(entry)])


class RGBroadcastStyleSelect(RuntimeDialEntity, SelectEntity):
    """Selects the content style, or the schedule."""

    _attr_translation_key = "style"
    _attr_icon = "mdi:playlist-play"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = STYLE_OPTIONS
    _config_attr = "style"

    def __init__(self, entry: RGBroadcastConfigEntry) -> None:
        super().__init__(entry, "style")
        self._attr_current_option = DEFAULT_STYLE

    def _read_value(self) -> str:
        return self._attr_current_option

    def _write_value(self, value: Any) -> None:
        self._attr_current_option = value

    def _coerce(self, raw: str) -> str | None:
        return raw if raw in STYLE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        self._apply(option)

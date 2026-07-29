"""Config and options flow tests, and full entry setup/teardown."""

from __future__ import annotations

from homeassistant.components.light import (
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_SUPPORTED_FEATURES, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.rgbroadcast.const import (
    CONF_BRIGHTNESS_CEILING,
    CONF_FORCE_STEPS,
    CONF_LIGHTS,
    CONF_ROLES,
    CONF_SCREEN_LIGHT,
    CONF_SPILL_LIGHTS,
    DOMAIN,
    ROLE_SCREEN,
    ROLE_SPILL,
)


def _add_light(hass: HomeAssistant, entity_id: str, modes: list[str]) -> None:
    hass.states.async_set(
        entity_id,
        "on",
        {
            ATTR_SUPPORTED_COLOR_MODES: modes,
            ATTR_SUPPORTED_FEATURES: LightEntityFeature.TRANSITION,
            "min_color_temp_kelvin": 2702,
            "max_color_temp_kelvin": 6535,
            "brightness": 128,
        },
    )


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The happy path: a hybrid screen light plus a spill light."""
    _add_light(hass, "light.screen", [ColorMode.HS, ColorMode.COLOR_TEMP])
    _add_light(hass, "light.lamp", [ColorMode.HS])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Living Room",
            CONF_SCREEN_LIGHT: "light.screen",
            CONF_SPILL_LIGHTS: ["light.lamp"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room"
    assert result["data"][CONF_LIGHTS] == ["light.screen", "light.lamp"]
    assert result["data"][CONF_ROLES] == {
        "light.screen": ROLE_SCREEN,
        "light.lamp": ROLE_SPILL,
    }


async def test_onoff_light_is_rejected(hass: HomeAssistant) -> None:
    """A smart plug is refused up front with a clear error, not silently."""
    _add_light(hass, "light.plug", [ColorMode.ONOFF])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "Nope", CONF_SCREEN_LIGHT: "light.plug"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"][CONF_SCREEN_LIGHT] == "light_onoff_only"


async def test_screen_excluded_from_spills(hass: HomeAssistant) -> None:
    """A light picked as both screen and spill is only the screen."""
    _add_light(hass, "light.screen", [ColorMode.HS])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Room",
            CONF_SCREEN_LIGHT: "light.screen",
            CONF_SPILL_LIGHTS: ["light.screen"],
        },
    )
    assert result["data"][CONF_LIGHTS] == ["light.screen"]
    assert result["data"][CONF_ROLES] == {"light.screen": ROLE_SCREEN}


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A set-up config entry driving one hybrid light."""
    # The light integration is not loaded in these tests, so provide the
    # services the engine calls. Real Home Assistant always has them.
    async_mock_service(hass, "light", "turn_on")
    async_mock_service(hass, "light", "turn_off")
    _add_light(hass, "light.tv", [ColorMode.HS, ColorMode.COLOR_TEMP])
    mock = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room",
        data={CONF_LIGHTS: ["light.tv"], CONF_ROLES: {"light.tv": ROLE_SCREEN}},
    )
    mock.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock.entry_id)
    await hass.async_block_till_done()
    return mock


async def test_entry_sets_up_all_entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Setup creates the switch, ad-break switch, select, number and sensor."""
    assert entry.state is ConfigEntryState.LOADED
    for suffix in (
        "switch.living_room",
        "switch.living_room_ad_breaks",
        "select.living_room_style",
        "number.living_room_intensity",
        "binary_sensor.living_room_ad_break",
    ):
        assert hass.states.get(suffix) is not None, f"missing {suffix}"


async def test_switch_starts_and_stops_engine(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Toggling the main switch drives the engine."""
    engine = entry.runtime_data.engine
    assert not engine.running

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.living_room"}, blocking=True
    )
    assert engine.running

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.living_room"}, blocking=True
    )
    await hass.async_block_till_done()
    assert not engine.running


async def test_style_select_updates_engine_live(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Changing the style entity updates the engine config immediately."""
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.living_room_style", "option": "film"},
        blocking=True,
    )
    assert entry.runtime_data.engine.config.style == "film"


async def test_options_apply_without_reload(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Saving options mutates the live engine instead of tearing it down."""
    engine_before = entry.runtime_data.engine

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_BRIGHTNESS_CEILING: 40, CONF_FORCE_STEPS: False},
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Same engine object: the loop was not reloaded.
    assert entry.runtime_data.engine is engine_before
    assert entry.runtime_data.engine.config.brightness_ceiling == 40


async def test_unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unloading stops the engine and removes the entities."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

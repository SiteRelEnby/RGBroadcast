"""Engine integration tests against a real Home Assistant test harness.

These exercise the async loop, service dispatch, coordination and ramp-down.
The pure maths is covered exhaustively elsewhere; here we care that the loop
wires it to real service calls correctly.
"""

from __future__ import annotations

import random

from homeassistant.components.light import (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.rgbroadcast.const import ROLE_SCREEN, ROLE_SPILL
from custom_components.rgbroadcast.engine import EngineConfig, RGBroadcastEngine


def _register_light(
    hass: HomeAssistant,
    entity_id: str,
    *,
    modes: list[str],
    features: int = 0,
    kelvin_min: int = 2702,
    kelvin_max: int = 6535,
) -> None:
    hass.states.async_set(
        entity_id,
        "on",
        {
            ATTR_SUPPORTED_COLOR_MODES: modes,
            ATTR_SUPPORTED_FEATURES: features,
            ATTR_MIN_COLOR_TEMP_KELVIN: kelvin_min,
            ATTR_MAX_COLOR_TEMP_KELVIN: kelvin_max,
            "brightness": 128,
            "hs_color": [200.0, 50.0],
        },
    )


async def _run_ticks(engine: RGBroadcastEngine, n: int) -> None:
    """Advance the engine deterministically by calling its internals directly.

    We avoid asyncio.sleep so tests are fast and do not depend on wall-clock.
    """
    from custom_components.rgbroadcast.styles import get_style
    from custom_components.rgbroadcast.walk import should_cut

    engine._lights = [light for e in engine._entity_ids if (light := engine._seed(e))]
    engine._all_fade = all(light.caps.can_fade for light in engine._lights)
    # Reset with the same clock the engine reads, or ad breaks fire instantly.
    engine._ads.reset(engine._now_mono())
    for _ in range(n):
        # Mirror the real loop order: advance ads, resolve style, then render.
        engine._advance_ads()
        style = get_style(engine._effective_style())
        tick = engine._draw_tick()
        is_cut = should_cut(tick, style, engine.config.intensity, engine._rng)
        await engine._render_all(style, tick, is_cut)


@pytest.fixture
def calls(hass: HomeAssistant):
    """Capture light.turn_on service calls."""
    return async_mock_service(hass, "light", "turn_on")


@pytest.fixture
def off_calls(hass: HomeAssistant):
    return async_mock_service(hass, "light", "turn_off")


async def test_engine_drives_a_light(hass: HomeAssistant, calls) -> None:
    """A single hybrid light produces turn_on calls with a colour and brightness."""
    _register_light(
        hass,
        "light.tv",
        modes=[ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.XY],
        features=LightEntityFeature.TRANSITION,
    )
    engine = RGBroadcastEngine(
        hass, ["light.tv"], {}, EngineConfig(style="news"), rng=random.Random(1)
    )
    await _run_ticks(engine, 40)

    assert calls, "engine should have issued turn_on calls"
    for call in calls:
        assert call.data["entity_id"] == "light.tv"
        assert "brightness_pct" in call.data
        has_colour = ATTR_HS_COLOR in call.data or ATTR_COLOR_TEMP_KELVIN in call.data
        assert has_colour


async def test_shared_clock_coordinates_lights(hass: HomeAssistant, calls) -> None:
    """Screen and spill lights step off one clock; spill stays dimmer."""
    _register_light(hass, "light.screen", modes=[ColorMode.HS])
    _register_light(hass, "light.spill", modes=[ColorMode.HS])
    engine = RGBroadcastEngine(
        hass,
        ["light.screen", "light.spill"],
        {"light.screen": ROLE_SCREEN, "light.spill": ROLE_SPILL},
        EngineConfig(style="action"),
        rng=random.Random(2),
    )
    await _run_ticks(engine, 60)

    screen_max = max(
        c.data["brightness_pct"] for c in calls if c.data["entity_id"] == "light.screen"
    )
    spill_max = max(
        c.data["brightness_pct"] for c in calls if c.data["entity_id"] == "light.spill"
    )
    assert spill_max < screen_max, "spill should never out-shine the screen"


async def test_onoff_light_is_skipped(hass: HomeAssistant, calls) -> None:
    """An on/off-only light contributes nothing and does not crash the engine."""
    _register_light(hass, "light.plug", modes=[ColorMode.ONOFF])
    engine = RGBroadcastEngine(
        hass, ["light.plug"], {}, EngineConfig(), rng=random.Random(3)
    )
    await _run_ticks(engine, 10)
    assert not calls


async def test_stepped_light_gets_no_transition(hass: HomeAssistant, calls) -> None:
    """A light that does not advertise transition must never receive one."""
    _register_light(hass, "light.stepped", modes=[ColorMode.HS], features=0)
    engine = RGBroadcastEngine(
        hass, ["light.stepped"], {}, EngineConfig(), rng=random.Random(4)
    )
    await _run_ticks(engine, 30)
    assert calls
    assert all("transition" not in c.data for c in calls)


async def test_identical_payload_is_not_resent(hass: HomeAssistant, calls) -> None:
    """Back-to-back identical states are suppressed to spare the recorder."""
    _register_light(hass, "light.tv", modes=[ColorMode.BRIGHTNESS])
    engine = RGBroadcastEngine(
        hass,
        ["light.tv"],
        {},
        EngineConfig(style="news", brightness_ceiling=1),  # tiny band -> repeats
        rng=random.Random(5),
    )
    await _run_ticks(engine, 100)
    # With a one-percent band, many ticks compute the same brightness; the guard
    # should collapse those into far fewer than 100 calls.
    assert len(calls) < 100


async def test_ramp_down_on_stop(hass: HomeAssistant, calls, off_calls) -> None:
    """Stopping a stepped light ramps brightness down, then turns off."""
    _register_light(hass, "light.stepped", modes=[ColorMode.HS], features=0)
    engine = RGBroadcastEngine(
        hass, ["light.stepped"], {}, EngineConfig(), rng=random.Random(6)
    )
    engine._lights = [engine._seed("light.stepped")]
    calls.clear()
    await engine._settle()
    # Several descending brightness calls, then exactly one turn_off.
    ramp = [c for c in calls if c.data["entity_id"] == "light.stepped"]
    assert len(ramp) >= 4
    assert len(off_calls) == 1


async def test_fade_light_ramps_via_transition(
    hass: HomeAssistant, calls, off_calls
) -> None:
    """A fade-capable light rams down with a single turn_off transition."""
    _register_light(
        hass, "light.tv", modes=[ColorMode.HS], features=LightEntityFeature.TRANSITION
    )
    engine = RGBroadcastEngine(
        hass, ["light.tv"], {}, EngineConfig(), rng=random.Random(7)
    )
    engine._lights = [engine._seed("light.tv")]
    await engine._settle()
    assert len(off_calls) == 1
    assert off_calls[0].data["transition"] > 0


async def test_ad_break_switches_to_ads_style(hass: HomeAssistant, calls) -> None:
    """When the ad-break controller is active, the effective style is ads."""
    _register_light(hass, "light.tv", modes=[ColorMode.HS])
    engine = RGBroadcastEngine(
        hass, ["light.tv"], {}, EngineConfig(style="news"), rng=random.Random(8)
    )
    engine._ad_active = True
    assert engine._effective_style() == "ads"
    engine._ad_active = False
    assert engine._effective_style() == "news"


async def test_live_option_change_takes_effect(hass: HomeAssistant, calls) -> None:
    """Mutating the config mid-run changes behaviour without a restart."""
    _register_light(hass, "light.tv", modes=[ColorMode.HS])
    config = EngineConfig(style="latenight")  # dim
    engine = RGBroadcastEngine(hass, ["light.tv"], {}, config, rng=random.Random(9))
    await _run_ticks(engine, 30)
    dim_max = max(c.data["brightness_pct"] for c in calls)

    calls.clear()
    config.style = "ads"  # bright, applied live
    await _run_ticks(engine, 30)
    bright_max = max(c.data["brightness_pct"] for c in calls)

    assert bright_max > dim_max

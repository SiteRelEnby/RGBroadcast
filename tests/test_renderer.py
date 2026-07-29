"""Tier detection and payload construction.

The local test hardware is all hybrid-tier, so the tiers that matter most here
(cct, brightness, onoff) can only be exercised with mocked states. That is
exactly why the renderer takes a plain ``State`` rather than reaching into hass.
"""

from __future__ import annotations

import random

from homeassistant.components.light import (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import State
import pytest

from custom_components.rgbroadcast.const import (
    TIER_BRIGHTNESS,
    TIER_CCT,
    TIER_COLOUR,
    TIER_HYBRID,
    TIER_ONOFF,
)
from custom_components.rgbroadcast.renderer import (
    build_payload,
    detect_capabilities,
    limits_from,
)
from custom_components.rgbroadcast.styles import get_style
from custom_components.rgbroadcast.walk import initial_state, step

# Native colour-space tuples that must never appear in a payload. Sending these
# reimplements HA's colour conversion and breaks on plain RGB lights.
FORBIDDEN_KEYS = ("rgbw_color", "rgbww_color", "rgb_color", "xy_color")


def make_light(
    modes: list[str],
    *,
    features: int = 0,
    kelvin_min: int | None = None,
    kelvin_max: int | None = None,
) -> State:
    """Build a light State with the given capabilities."""
    attrs: dict = {ATTR_SUPPORTED_COLOR_MODES: modes, ATTR_SUPPORTED_FEATURES: features}
    if kelvin_min is not None:
        attrs[ATTR_MIN_COLOR_TEMP_KELVIN] = kelvin_min
    if kelvin_max is not None:
        attrs[ATTR_MAX_COLOR_TEMP_KELVIN] = kelvin_max
    return State("light.test", "on", attrs)


# --- tier detection ----------------------------------------------------------


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.XY], TIER_HYBRID),
        ([ColorMode.COLOR_TEMP, ColorMode.RGB], TIER_HYBRID),
        ([ColorMode.HS], TIER_COLOUR),
        ([ColorMode.RGB], TIER_COLOUR),
        ([ColorMode.XY], TIER_COLOUR),
        ([ColorMode.RGBWW], TIER_COLOUR),
        ([ColorMode.COLOR_TEMP], TIER_CCT),
        ([ColorMode.BRIGHTNESS], TIER_BRIGHTNESS),
        ([ColorMode.ONOFF], TIER_ONOFF),
        ([], TIER_ONOFF),
        # A white channel is not hue, but it does dim, so it is brightness-tier.
        ([ColorMode.WHITE], TIER_BRIGHTNESS),
    ],
)
def test_tier_detection(modes: list[str], expected: str) -> None:
    """Each capability combination maps to the right tier."""
    caps = detect_capabilities(make_light(modes))
    assert caps.tier == expected


def test_reference_lamp_is_hybrid() -> None:
    """The real SwitchBot lamp: color_temp + hs + xy, advertises transition."""
    lamp = make_light(
        [ColorMode.COLOR_TEMP, ColorMode.HS, ColorMode.XY],
        features=LightEntityFeature.TRANSITION,
        kelvin_min=2702,
        kelvin_max=6535,
    )
    caps = detect_capabilities(lamp)
    assert caps.tier == TIER_HYBRID
    assert caps.has_colour and caps.has_cct
    assert caps.can_fade
    assert caps.advertises_transition
    assert (caps.kelvin_min, caps.kelvin_max) == (2702, 6535)


def test_onoff_light_is_not_simulatable() -> None:
    """A smart plug pretending to be a light has nothing to vary."""
    caps = detect_capabilities(make_light([ColorMode.ONOFF]))
    assert not caps.is_simulatable


def test_force_tier_override() -> None:
    """A misreporting device can be pinned to a tier by hand."""
    lamp = make_light([ColorMode.COLOR_TEMP, ColorMode.HS])
    caps = detect_capabilities(lamp, force_tier=TIER_CCT)
    assert caps.tier == TIER_CCT


def test_kelvin_bounds_fall_back_when_absent() -> None:
    """A CCT light that omits its bounds still gets a usable range."""
    caps = detect_capabilities(make_light([ColorMode.COLOR_TEMP]))
    assert caps.kelvin_min < caps.kelvin_max


# --- transition detection ----------------------------------------------------


def test_transition_detected_from_feature_bit() -> None:
    """Bit 32 is LightEntityFeature.TRANSITION."""
    with_it = detect_capabilities(
        make_light([ColorMode.HS], features=LightEntityFeature.TRANSITION)
    )
    without = detect_capabilities(make_light([ColorMode.HS], features=0))
    assert with_it.can_fade
    assert not without.can_fade


def test_force_steps_overrides_advertised_transition() -> None:
    """The Matter-lies-about-transition case: advertised, but forced off.

    This is the single most important override. The reference lamp advertises
    transition support over Matter and then snaps anyway.
    """
    lamp = make_light([ColorMode.HS], features=LightEntityFeature.TRANSITION)
    caps = detect_capabilities(lamp, force_steps=True)
    assert caps.advertises_transition is True
    assert caps.can_fade is False


# --- payload construction ----------------------------------------------------


def _caps(**kw):
    return detect_capabilities(make_light(**kw))


def test_payload_never_sends_both_colour_and_cct() -> None:
    """Colour and colour temperature are mutually exclusive per call."""
    style = get_style("game")
    caps = _caps(
        modes=[ColorMode.COLOR_TEMP, ColorMode.HS],
        features=LightEntityFeature.TRANSITION,
        kelvin_min=2702,
        kelvin_max=6535,
    )
    limits = limits_from(caps, disable_cct=False, brightness_ceiling=100)
    rng = random.Random(1)
    state = initial_state(style, limits, rng)
    for i in range(3000):
        state = step(state, style, is_cut=(i % 4 == 0), limits=limits, rng=rng)
        payload = build_payload(state, caps, transition=1.0)
        has_hs = ATTR_HS_COLOR in payload
        has_cct = ATTR_COLOR_TEMP_KELVIN in payload
        assert not (has_hs and has_cct), "both colour axes in one call"
        assert has_hs or has_cct, "hybrid light should always carry a colour"


def test_payload_never_sends_native_tuples() -> None:
    """No rgbw/rgbww/rgb/xy tuples, ever, on any tier."""
    for modes in (
        [ColorMode.COLOR_TEMP, ColorMode.RGBWW],
        [ColorMode.RGBW],
        [ColorMode.XY],
        [ColorMode.COLOR_TEMP],
        [ColorMode.BRIGHTNESS],
    ):
        caps = _caps(modes=modes, kelvin_min=2200, kelvin_max=6500)
        style = get_style("action")
        limits = limits_from(caps, disable_cct=False, brightness_ceiling=100)
        rng = random.Random(2)
        state = initial_state(style, limits, rng)
        for i in range(500):
            state = step(state, style, is_cut=(i % 3 == 0), limits=limits, rng=rng)
            payload = build_payload(state, caps)
            for key in FORBIDDEN_KEYS:
                assert key not in payload, f"{key} sent to {modes}"


def test_transition_only_present_when_can_fade() -> None:
    """A stepped light must never receive a transition it will silently drop."""
    style = get_style("news")
    stepped = _caps(modes=[ColorMode.HS], features=0)
    limits = limits_from(stepped, disable_cct=False, brightness_ceiling=100)
    state = initial_state(style, limits, random.Random(3))
    assert ATTR_TRANSITION not in build_payload(state, stepped, transition=2.0)

    fading = _caps(modes=[ColorMode.HS], features=LightEntityFeature.TRANSITION)
    assert ATTR_TRANSITION in build_payload(state, fading, transition=2.0)


def test_brightness_only_payload() -> None:
    """A dimmable-only light varies brightness and nothing else."""
    caps = _caps(modes=[ColorMode.BRIGHTNESS])
    style = get_style("news")
    limits = limits_from(caps, disable_cct=False, brightness_ceiling=100)
    state = initial_state(style, limits, random.Random(4))
    payload = build_payload(state, caps)
    assert set(payload) == {"brightness_pct"}


def test_cct_only_light_always_uses_kelvin() -> None:
    """A CCT-only light renders every state as colour temperature."""
    caps = _caps(modes=[ColorMode.COLOR_TEMP], kelvin_min=2200, kelvin_max=6500)
    style = get_style("film")
    limits = limits_from(caps, disable_cct=False, brightness_ceiling=100)
    rng = random.Random(5)
    state = initial_state(style, limits, rng)
    for i in range(500):
        state = step(state, style, is_cut=(i % 3 == 0), limits=limits, rng=rng)
        payload = build_payload(state, caps)
        assert ATTR_COLOR_TEMP_KELVIN in payload
        assert ATTR_HS_COLOR not in payload


def test_disable_cct_forces_hue_on_hybrid() -> None:
    """The override keeps a hybrid light on hs_color throughout."""
    caps = _caps(
        modes=[ColorMode.COLOR_TEMP, ColorMode.HS], kelvin_min=2702, kelvin_max=6535
    )
    style = get_style("film")  # low saturation, would normally pick CCT
    limits = limits_from(caps, disable_cct=True, brightness_ceiling=100)
    rng = random.Random(6)
    state = initial_state(style, limits, rng)
    for i in range(500):
        state = step(state, style, is_cut=(i % 3 == 0), limits=limits, rng=rng)
        assert ATTR_COLOR_TEMP_KELVIN not in build_payload(state, caps)


def test_hs_values_are_in_range() -> None:
    """Hue in [0, 360), saturation in [0, 100]."""
    caps = _caps(modes=[ColorMode.HS])
    style = get_style("game")
    limits = limits_from(caps, disable_cct=False, brightness_ceiling=100)
    rng = random.Random(7)
    state = initial_state(style, limits, rng)
    for i in range(1000):
        state = step(state, style, is_cut=(i % 4 == 0), limits=limits, rng=rng)
        hue, sat = build_payload(state, caps)[ATTR_HS_COLOR]
        assert 0.0 <= hue < 360.0
        assert 0.0 <= sat <= 100.0

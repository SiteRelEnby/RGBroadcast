"""Turning a walk state into a ``light.turn_on`` call.

This is the layer that meets real hardware, so it carries the hard-won facts
about Home Assistant's light platform. The important ones:

* ``light.turn_on`` has a universal schema. ``hs_color`` and
  ``color_temp_kelvin`` are accepted for every colour-capable light and Home
  Assistant converts them to the device's native colour space. So there is one
  code path, not one per light type.
* Never send native ``rgbw_color`` / ``rgbww_color`` tuples. That reimplements
  Home Assistant's colour conversion and breaks the moment it meets a plain RGB
  light. Let the component convert.
* Colour and colour temperature are mutually exclusive per call. Passing both,
  one silently wins.
* ``transition`` is passed to the integration and silently dropped if the device
  does not honour it. There is no software fallback, and devices routinely
  advertise support and then snap anyway (Matter lights especially). So the
  auto-detected capability is always overridable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    LightEntityFeature,
    brightness_supported,
    color_supported,
    color_temp_supported,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import State

from .const import (
    TIER_AUTO,
    TIER_BRIGHTNESS,
    TIER_CCT,
    TIER_COLOUR,
    TIER_HYBRID,
    TIER_ONOFF,
)
from .walk import Limits, WalkState


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a light can actually do, derived from its state."""

    tier: str
    can_fade: bool
    has_colour: bool
    has_cct: bool
    kelvin_min: int
    kelvin_max: int
    #: True when the device advertised transition support. Kept separate from
    #: ``can_fade`` so the UI can say "this light claims to fade but you have
    #: forced stepped rendering".
    advertises_transition: bool

    @property
    def is_simulatable(self) -> bool:
        """Whether there is anything to vary. An on/off light is a dead end."""
        return self.tier != TIER_ONOFF


def _colour_modes(state: State) -> set[str]:
    modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or []
    return {str(m) for m in modes}


def _detect_tier(modes: set[str]) -> str:
    """Classify a light by what colour axes it can vary.

    Built on Home Assistant's own colour-mode predicates so the taxonomy stays
    in step with the platform. The hybrid/colour/cct/brightness/onoff tiering on
    top is ours.
    """
    has_colour = color_supported(modes)
    has_cct = color_temp_supported(modes)
    if has_colour and has_cct:
        return TIER_HYBRID
    if has_colour:
        return TIER_COLOUR
    if has_cct:
        return TIER_CCT
    if brightness_supported(modes):
        return TIER_BRIGHTNESS
    return TIER_ONOFF


def detect_capabilities(
    state: State,
    *,
    force_steps: bool = False,
    force_tier: str = TIER_AUTO,
) -> Capabilities:
    """Inspect a light's state and decide how to drive it.

    ``force_steps`` and ``force_tier`` are the user overrides for the case where
    a device misreports itself, which is common enough that they are not
    optional extras.
    """
    modes = _colour_modes(state)
    tier = force_tier if force_tier != TIER_AUTO else _detect_tier(modes)

    features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
    advertises = bool(features & LightEntityFeature.TRANSITION)
    can_fade = advertises and not force_steps

    kelvin_min = state.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN)
    kelvin_max = state.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN)

    return Capabilities(
        tier=tier,
        can_fade=can_fade,
        has_colour=tier in (TIER_HYBRID, TIER_COLOUR),
        has_cct=tier in (TIER_HYBRID, TIER_CCT),
        kelvin_min=int(kelvin_min) if kelvin_min else DEFAULT_MIN_KELVIN,
        kelvin_max=int(kelvin_max) if kelvin_max else DEFAULT_MAX_KELVIN,
        advertises_transition=advertises,
    )


def limits_from(
    caps: Capabilities, *, disable_cct: bool, brightness_ceiling: int
) -> Limits:
    """Build the walk's constraints from detected capabilities."""
    return Limits(
        kelvin_min=caps.kelvin_min,
        kelvin_max=caps.kelvin_max,
        has_cct=caps.has_cct,
        has_colour=caps.has_colour,
        disable_cct=disable_cct,
        brightness_ceiling=brightness_ceiling,
    )


def build_payload(
    state: WalkState,
    caps: Capabilities,
    *,
    transition: float | None = None,
) -> dict[str, Any]:
    """Build ``light.turn_on`` service data for one walk state.

    Exactly one colour axis is ever included, chosen by ``state.use_cct``, which
    the walk only flips on a cut. Brightness is always present; a light with no
    colour of any kind still varies brightness, which is the dominant realism
    cue anyway.
    """
    payload: dict[str, Any] = {ATTR_BRIGHTNESS_PCT: int(state.brightness)}

    want_cct = state.use_cct and caps.has_cct
    if want_cct:
        payload[ATTR_COLOR_TEMP_KELVIN] = int(state.kelvin)
    elif caps.has_colour:
        payload[ATTR_HS_COLOR] = [
            round(state.hue % 360, 2),
            round(min(100.0, max(0.0, state.render_saturation)), 2),
        ]

    if transition is not None and caps.can_fade:
        payload[ATTR_TRANSITION] = round(transition, 2)

    return payload

"""The random walk that makes a lamp look like a television.

This module is deliberately pure: no Home Assistant imports, no I/O, no clock.
Everything is a function of the previous state, a style, and an injected
``random.Random``. That is what makes the realism model exhaustively testable
(see ``tests/test_walk.py``), and it is worth protecting.

The realism model it encodes:

1. Random *walk*, not random jump: consecutive states are related.
2. Brightness variance is the dominant cue. It is what reads as "a screen"
   through curtains.
3. Mostly desaturated, occasionally vivid, on a weighted distribution.
4. Occasional hard cuts, expressed as mean seconds between cuts so the rate is
   independent of the tick.
5. Saturation drifts on ordinary ticks and only re-rolls on a cut, otherwise
   saturation jitter fights the hue walk.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import random
from typing import Final

from .const import ROLE_SCREEN, ROLE_SPILL
from .styles import Style

# Hue moves on its own scale rather than reusing the brightness deltas: a 40
# point brightness jump is dramatic, 40 degrees of hue is barely a tint change.
HUE_DRIFT_DEG: Final = 6.0
HUE_CUT_DEG: Final = 50.0

# Colour temperature likewise.
KELVIN_DRIFT: Final = 120
KELVIN_CUT: Final = 700

SAT_DRIFT: Final = 3.0

# Below this saturation the drawn colour is effectively a white, so on a hybrid
# fixture the dedicated warm/cold white LEDs render it better than white mixed
# from RGB. Kept fairly low so that a light with a slight tint still renders
# that tint as colour rather than snapping to white.
CCT_SAT_THRESHOLD: Final = 8.0

# Weighted saturation draw. Real television light is mostly near-white, but a
# lamp watched from inside the room reads better with more colour than the
# through-a-curtain view strictly needs, so the low band is not overwhelming.
SAT_WEIGHT_LOW: Final = 55
SAT_WEIGHT_MED: Final = 80  # cumulative: low 55%, medium 25%, high 20%

#: Reference tick in seconds. Walk deltas are tuned against this dwell and
#: scaled linearly for other tick rates, so a fade-capable light running a 6s
#: tick moves the same amount per second as a stepped light running 2.5s.
REFERENCE_TICK: Final = 2.5


@dataclass(frozen=True, slots=True)
class Limits:
    """Device-derived and user-derived constraints on the walk."""

    #: Kelvin range the device actually supports. Read from the light, never
    #: hardcoded: the reference lamp reports 2702-6535, not 2700-6500.
    kelvin_min: int = 2700
    kelvin_max: int = 6500
    #: Whether colour temperature is available at all.
    has_cct: bool = True
    #: Whether the light can render hue.
    has_colour: bool = True
    #: User override forcing hs_color on hybrid lights.
    disable_cct: bool = False
    #: Scales the whole brightness band, for dim rooms. Percent.
    brightness_ceiling: int = 100


@dataclass(frozen=True, slots=True)
class WalkState:
    """One point in the walk. Percentages, degrees, kelvin."""

    brightness: int
    #: The walk's intrinsic saturation, in the style's own range. Drifts and
    #: re-rolls here; the role profile and the colour dial are applied on top to
    #: produce ``render_saturation``. Kept separate so those factors do not
    #: compound as the walk feeds back into itself each tick.
    hue: float
    saturation: float
    kelvin: int
    #: Saturation actually sent to the light, after the role profile and colour
    #: dial. This is what the payload and the colour/CCT-mode choice use.
    render_saturation: float = 0.0
    #: Whether this state renders via colour temperature rather than hue. Colour
    #: and colour temperature are mutually exclusive per call, and switching
    #: mid-fade jumps, so this only flips on a hard cut.
    use_cct: bool = False


@dataclass(frozen=True, slots=True)
class RoleProfile:
    """How one light behaves relative to the shared scene clock.

    Coordination policy expressed as data, not as branches in the walk. The
    screen role is the identity profile (every factor neutral); other roles are
    the same walk with these factors applied, so a new role is a new profile
    rather than new conditionals.
    """

    #: Fraction of the style's brightness span this light uses, measured up from
    #: the band floor. 1.0 is the full band; a spill light stays low.
    brightness_span: float = 1.0
    #: Multiplier on rendered saturation.
    saturation_factor: float = 1.0
    #: Minimum rendered saturation, so a light can be made to always carry
    #: colour regardless of how desaturated the scene is.
    saturation_floor: float = 0.0
    #: Maximum rendered saturation. None means use the style's own high band; a
    #: value lets a light be more vivid than the style (a deep colour accent).
    saturation_ceiling: float | None = None
    #: How much a cut is softened toward an ordinary drift, in [0, 1]. 0 reacts
    #: to a cut at full magnitude; a spill reacts more gently.
    cut_softness: float = 0.0
    #: Fraction of the reference light's hue this light adopts per tick, so it
    #: trails rather than tracking exactly. 0 means independent.
    hue_lag: float = 0.0


#: The screen does the full television effect: brightness-led, colour when the
#: content has it. Spill lights are colour accents (bias lighting): they take
#: the screen's hue and render it deeper and more saturated, staying dimmer, so
#: a pair of accent lights inject colour notes alongside the main screen rather
#: than washing out to white.
ROLE_PROFILES: Final[dict[str, RoleProfile]] = {
    ROLE_SCREEN: RoleProfile(),
    ROLE_SPILL: RoleProfile(
        brightness_span=0.55,
        saturation_floor=45.0,
        saturation_ceiling=95.0,
        cut_softness=0.4,
        hue_lag=0.6,
    ),
}


def profile_for(role: str) -> RoleProfile:
    """Return the profile for a role, defaulting to the screen (identity)."""
    return ROLE_PROFILES.get(role, ROLE_PROFILES[ROLE_SCREEN])


def brightness_band(style: Style, limits: Limits) -> tuple[int, int]:
    """Return the effective brightness band, honouring the ceiling.

    Both ends are scaled. Scaling only the top (as the original design sketched)
    inverts the band for styles with a high floor: ``news`` is 55-75, and a 20%
    ceiling would produce 55-15, which a min/max clamp silently pins to 55. The
    dim-room control would then make the light *brighter* than its own ceiling.
    """
    scale = max(1, min(100, limits.brightness_ceiling)) / 100
    low = max(1, round(style.bri[0] * scale))
    high = max(low + 1, round(style.bri[1] * scale))
    return low, min(100, high)


def kelvin_band(style: Style, limits: Limits) -> tuple[int, int]:
    """Return the style's colour temperature band clamped to the device's.

    Where the two do not overlap at all (a warm-only bulb asked for ``news``,
    which wants 5000-6500K) both ends collapse onto the nearest achievable
    value, so the light does the closest thing it can rather than producing an
    inverted range.
    """
    low = min(max(style.cct[0], limits.kelvin_min), limits.kelvin_max)
    high = min(max(style.cct[1], limits.kelvin_min), limits.kelvin_max)
    return low, high


def cut_probability(tick: float, style: Style, intensity: float) -> float:
    """Per-tick probability of a hard cut.

    Derived from mean seconds between cuts so that changing the tick rate does
    not change how often the content cuts. Intensity makes cuts more frequent.
    """
    mean = max(0.5, style.mean_cut / max(intensity, 0.01))
    return min(1.0, tick / mean)


def should_cut(tick: float, style: Style, intensity: float, rng: random.Random) -> bool:
    """Roll for a hard cut on this tick."""
    return rng.random() < cut_probability(tick, style, intensity)


def draw_tick(tick_min: float, tick_max: float, rng: random.Random) -> float:
    """Draw a jittered tick length. A metronomic interval is itself a tell."""
    low, high = min(tick_min, tick_max), max(tick_min, tick_max)
    return rng.uniform(low, high)


def _hue_delta(target: float, current: float) -> float:
    """Shortest-path signed hue difference, in degrees.

    Without wraparound handling a walk from 350 to 10 degrees traverses the
    entire spectrum, which looks like a rainbow sweep rather than a tint shift.
    """
    return ((target - current + 180) % 360) - 180


def _hue_in_band(hue: float, style: Style) -> bool:
    """Whether a hue lies inside the style's band."""
    if style.hue_is_full_circle:
        return True
    low, high = style.hue
    hue %= 360
    if style.hue_wraps:
        return hue >= low or hue <= high
    return low <= hue <= high


def _clamp_hue(hue: float, style: Style) -> float:
    """Constrain a hue to the style's band, snapping to the nearer edge."""
    hue %= 360
    if _hue_in_band(hue, style):
        return hue
    low, high = style.hue
    # Circular distance to each edge, so a wrapped band snaps sensibly.
    if abs(_hue_delta(low, hue)) <= abs(_hue_delta(high, hue)):
        return float(low % 360)
    return float(high % 360)


def _draw_saturation(style: Style, rng: random.Random) -> float:
    """Draw a fresh saturation from the style's weighted distribution.

    Weighted low/medium/high per SAT_WEIGHT_* (roughly 55/25/20). A uniform draw
    looks like a disco; television is mostly pale light with occasional colour.
    """
    low, med, high = style.sat
    roll = rng.random() * 100
    if roll <= SAT_WEIGHT_LOW:
        return float(rng.uniform(0, low))
    if roll <= SAT_WEIGHT_MED:
        return float(rng.uniform(low, med))
    return float(rng.uniform(med, high))


def initial_state(
    style: Style,
    limits: Limits,
    rng: random.Random,
    *,
    profile: RoleProfile | None = None,
    colour: float = 1.0,
) -> WalkState:
    """Seed a walk with a plausible starting point inside the style."""
    profile = profile or RoleProfile()
    low, high = brightness_band(style, limits)
    high = max(low + 1, round(low + (high - low) * profile.brightness_span))
    k_low, k_high = kelvin_band(style, limits)
    intrinsic = _draw_saturation(style, rng)
    rendered = render_saturation(intrinsic, style, profile, colour)
    hue = (
        rng.uniform(0, 360)
        if style.hue_is_full_circle
        else _clamp_hue(rng.uniform(style.hue[0], style.hue[0] + 30), style)
    )
    return WalkState(
        brightness=rng.randint(low, high),
        hue=hue,
        saturation=intrinsic,
        render_saturation=rendered,
        kelvin=rng.randint(k_low, k_high),
        use_cct=_resolve_use_cct(rendered, limits),
    )


def _resolve_use_cct(saturation: float, limits: Limits) -> bool:
    """Decide whether a state renders as colour temperature.

    Colour and colour temperature are mutually exclusive in a single
    ``light.turn_on`` call, so this picks exactly one axis.
    """
    if not limits.has_cct or limits.disable_cct:
        return False
    if not limits.has_colour:
        return True
    return saturation < CCT_SAT_THRESHOLD


def render_saturation(
    intrinsic: float, style: Style, profile: RoleProfile, colour: float
) -> float:
    """Apply the role profile and the colour dial to a walk saturation.

    Kept separate from the walk so it can be recomputed fresh each tick without
    the profile factor compounding as the walk feeds its own output back in, and
    so the colour dial takes effect immediately rather than only on the next
    cut. ``colour`` is the user's saturation bias: 0 forces white, 1 is the
    baseline, higher is more vivid.
    """
    ceiling = (
        profile.saturation_ceiling
        if profile.saturation_ceiling is not None
        else float(style.sat[2])
    )
    biased = intrinsic * profile.saturation_factor * max(0.0, colour)
    # The floor scales with the colour dial too, so turning colour down all the
    # way still reaches white on an accent light.
    floor = profile.saturation_floor * min(1.0, max(0.0, colour))
    return min(ceiling, max(floor, biased))


def step(
    state: WalkState,
    style: Style,
    *,
    is_cut: bool,
    intensity: float = 1.0,
    colour: float = 1.0,
    limits: Limits | None = None,
    rng: random.Random,
    profile: RoleProfile | None = None,
    tick: float = REFERENCE_TICK,
    reference_hue: float | None = None,
) -> WalkState:
    """Advance the walk by one tick.

    ``tick`` is the dwell length in seconds; deltas are scaled by
    ``tick / REFERENCE_TICK`` so a slow, fade-capable light covers the same
    ground per second as a fast, stepped one. ``profile`` is the light's role
    policy (default: the screen, i.e. identity). ``colour`` is the saturation
    bias dial. ``reference_hue`` is the light the profile trails, if any.
    """
    limits = limits or Limits()
    profile = profile or RoleProfile()

    # A cut is softened toward an ordinary drift by the profile, so an accent
    # light reacts to the same cut without the room strobing in unison.
    cut_magnitude = style.jump + (style.drift - style.jump) * profile.cut_softness
    magnitude = cut_magnitude if is_cut else style.drift

    scale = max(0.05, intensity) * max(0.05, tick / REFERENCE_TICK)
    delta = max(1, round(magnitude * scale))

    # --- brightness ---------------------------------------------------------
    low, high = brightness_band(style, limits)
    high = max(low + 1, round(low + (high - low) * profile.brightness_span))
    brightness = min(high, max(low, state.brightness + rng.randint(-delta, delta)))

    # --- saturation ---------------------------------------------------------
    # The intrinsic saturation lives in the style's own range: re-rolled on a
    # cut, drifting otherwise so it does not fight the hue walk. The profile and
    # colour dial are layered on top to get the value actually rendered.
    if is_cut:
        intrinsic = _draw_saturation(style, rng)
    else:
        intrinsic = state.saturation + rng.uniform(-SAT_DRIFT, SAT_DRIFT)
    intrinsic = min(float(style.sat[2]), max(0.0, intrinsic))
    rendered = render_saturation(intrinsic, style, profile, colour)

    # --- hue ----------------------------------------------------------------
    hue_delta = (HUE_CUT_DEG if is_cut else HUE_DRIFT_DEG) * scale
    hue = state.hue + rng.uniform(-hue_delta, hue_delta)
    if profile.hue_lag and reference_hue is not None:
        # Trail the reference light rather than tracking it exactly.
        hue += _hue_delta(reference_hue, hue) * profile.hue_lag
    hue = _clamp_hue(hue, style) if not style.hue_is_full_circle else hue % 360

    # --- colour temperature -------------------------------------------------
    k_low, k_high = kelvin_band(style, limits)
    k_delta = max(1, round((KELVIN_CUT if is_cut else KELVIN_DRIFT) * scale))
    kelvin = min(k_high, max(k_low, state.kelvin + rng.randint(-k_delta, k_delta)))

    # --- render mode --------------------------------------------------------
    # The colour/CCT choice follows the rendered saturation, and is locked
    # between cuts (a mid-fade mode switch jumps). The capability guards apply
    # every tick, so a live disable_cct toggle is honoured at once.
    use_cct = _resolve_use_cct(rendered, limits) if is_cut else state.use_cct
    if not limits.has_cct or limits.disable_cct:
        use_cct = False
    elif not limits.has_colour:
        use_cct = True

    return replace(
        state,
        brightness=brightness,
        hue=hue,
        saturation=intrinsic,
        render_saturation=rendered,
        kelvin=kelvin,
        use_cct=use_cct,
    )

"""Bounds and distribution tests for the walk.

The walk is what makes the effect convincing, and it runs unattended for hours,
so every constraint it claims to honour is asserted here over enough iterations
that a rare escape cannot hide. None of this needs Home Assistant.
"""

from __future__ import annotations

import random

import pytest

from custom_components.rgbroadcast.const import ROLE_SCREEN, ROLE_SPILL
from custom_components.rgbroadcast.styles import STYLES, Style, get_style
from custom_components.rgbroadcast.walk import (
    CCT_SAT_THRESHOLD,
    REFERENCE_TICK,
    Limits,
    WalkState,
    _draw_saturation,
    _hue_delta,
    _hue_in_band,
    brightness_band,
    cut_probability,
    initial_state,
    kelvin_band,
    profile_for,
    should_cut,
    step,
)

ALL_STYLES = list(STYLES.values())
INTENSITIES = [0.5, 1.0, 2.0]
CEILINGS = [100, 50, 20]

ITERATIONS = 2000


def _run(
    style: Style,
    limits: Limits,
    *,
    intensity: float = 1.0,
    role: str = ROLE_SCREEN,
    iterations: int = ITERATIONS,
    seed: int = 1234,
    tick: float = REFERENCE_TICK,
):
    """Run the walk and yield every state it passes through."""
    rng = random.Random(seed)
    profile = profile_for(role)
    state = initial_state(style, limits, rng)
    states = [state]
    for i in range(iterations):
        # Force a healthy mix of cuts and drifts regardless of style timing.
        is_cut = i % 7 == 0
        state = step(
            state,
            style,
            is_cut=is_cut,
            intensity=intensity,
            limits=limits,
            rng=rng,
            profile=profile,
            tick=tick,
        )
        states.append(state)
    return states


@pytest.mark.parametrize("style", ALL_STYLES, ids=lambda s: s.name)
@pytest.mark.parametrize("intensity", INTENSITIES)
@pytest.mark.parametrize("ceiling", CEILINGS)
def test_walk_stays_in_bounds(style: Style, intensity: float, ceiling: int) -> None:
    """Brightness, saturation, hue and kelvin never leave their bands."""
    limits = Limits(kelvin_min=2702, kelvin_max=6535, brightness_ceiling=ceiling)
    b_low, b_high = brightness_band(style, limits)
    k_low, k_high = kelvin_band(style, limits)

    for state in _run(style, limits, intensity=intensity):
        assert b_low <= state.brightness <= b_high, f"brightness {state.brightness}"
        assert 0.0 <= state.saturation <= style.sat[2], f"sat {state.saturation}"
        assert 0.0 <= state.hue < 360.0, f"hue {state.hue}"
        assert _hue_in_band(state.hue, style), f"hue {state.hue} outside {style.hue}"
        assert k_low <= state.kelvin <= k_high, f"kelvin {state.kelvin}"


@pytest.mark.parametrize("style", ALL_STYLES, ids=lambda s: s.name)
def test_brightness_band_never_inverts(style: Style) -> None:
    """A low ceiling must not invert the band.

    Scaling only the top of the band (as originally specified) turns news 55-75
    into 55-15 at a 20% ceiling, which a min/max clamp silently pins to 55: the
    dim-room control would make the light brighter than its own ceiling.
    """
    previous_high = None
    for ceiling in (100, 75, 50, 25, 10, 1):
        low, high = brightness_band(style, Limits(brightness_ceiling=ceiling))
        assert low < high, f"inverted band at ceiling {ceiling}: {low}-{high}"
        assert 1 <= low <= 100
        assert 1 <= high <= 100
        if previous_high is not None:
            assert high <= previous_high, "lowering the ceiling must not brighten"
        previous_high = high


def test_brightness_ceiling_actually_dims() -> None:
    """The ceiling is a real constraint, not decoration."""
    style = get_style("news")
    full = brightness_band(style, Limits(brightness_ceiling=100))
    dim = brightness_band(style, Limits(brightness_ceiling=20))
    assert dim[1] < full[0], "a 20% ceiling should sit below the full band"


# --- hue ---------------------------------------------------------------------


def test_hue_delta_takes_shortest_path() -> None:
    """Crossing 0 degrees must not sweep the whole spectrum."""
    assert _hue_delta(10, 350) == pytest.approx(20)
    assert _hue_delta(350, 10) == pytest.approx(-20)
    assert _hue_delta(90, 45) == pytest.approx(45)
    # Exactly antipodal: either direction is equally short, so only the
    # magnitude is meaningful.
    assert abs(_hue_delta(180, 0)) == pytest.approx(180)
    # The result is always the shorter arc, never the long way round.
    for target in range(0, 360, 7):
        for current in range(0, 360, 11):
            assert abs(_hue_delta(target, current)) <= 180


def test_wrapping_hue_band_is_honoured() -> None:
    """A band written as (340, 20) means 340..360..20, not 20..340."""
    style = Style("wrapped", 10, (20, 80), 5, 30, (340, 20), (5, 15, 40), (2700, 4000))
    assert style.hue_wraps
    limits = Limits()
    seen_low_side = seen_high_side = False
    for state in _run(style, limits, iterations=4000):
        assert _hue_in_band(state.hue, style), f"hue {state.hue} escaped (340, 20)"
        if state.hue >= 340:
            seen_high_side = True
        if state.hue <= 20:
            seen_low_side = True
    assert seen_low_side and seen_high_side, "walk should cross the wrap point"


def test_full_circle_hue_is_unconstrained() -> None:
    """Styles with a full-circle band roam freely and wrap cleanly."""
    style = get_style("game")
    assert style.hue_is_full_circle
    hues = [s.hue for s in _run(style, Limits(), iterations=4000)]
    assert all(0.0 <= h < 360.0 for h in hues)
    assert max(hues) - min(hues) > 180, "should roam widely over the wheel"


# --- saturation --------------------------------------------------------------


def test_saturation_distribution_is_weighted() -> None:
    """Weighted toward low, but with real colour: roughly 55/25/20."""
    style = get_style("film")
    rng = random.Random(7)
    low = med = high = 0
    draws = 40000
    for _ in range(draws):
        value = _draw_saturation(style, rng)
        if value <= style.sat[0]:
            low += 1
        elif value <= style.sat[1]:
            med += 1
        else:
            high += 1
    assert low / draws == pytest.approx(0.55, abs=0.02)
    assert med / draws == pytest.approx(0.25, abs=0.02)
    assert high / draws == pytest.approx(0.20, abs=0.02)


def test_saturation_only_rerolls_on_a_cut() -> None:
    """Between cuts saturation drifts, so it does not fight the hue walk."""
    style = get_style("game")  # wide saturation band, so a re-roll is obvious
    rng = random.Random(3)
    limits = Limits()
    state = initial_state(style, limits, rng)
    for _ in range(500):
        nxt = step(state, style, is_cut=False, limits=limits, rng=rng)
        assert abs(nxt.saturation - state.saturation) <= 3.0 + 1e-9
        state = nxt


# --- render mode -------------------------------------------------------------


def test_render_mode_only_flips_on_a_cut() -> None:
    """Switching hue/CCT mid-drift produces a visible jump, so it is locked."""
    style = get_style("film")
    limits = Limits(has_cct=True, has_colour=True)
    rng = random.Random(11)
    state = initial_state(style, limits, rng)
    for i in range(3000):
        is_cut = i % 5 == 0
        nxt = step(state, style, is_cut=is_cut, limits=limits, rng=rng)
        if not is_cut:
            assert nxt.use_cct == state.use_cct, "mode changed mid-drift"
        state = nxt


def test_render_mode_follows_saturation_on_a_cut() -> None:
    """Low saturation renders as colour temperature, high as hue."""
    style = get_style("game")
    limits = Limits(has_cct=True, has_colour=True)
    rng = random.Random(5)
    state = initial_state(style, limits, rng)
    checked = 0
    for _ in range(2000):
        state = step(state, style, is_cut=True, limits=limits, rng=rng)
        assert state.use_cct == (state.saturation < CCT_SAT_THRESHOLD)
        checked += 1
    assert checked


@pytest.mark.parametrize(
    ("has_cct", "has_colour", "expected"),
    [
        (True, True, None),  # either, depending on saturation
        (False, True, False),  # colour only: never CCT
        (True, False, True),  # cct only: always CCT
    ],
)
def test_render_mode_respects_capability(
    has_cct: bool, has_colour: bool, expected: bool | None
) -> None:
    """A light without hue cannot render hue, and vice versa."""
    style = get_style("film")
    limits = Limits(has_cct=has_cct, has_colour=has_colour)
    for state in _run(style, limits, iterations=500):
        if expected is not None:
            assert state.use_cct is expected


def test_disable_cct_override_forces_hue() -> None:
    """The user override wins over auto-detection."""
    style = get_style("film")
    limits = Limits(has_cct=True, has_colour=True, disable_cct=True)
    assert all(not s.use_cct for s in _run(style, limits, iterations=500))


# --- colour temperature ------------------------------------------------------


def test_kelvin_band_intersects_device_and_style() -> None:
    """The device's real range wins over the style's preference."""
    style = get_style("news")  # wants 5000-6500K
    low, high = kelvin_band(style, Limits(kelvin_min=2702, kelvin_max=6535))
    assert (low, high) == (5000, 6500)


def test_kelvin_band_collapses_when_ranges_do_not_overlap() -> None:
    """A warm-only bulb asked for news does the closest thing it can."""
    style = get_style("news")  # wants 5000-6500K
    low, high = kelvin_band(style, Limits(kelvin_min=2200, kelvin_max=4000))
    assert low <= high, "must never produce an inverted range"
    assert (low, high) == (4000, 4000)


def test_kelvin_never_leaves_device_range() -> None:
    """Even with a mismatched style, output stays achievable."""
    style = get_style("news")
    limits = Limits(kelvin_min=2200, kelvin_max=4000)
    for state in _run(style, limits, iterations=1000):
        assert 2200 <= state.kelvin <= 4000


# --- cuts --------------------------------------------------------------------


@pytest.mark.parametrize("style", ALL_STYLES, ids=lambda s: s.name)
def test_cut_probability_matches_mean_cut(style: Style) -> None:
    """Observed cut rate must match the style's mean seconds between cuts."""
    tick = 2.5
    rng = random.Random(99)
    trials = 40000
    cuts = sum(should_cut(tick, style, 1.0, rng) for _ in range(trials))
    expected = cut_probability(tick, style, 1.0)
    assert cuts / trials == pytest.approx(expected, abs=0.01)


def test_cut_rate_is_independent_of_tick() -> None:
    """Halving the tick must halve the per-tick probability, not the cut rate."""
    style = get_style("film")
    fast = cut_probability(1.25, style, 1.0) / 1.25
    slow = cut_probability(5.0, style, 1.0) / 5.0
    assert fast == pytest.approx(slow)


def test_intensity_increases_cut_frequency() -> None:
    """Intensity is the single realism dial, and it must actually do something."""
    style = get_style("film")
    assert cut_probability(2.5, style, 2.0) > cut_probability(2.5, style, 1.0)
    assert cut_probability(2.5, style, 0.5) < cut_probability(2.5, style, 1.0)


def test_cut_probability_is_clamped() -> None:
    """A very long tick against a fast style cannot exceed certainty."""
    style = get_style("ads")  # mean_cut 3s
    assert cut_probability(120.0, style, 2.0) == 1.0


# --- coordination roles ------------------------------------------------------


@pytest.mark.parametrize("style", ALL_STYLES, ids=lambda s: s.name)
def test_spill_stays_dimmer_than_the_screen(style: Style) -> None:
    """Ambient bounce should not out-shine the screen it is bouncing from."""
    limits = Limits()
    screen_low, screen_high = brightness_band(style, limits)
    spill_max = max(s.brightness for s in _run(style, limits, role=ROLE_SPILL))
    assert screen_low <= spill_max < screen_high


def test_spill_trails_the_screen_hue() -> None:
    """A spill light follows the screen rather than wandering independently."""
    style = get_style("game")
    limits = Limits()
    rng = random.Random(17)
    screen = initial_state(style, limits, rng)
    spill = initial_state(style, limits, rng)

    # Drive the screen to a fixed hue and let the spill chase it.
    screen = WalkState(brightness=50, hue=200.0, saturation=40.0, kelvin=4000)
    spill_profile = profile_for(ROLE_SPILL)
    for _ in range(60):
        spill = step(
            spill,
            style,
            is_cut=False,
            limits=limits,
            rng=rng,
            profile=spill_profile,
            reference_hue=screen.hue,
        )
    assert abs(_hue_delta(screen.hue, spill.hue)) < 40, "spill should converge"


def _mean_render_sat(style, limits, *, colour, role=ROLE_SCREEN, seed=1):
    rng = random.Random(seed)
    profile = profile_for(role)
    state = initial_state(style, limits, rng, profile=profile, colour=colour)
    vals = []
    for i in range(600):
        state = step(
            state,
            style,
            is_cut=(i % 5 == 0),
            colour=colour,
            limits=limits,
            rng=rng,
            profile=profile,
        )
        vals.append(state.render_saturation)
    return sum(vals) / len(vals)


def test_colour_dial_zero_renders_white() -> None:
    """Colour 0 pins rendered saturation to zero, so everything is white/CCT."""
    style = get_style("game")  # the most saturated style
    limits = Limits()
    rng = random.Random(3)
    state = initial_state(style, limits, rng, colour=0.0)
    for i in range(300):
        state = step(
            state, style, is_cut=(i % 4 == 0), colour=0.0, limits=limits, rng=rng
        )
        assert state.render_saturation == 0.0


def test_colour_dial_scales_saturation_up() -> None:
    """Turning colour up makes the rendered light more saturated."""
    style = get_style("film")
    limits = Limits()
    assert (
        _mean_render_sat(style, limits, colour=2.0)
        > _mean_render_sat(style, limits, colour=1.0)
        > _mean_render_sat(style, limits, colour=0.5)
    )


def test_spill_injects_colour_even_on_a_pale_style() -> None:
    """A spill accent stays colourful even when the style is near-white."""
    style = get_style("news")  # lowest-saturation style
    limits = Limits()
    profile = profile_for(ROLE_SPILL)
    rng = random.Random(4)
    state = initial_state(style, limits, rng, profile=profile)
    for i in range(400):
        state = step(
            state, style, is_cut=(i % 6 == 0), limits=limits, rng=rng, profile=profile
        )
        # Floored well above the CCT-white threshold: the accent shows colour.
        assert state.render_saturation >= profile.saturation_floor - 1e-9


def test_spill_saturation_does_not_decay_between_cuts() -> None:
    """Regression: the profile factor must not compound as the walk feeds back.

    A spill run for many consecutive non-cut ticks must stay colourful rather
    than decaying toward white, which is what happened when the saturation
    factor was re-applied to the walk's own output every tick.
    """
    style = get_style("sport")
    limits = Limits()
    profile = profile_for(ROLE_SPILL)
    rng = random.Random(5)
    state = initial_state(style, limits, rng, profile=profile)
    for _ in range(300):  # all non-cut
        state = step(
            state, style, is_cut=False, limits=limits, rng=rng, profile=profile
        )
    assert state.render_saturation >= profile.saturation_floor - 1e-9


# --- delta scaling -----------------------------------------------------------


def test_delta_scale_keeps_pace_across_tick_rates() -> None:
    """A slow fade-capable light should cover the same ground per second.

    Otherwise a light that dwells for 6s at a delta tuned for 2.5s crawls, and
    the two renderers visibly disagree about how lively the content is.
    """
    style = get_style("action")
    limits = Limits()

    def total_travel(tick: float, ticks: int) -> float:
        rng = random.Random(42)
        state = initial_state(style, limits, rng)
        travel = 0.0
        for _ in range(ticks):
            nxt = step(
                state,
                style,
                is_cut=False,
                limits=limits,
                rng=rng,
                tick=tick,
            )
            travel += abs(nxt.brightness - state.brightness)
            state = nxt
        return travel

    # 1000 ticks at the reference dwell versus 500 at double: same elapsed time.
    fast = total_travel(REFERENCE_TICK, 1000)
    slow = total_travel(REFERENCE_TICK * 2, 500)
    assert slow == pytest.approx(fast, rel=0.25)


# --- style bundle validation -------------------------------------------------


def test_shipped_styles_are_valid() -> None:
    """Every shipped bundle satisfies its own invariants."""
    for name, style in STYLES.items():
        assert style.name == name
        assert style.bri[0] < style.bri[1]
        assert style.sat[0] <= style.sat[1] <= style.sat[2]
        assert style.cct[0] < style.cct[1]
        assert style.mean_cut > 0
        assert style.drift < style.jump, "a cut should move more than a drift"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bri": (80, 20)},
        {"sat": (30, 10, 50)},
        {"cct": (6000, 3000)},
        {"mean_cut": 0},
    ],
)
def test_invalid_style_is_rejected(kwargs: dict) -> None:
    """Bad bundles fail loudly at import, not silently at 3am."""
    base = {
        "name": "bad",
        "mean_cut": 10,
        "bri": (20, 80),
        "drift": 5,
        "jump": 30,
        "hue": (0, 360),
        "sat": (5, 15, 40),
        "cct": (2700, 4000),
    }
    with pytest.raises(ValueError):
        Style(**{**base, **kwargs})


def test_unknown_style_falls_back_rather_than_raising() -> None:
    """A missing style must not stop a simulation that is meant to look lived-in."""
    assert get_style("nonsense").name == "news"

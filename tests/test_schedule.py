"""Tests for the pure scheduling and ad-break state machines."""

from __future__ import annotations

from datetime import time
import random

from custom_components.rgbroadcast.const import (
    AD_BREAK_MAX_S,
    AD_BREAK_MIN_S,
    AD_CONTENT_MAX_S,
    AD_CONTENT_MIN_S,
)
from custom_components.rgbroadcast.schedule import (
    DEFAULT_SCHEDULE,
    AdBreakController,
    ScheduleSlot,
    active_style,
    materialise_schedule,
)

# --- style scheduler ---------------------------------------------------------


def test_materialise_is_sorted_and_complete() -> None:
    """Every slot appears once, sorted by start minute."""
    rng = random.Random(1)
    mat = materialise_schedule(DEFAULT_SCHEDULE, rng)
    assert len(mat) == len(DEFAULT_SCHEDULE)
    minutes = [m for m, _ in mat]
    assert minutes == sorted(minutes)


def test_active_style_picks_the_current_slot() -> None:
    """A concrete schedule resolves to the right style through the evening."""
    # active_style contracts on start-minute-sorted input, as materialise gives.
    mat = ((30, "latenight"), (19 * 60, "news"), (21 * 60, "film"))
    assert active_style(time(20, 0), mat) == "news"
    assert active_style(time(22, 0), mat) == "film"
    assert active_style(time(0, 45), mat) == "latenight"


def test_before_first_slot_wraps_to_last_slot() -> None:
    """Pre-dawn hours belong to the previous evening's final slot."""
    mat = ((19 * 60, "news"), (21 * 60, "film"))
    # 03:00 is before the earliest slot (19:00), so it wraps to the last (film).
    assert active_style(time(3, 0), mat) == "film"


def test_overnight_latenight_carries_into_small_hours() -> None:
    """A 00:30 latenight slot should still be active at 02:00."""
    mat = materialise_schedule(DEFAULT_SCHEDULE, random.Random(0))
    # latenight is the only after-midnight slot, so by 04:00 it must be active
    # regardless of jitter.
    assert active_style(time(4, 0), mat) == "latenight"


def test_empty_schedule_returns_none() -> None:
    assert active_style(time(12, 0), ()) is None


def test_schedule_varies_between_nights() -> None:
    """The multi-option slot should not pick the same style every night."""
    slot = ScheduleSlot(time(21, 0), ("film", "sport", "game"), jitter_min=0)
    chosen = {
        materialise_schedule((slot,), random.Random(seed))[0][1] for seed in range(50)
    }
    assert len(chosen) > 1, "a multi-option slot should vary across nights"


def test_jitter_stays_within_bounds() -> None:
    """Changeover jitter never exceeds the slot's declared window."""
    slot = ScheduleSlot(time(21, 0), ("film",), jitter_min=40)
    base = 21 * 60
    for seed in range(200):
        minute = materialise_schedule((slot,), random.Random(seed))[0][0]
        # Account for wrap near midnight by comparing circularly.
        diff = min((minute - base) % 1440, (base - minute) % 1440)
        assert diff <= 40


# --- ad breaks ---------------------------------------------------------------


def test_ad_break_cycle_timing() -> None:
    """Content lasts 12-18 min, breaks 2-4 min, and they alternate."""
    ctrl = AdBreakController(random.Random(3))
    now = 1000.0
    ctrl.reset(now)
    assert not ctrl.update(now, "news", True)

    # Advance in small steps and record transitions.
    transitions = []
    prev = False
    for _ in range(200000):
        now += 1.0
        active = ctrl.update(now, "news", True)
        if active != prev:
            transitions.append((now, active))
            prev = active
        if len(transitions) >= 6:
            break

    # First: content -> ad. Duration of that content window is within bounds.
    content_len = transitions[0][0] - 1000.0
    assert AD_CONTENT_MIN_S <= content_len <= AD_CONTENT_MAX_S + 1

    # Then ad -> content: the ad's length.
    ad_len = transitions[1][0] - transitions[0][0]
    assert AD_BREAK_MIN_S <= ad_len <= AD_BREAK_MAX_S + 1

    # Alternation.
    assert [a for _, a in transitions] == [True, False, True, False, True, False]


def test_ad_breaks_disabled_never_fire() -> None:
    """With ad breaks off, the flag stays down no matter how much time passes."""
    ctrl = AdBreakController(random.Random(4))
    now = 0.0
    ctrl.reset(now)
    for _ in range(10000):
        now += 60.0
        assert not ctrl.update(now, "news", enabled=False)


def test_exempt_style_suppresses_ads() -> None:
    """Gaming has no ad breaks."""
    ctrl = AdBreakController(random.Random(5))
    now = 0.0
    ctrl.reset(now)
    for _ in range(10000):
        now += 60.0
        assert not ctrl.update(now, "game", enabled=True)


def test_re_enabling_does_not_immediately_fire() -> None:
    """Coming back from an exempt style rearms with a fresh content window.

    Otherwise a stale boundary from before the exemption would trip an ad break
    the instant the style changed back, which looks like a glitch.
    """
    ctrl = AdBreakController(random.Random(6))
    now = 0.0
    ctrl.reset(now)
    # Spend a long time on an exempt style.
    now += 100000.0
    ctrl.update(now, "game", enabled=True)
    # Switch back: the very next update must not already be in a break.
    assert not ctrl.update(now + 1.0, "news", enabled=True)

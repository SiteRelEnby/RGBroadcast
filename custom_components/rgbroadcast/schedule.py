"""Time-of-day style scheduling and ad breaks.

Both are pure state machines here, driven by an injected clock, so the awkward
timing logic is testable without an event loop. The async engine consumes them.

A constant style all evening is the fixed-schedule mistake in another form: a
house that watches the same genre from 19:00 to midnight every night is nearly
as scripted-looking as random flicker. The scheduler rotates styles across the
evening with jittered changeover times and per-night random choices, so no two
nights match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
import random

from .const import (
    AD_BREAK_MAX_S,
    AD_BREAK_MIN_S,
    AD_CONTENT_MAX_S,
    AD_CONTENT_MIN_S,
    AD_EXEMPT_STYLES,
)

# --- Style scheduler ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    """One evening slot: from ``start``, show one of ``styles`` (chosen nightly)."""

    start: time
    styles: tuple[str, ...]
    #: Plus or minus this many minutes of randomisation on the changeover.
    jitter_min: int = 30


#: The default evening: studio news early on, something with more motion mid
#: evening, dim late-night after midnight. Each slot picks one option per night.
DEFAULT_SCHEDULE: tuple[ScheduleSlot, ...] = (
    ScheduleSlot(time(19, 0), ("news",), jitter_min=30),
    ScheduleSlot(time(21, 0), ("film", "sport", "game"), jitter_min=40),
    ScheduleSlot(time(0, 30), ("latenight",), jitter_min=30),
)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def materialise_schedule(
    slots: tuple[ScheduleSlot, ...], rng: random.Random
) -> tuple[tuple[int, str], ...]:
    """Fix a schedule for one night: jittered start minute plus a chosen style.

    Returns ``(minute_of_day, style_name)`` pairs sorted by start minute. Done
    once per night so the boundaries do not jitter every time they are read,
    which would make the style flap around the changeover.
    """
    materialised = []
    for slot in slots:
        jitter = rng.randint(-slot.jitter_min, slot.jitter_min)
        minute = (_minutes(slot.start) + jitter) % (24 * 60)
        materialised.append((minute, rng.choice(slot.styles)))
    return tuple(sorted(materialised))


def active_style(now: time, materialised: tuple[tuple[int, str], ...]) -> str | None:
    """Pick the style whose slot is currently active.

    Before the first slot of the day you are still in the previous evening's
    last slot, which is what makes an after-midnight ``latenight`` slot carry on
    into the small hours. Returns ``None`` for an empty schedule.
    """
    if not materialised:
        return None
    current = _minutes(now)
    active = materialised[-1][1]  # wrap: the last slot owns the pre-dawn hours
    for minute, style in materialised:
        if minute <= current:
            active = style
        else:
            break
    return active


# --- Ad breaks ---------------------------------------------------------------


@dataclass(slots=True)
class AdBreakController:
    """Broadcast-style ad breaks: content, then a burst of ads, repeat.

    Roughly every 12-18 minutes of content comes 2-4 minutes of brighter,
    faster, more saturated output. That signature is distinctive and sells the
    illusion of live broadcast television. Streaming and gaming do not have it,
    so exempt styles pause the machine entirely.
    """

    rng: random.Random
    _in_break: bool = field(default=False, init=False)
    _boundary: float = field(default=0.0, init=False)
    _armed: bool = field(default=False, init=False)

    def _next_content(self) -> float:
        return self.rng.uniform(AD_CONTENT_MIN_S, AD_CONTENT_MAX_S)

    def _next_break(self) -> float:
        return self.rng.uniform(AD_BREAK_MIN_S, AD_BREAK_MAX_S)

    def reset(self, now: float) -> None:
        """Start a fresh content stretch. Called when the simulation starts."""
        self._in_break = False
        self._armed = True
        self._boundary = now + self._next_content()

    def update(self, now: float, style: str, enabled: bool) -> bool:
        """Advance the machine and report whether an ad break is running now.

        Disabling ad breaks, or an exempt style, ends any break in progress and
        disarms the timer so it does not fire the instant it is re-enabled with
        a stale boundary.
        """
        if not enabled or style in AD_EXEMPT_STYLES:
            self._in_break = False
            self._armed = False
            return False

        if not self._armed:
            self.reset(now)
            return False

        # A single tick can be long, but breaks and content are minutes, so at
        # most one boundary is crossed per tick in practice; loop to be safe.
        while now >= self._boundary:
            if self._in_break:
                self._in_break = False
                self._boundary += self._next_content()
            else:
                self._in_break = True
                self._boundary += self._next_break()

        return self._in_break

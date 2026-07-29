"""The simulation engine: one shared clock driving one or more lights.

The engine owns the parts that must be shared across a config entry's lights so
they look like one television lighting one room rather than several televisions:
a single tick, a single cut decision, and the ad-break and schedule state. Each
light then steps its own walk off that shared decision, in its own role.

The visual maths lives in :mod:`walk`, :mod:`renderer` and :mod:`schedule`, all
pure. This module is the thin async shell that reads device state once, then
issues service calls on a jittered clock until told to stop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
from dataclasses import dataclass, replace
import logging
import random

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import homeassistant.util.dt as dt_util

from .const import (
    AD_STYLE,
    DEFAULT_AD_BREAKS,
    DEFAULT_BRIGHTNESS_CEILING,
    DEFAULT_INTENSITY,
    DEFAULT_ON_STOP,
    DEFAULT_STYLE,
    DEFAULT_TICK_MAX,
    DEFAULT_TICK_MIN,
    DOMAIN,
    FADE_TICK_FRACTION,
    FADE_TICK_MAX,
    FADE_TICK_MIN,
    ISSUE_ONOFF_LIGHT,
    ON_STOP_RESTORE,
    RAMP_DOWN_SECONDS,
    RAMP_DOWN_STEPS,
    ROLE_SCREEN,
    STYLE_SCHEDULE,
    TIER_AUTO,
)
from .renderer import (
    Capabilities,
    build_payload,
    detect_capabilities,
    limits_from,
)
from .schedule import (
    DEFAULT_SCHEDULE,
    AdBreakController,
    ScheduleSlot,
    active_style,
    materialise_schedule,
)
from .styles import Style, get_style
from .walk import (
    Limits,
    RoleProfile,
    WalkState,
    draw_tick,
    initial_state,
    profile_for,
    should_cut,
    step,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Live-tunable configuration. Mutated in place so options apply mid-run.

    Editing a config entry's options normally reloads the entry, which would
    kill the running loop. The update listener instead copies new values onto
    the existing instance, so the loop reads them on its next tick without a
    restart.
    """

    style: str = DEFAULT_STYLE
    intensity: float = DEFAULT_INTENSITY
    ad_breaks: bool = DEFAULT_AD_BREAKS
    tick_min: float | None = None  # None: pick from renderer capability
    tick_max: float | None = None
    brightness_ceiling: int = DEFAULT_BRIGHTNESS_CEILING
    force_steps: bool = False
    force_tier: str = TIER_AUTO
    disable_cct: bool = False
    on_stop: str = DEFAULT_ON_STOP
    schedule: tuple[ScheduleSlot, ...] = DEFAULT_SCHEDULE


@dataclass
class _Light:
    """Per-light runtime state within an entry.

    ``caps``, ``limits`` and ``profile`` are fixed when the light is seeded, so
    the per-tick loop reads them rather than recomputing them each time.
    """

    entity_id: str
    role: str
    caps: Capabilities
    limits: Limits
    profile: RoleProfile
    walk: WalkState
    last_payload: dict | None = None
    #: Captured at start for the restore_previous stop behaviour.
    restore_state: dict | None = None


class RGBroadcastEngine:
    """Drives a set of lights to look like a television is on."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_ids: list[str],
        roles: dict[str, str],
        config: EngineConfig,
        *,
        rng: random.Random | None = None,
        device_name: str = "RGBroadcast",
    ) -> None:
        self.hass = hass
        self._entity_ids = entity_ids
        self._roles = roles
        self.config = config
        self._device_name = device_name
        self._rng = rng or random.Random()
        self._ads = AdBreakController(self._rng)
        self._task: asyncio.Task | None = None
        self._lights: list[_Light] = []
        self._ad_active = False
        self._schedule_day: int | None = None
        self._materialised: tuple[tuple[int, str], ...] = ()
        #: Set once per run: whether every light can fade, which widens the tick
        #: band. Invariant until a re-seed, so it is not recomputed per tick.
        self._all_fade = False
        #: Last hue the screen light rendered, so spill lights can trail it
        #: without relying on the per-tick iteration order.
        self._screen_hue: float | None = None
        #: Notified when the ad-break flag changes, so the diagnostic sensor and
        #: any listeners can update without polling.
        self._listeners: list[Callable[[], None]] = []

    # --- public API ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def ad_active(self) -> bool:
        return self._ad_active

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired when the ad-break flag changes."""
        self._listeners.append(cb)

        def _remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return _remove

    async def async_start(self) -> None:
        """Seed from current device state and start the loop."""
        if self.running:
            return
        self._lights = [light for e in self._entity_ids if (light := self._seed(e))]
        if not self._lights:
            _LOGGER.warning(
                "rgbroadcast: no simulatable lights among %s; not starting",
                self._entity_ids,
            )
            return
        self._all_fade = all(light.caps.can_fade for light in self._lights)
        self._screen_hue = None
        self._ads.reset(self._now_mono())
        self._task = self.hass.async_create_task(self._run())

    async def async_stop(self) -> None:
        """Stop the loop and settle the lights (ramp down or restore)."""
        await self._cancel_task()
        await self._settle()
        self._set_ad_active(False)

    async def async_restart(self) -> None:
        """Re-seed and restart without settling the lights.

        Used when a capability override (force stepped, force tier) changes
        mid-run: those are read when a light is seeded, so the loop must re-seed
        to honour them. Skipping the ramp-down keeps the change seamless rather
        than blinking the room off and on.
        """
        if not self.running:
            return
        await self._cancel_task()
        await self.async_start()

    #: Structural fields the engine owns. Changing one of these is what an
    #: options edit does; the runtime dials (style/intensity/ad_breaks) are
    #: owned by their entities and are not touched here.
    _STRUCTURAL_FIELDS = (
        "tick_min",
        "tick_max",
        "brightness_ceiling",
        "force_steps",
        "force_tier",
        "disable_cct",
        "on_stop",
    )

    async def apply_structural(self, new: EngineConfig) -> None:
        """Apply changed structural options to the live engine.

        The engine, not the caller, decides how each change takes effect, since
        that depends on how the field is consumed: capability overrides are read
        when a light is seeded (so they need a re-seed), the colour/brightness
        limits are cached per light (so they are rebuilt in place), and the rest
        are read fresh each tick (so a plain copy suffices). Runtime dials are
        left untouched.
        """
        old = self.config
        reseed = new.force_steps != old.force_steps or new.force_tier != old.force_tier
        rebuild_limits = (
            new.disable_cct != old.disable_cct
            or new.brightness_ceiling != old.brightness_ceiling
        )
        for field in self._STRUCTURAL_FIELDS:
            setattr(old, field, getattr(new, field))

        if reseed:
            await self.async_restart()
        elif rebuild_limits:
            self._rebuild_limits()

    def _rebuild_limits(self) -> None:
        """Recompute each light's cached limits after a colour/ceiling change."""
        for light in self._lights:
            light.limits = replace(
                light.limits,
                disable_cct=self.config.disable_cct,
                brightness_ceiling=self.config.brightness_ceiling,
            )

    async def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- seeding ------------------------------------------------------------

    def _seed(self, entity_id: str) -> _Light | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("rgbroadcast: %s not found", entity_id)
            return None
        caps = detect_capabilities(
            state,
            force_steps=self.config.force_steps,
            force_tier=self.config.force_tier,
        )
        if not caps.is_simulatable:
            _LOGGER.warning(
                "rgbroadcast: %s is on/off only and cannot be simulated", entity_id
            )
            self._raise_onoff_issue(entity_id)
            return None
        self._clear_onoff_issue(entity_id)
        style = get_style(self._current_style())
        role = self._roles.get(entity_id, ROLE_SCREEN)
        limits = limits_from(
            caps,
            disable_cct=self.config.disable_cct,
            brightness_ceiling=self.config.brightness_ceiling,
        )
        return _Light(
            entity_id=entity_id,
            role=role,
            caps=caps,
            limits=limits,
            profile=profile_for(role),
            walk=initial_state(style, limits, self._rng),
            restore_state=self._capture(state),
        )

    @staticmethod
    def _capture(state) -> dict:
        """Snapshot enough of a light's state to restore it later."""
        keep = ("brightness", "hs_color", "color_temp_kelvin", "rgb_color")
        attrs = {k: state.attributes[k] for k in keep if k in state.attributes}
        return {"state": state.state, "attrs": attrs}

    # --- the loop -----------------------------------------------------------

    async def _run(self) -> None:
        try:
            while True:
                # Resolve the tick's inputs once. Advancing ads first means the
                # cut decision and the render agree on the effective style.
                self._advance_ads()
                style = get_style(self._effective_style())
                tick = self._draw_tick()
                is_cut = should_cut(tick, style, self.config.intensity, self._rng)
                await self._render_all(style, tick, is_cut)
                await asyncio.sleep(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("rgbroadcast: engine loop crashed")

    def _draw_tick(self) -> float:
        """Pick this tick's length, honouring overrides then capability.

        Fade-capable lights interpolate between commands, so when every light
        can fade the loop dwells far longer for the same apparent motion.
        ``_all_fade`` is fixed at seed time, not recomputed here.
        """
        lo = self.config.tick_min
        hi = self.config.tick_max
        if lo is None:
            lo = FADE_TICK_MIN if self._all_fade else DEFAULT_TICK_MIN
        if hi is None:
            hi = FADE_TICK_MAX if self._all_fade else DEFAULT_TICK_MAX
        return draw_tick(lo, hi, self._rng)

    async def _render_all(self, style: Style, tick: float, is_cut: bool) -> None:
        for light in self._lights:
            light.walk = step(
                light.walk,
                style,
                is_cut=is_cut,
                intensity=self.config.intensity,
                limits=light.limits,
                rng=self._rng,
                profile=light.profile,
                tick=tick,
                reference_hue=self._screen_hue,
            )
            if light.role == ROLE_SCREEN:
                self._screen_hue = light.walk.hue

            transition = 0.0 if is_cut else tick * FADE_TICK_FRACTION
            payload = build_payload(light.walk, light.caps, transition=transition)
            await self._call_light(light, payload)

    def _light_call(self, entity_id: str, service: str, **data: object) -> Coroutine:
        """Issue a non-blocking light service call for one entity."""
        return self.hass.services.async_call(
            LIGHT_DOMAIN,
            service,
            {ATTR_ENTITY_ID: entity_id, **data},
            blocking=False,
        )

    async def _call_light(self, light: _Light, payload: dict) -> None:
        # Skip identical back-to-back calls: no visual change, just recorder
        # noise and device chatter.
        comparable = {k: v for k, v in payload.items() if k != "transition"}
        if comparable == light.last_payload:
            return
        light.last_payload = comparable
        await self._light_call(light.entity_id, SERVICE_TURN_ON, **payload)

    # --- ad breaks and scheduling ------------------------------------------

    def _advance_ads(self) -> None:
        active = self._ads.update(
            self._now_mono(), self._current_style(), self.config.ad_breaks
        )
        self._set_ad_active(active)

    def _set_ad_active(self, active: bool) -> None:
        if active != self._ad_active:
            self._ad_active = active
            for cb in list(self._listeners):
                cb()

    def _current_style(self) -> str:
        """The style the user or schedule has selected, before ad override."""
        if self.config.style == STYLE_SCHEDULE:
            return self._scheduled_style()
        return self.config.style

    def _effective_style(self) -> str:
        """The style actually rendered this tick, ads included."""
        if self._ad_active:
            return AD_STYLE
        return self._current_style()

    def _scheduled_style(self) -> str:
        now = dt_util.now()
        # Re-roll the night's jittered boundaries once per calendar day.
        if now.toordinal() != self._schedule_day:
            self._schedule_day = now.toordinal()
            self._materialised = materialise_schedule(self.config.schedule, self._rng)
        return active_style(now.time(), self._materialised) or DEFAULT_STYLE

    # --- stopping -----------------------------------------------------------

    async def _settle(self) -> None:
        """Bring the lights to rest gracefully rather than snapping to black.

        Lights settle concurrently: a stepped ramp-down takes a few seconds, and
        there is no reason to make several lights queue behind each other.
        """
        await asyncio.gather(*(self._settle_one(light) for light in self._lights))

    async def _settle_one(self, light: _Light) -> None:
        if self.config.on_stop == ON_STOP_RESTORE and light.restore_state:
            await self._restore(light)
        else:
            await self._ramp_down(light)

    async def _ramp_down(self, light: _Light) -> None:
        """Fade to off. A room going dark reads better than a hard cut."""
        if light.caps.can_fade:
            await self._light_call(
                light.entity_id, SERVICE_TURN_OFF, transition=RAMP_DOWN_SECONDS
            )
            return
        # Stepped light: descend by hand, since transition would be ignored.
        start = light.walk.brightness
        for i in range(RAMP_DOWN_STEPS, 0, -1):
            pct = max(1, round(start * i / RAMP_DOWN_STEPS))
            await self._light_call(light.entity_id, SERVICE_TURN_ON, brightness_pct=pct)
            await asyncio.sleep(RAMP_DOWN_SECONDS / RAMP_DOWN_STEPS)
        await self._light_call(light.entity_id, SERVICE_TURN_OFF)

    async def _restore(self, light: _Light) -> None:
        """Put the light back to whatever it was before the simulation."""
        snap = light.restore_state
        if not snap or snap["state"] != "on":
            await self._light_call(light.entity_id, SERVICE_TURN_OFF)
            return
        await self._light_call(light.entity_id, SERVICE_TURN_ON, **snap["attrs"])

    # --- repair issues ------------------------------------------------------

    def _raise_onoff_issue(self, entity_id: str) -> None:
        """Surface a light that has degraded to on/off since it was configured.

        The config flow blocks on/off lights up front, so this only fires when a
        working light later stops reporting anything to vary. Otherwise the
        simulation silently does nothing for that light, with no feedback path.
        """
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_ONOFF_LIGHT}_{entity_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ONOFF_LIGHT,
            translation_placeholders={"entity": entity_id, "name": self._device_name},
        )

    def _clear_onoff_issue(self, entity_id: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_ONOFF_LIGHT}_{entity_id}")

    # --- clock --------------------------------------------------------------

    def _now_mono(self) -> float:
        """Monotonic seconds for interval timing (ad breaks)."""
        return self.hass.loop.time()

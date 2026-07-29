"""Constants for the RGBroadcast integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "rgbroadcast"

# --- Config entry keys -------------------------------------------------------
# CONF_NAME is homeassistant.const.CONF_NAME; do not redeclare it here.

CONF_LIGHTS: Final = "lights"
CONF_ROLES: Final = "roles"
CONF_SCREEN_LIGHT: Final = "screen_light"
CONF_SPILL_LIGHTS: Final = "spill_lights"

# Structural options (set in the config/options flow, reloaded to change).
CONF_TICK_MIN: Final = "tick_min"
CONF_TICK_MAX: Final = "tick_max"
CONF_BRIGHTNESS_CEILING: Final = "brightness_ceiling"
CONF_FORCE_STEPS: Final = "force_steps"
CONF_FORCE_TIER: Final = "force_tier"
CONF_DISABLE_CCT: Final = "disable_cct"
CONF_ON_STOP: Final = "on_stop"

# --- Roles -------------------------------------------------------------------
# A role is the coordination policy for one light within a room. The concrete
# behaviour lives in the RoleProfile it maps to (see walk.ROLE_PROFILES); the
# walk itself is role-agnostic and just applies whatever profile it is given.

ROLE_SCREEN: Final = "screen"
ROLE_SPILL: Final = "spill"
ROLES: Final = (ROLE_SCREEN, ROLE_SPILL)

# --- Capability tiers (design doc section 3.5) -------------------------------

TIER_HYBRID: Final = "hybrid"
TIER_COLOUR: Final = "colour"
TIER_CCT: Final = "cct"
TIER_BRIGHTNESS: Final = "brightness"
TIER_ONOFF: Final = "onoff"
TIERS: Final = (TIER_HYBRID, TIER_COLOUR, TIER_CCT, TIER_BRIGHTNESS, TIER_ONOFF)

TIER_AUTO: Final = "auto"

# --- On-stop behaviour -------------------------------------------------------

ON_STOP_TURN_OFF: Final = "turn_off"
ON_STOP_RESTORE: Final = "restore_previous"
ON_STOP_OPTIONS: Final = (ON_STOP_TURN_OFF, ON_STOP_RESTORE)

# --- Defaults ----------------------------------------------------------------

DEFAULT_STYLE: Final = "news"
DEFAULT_INTENSITY: Final = 1.0
DEFAULT_AD_BREAKS: Final = True
DEFAULT_BRIGHTNESS_CEILING: Final = 100
DEFAULT_ON_STOP: Final = ON_STOP_TURN_OFF

MIN_INTENSITY: Final = 0.5
MAX_INTENSITY: Final = 2.0
INTENSITY_STEP: Final = 0.1

# Stepped rendering needs a fast tick to look continuous. Fade-capable lights
# interpolate between commands, so they can dwell far longer for the same
# apparent motion; async_setup picks the wider band when every light can fade.
DEFAULT_TICK_MIN: Final = 1.5
DEFAULT_TICK_MAX: Final = 3.5
FADE_TICK_MIN: Final = 3.0
FADE_TICK_MAX: Final = 7.0

# Transition is set to a fraction of the tick so each fade lands before the next
# command is issued. Queued transitions on a device that honours them fight each
# other (design doc section 6).
FADE_TICK_FRACTION: Final = 0.9

# --- Ad breaks (design doc section 8) ----------------------------------------

AD_CONTENT_MIN_S: Final = 12 * 60
AD_CONTENT_MAX_S: Final = 18 * 60
AD_BREAK_MIN_S: Final = 2 * 60
AD_BREAK_MAX_S: Final = 4 * 60
AD_STYLE: Final = "ads"
# Streaming and gaming do not have ad breaks.
AD_EXEMPT_STYLES: Final = ("game",)

# --- Ramp-down (design doc section 9) ----------------------------------------

RAMP_DOWN_STEPS: Final = 8
RAMP_DOWN_SECONDS: Final = 4.0

# --- Misc --------------------------------------------------------------------

STYLE_SCHEDULE: Final = "schedule"

ISSUE_ONOFF_LIGHT: Final = "onoff_light"

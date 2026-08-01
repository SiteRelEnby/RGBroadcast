"""Config and options flow for RGBroadcast."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from . import RGBroadcastConfigEntry
from .const import (
    CONF_BRIGHTNESS_CEILING,
    CONF_DISABLE_CCT,
    CONF_FORCE_STEPS,
    CONF_FORCE_TIER,
    CONF_LIGHTS,
    CONF_ON_STOP,
    CONF_ROLES,
    CONF_SCREEN_LIGHT,
    CONF_SPILL_LIGHTS,
    CONF_TICK_MAX,
    CONF_TICK_MIN,
    DEFAULT_BRIGHTNESS_CEILING,
    DEFAULT_ON_STOP,
    DOMAIN,
    ON_STOP_OPTIONS,
    ROLE_SCREEN,
    ROLE_SPILL,
    TIER_AUTO,
    TIERS,
)
from .renderer import detect_capabilities

_LIGHT_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="light"))
_LIGHTS_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="light", multiple=True)
)


def _lights_schema(
    name: str = "Living Room",
    screen: str | None = None,
    spills: list[str] | None = None,
) -> vol.Schema:
    """The light-selection form, optionally pre-filled for reconfiguration."""
    screen_field = (
        vol.Required(CONF_SCREEN_LIGHT, default=screen)
        if screen
        else vol.Required(CONF_SCREEN_LIGHT)
    )
    spill_field = (
        vol.Optional(CONF_SPILL_LIGHTS, default=spills)
        if spills
        else vol.Optional(CONF_SPILL_LIGHTS)
    )
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=name): str,
            screen_field: _LIGHT_SELECTOR,
            spill_field: _LIGHTS_SELECTOR,
        }
    )


class RGBroadcastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and reconfiguration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the name and the lights to drive."""
        errors: dict[str, str] = {}
        if user_input is not None:
            lights, roles, errors = self._resolve_lights(user_input)
            if not errors:
                await self.async_set_unique_id(
                    "_".join(sorted(lights)), raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_LIGHTS: lights, CONF_ROLES: roles},
                )

        return self.async_show_form(
            step_id="user", data_schema=_lights_schema(), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the name and lights of an existing entry, in place.

        Reloads the entry so the engine re-seeds with the new light set (adding
        or removing spill accents, for example) without needing to delete and
        recreate it.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            lights, roles, errors = self._resolve_lights(user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    title=user_input[CONF_NAME],
                    data={CONF_LIGHTS: lights, CONF_ROLES: roles},
                )

        current_roles: dict[str, str] = entry.data.get(CONF_ROLES, {})
        screen = next((e for e, r in current_roles.items() if r == ROLE_SCREEN), None)
        spills = [e for e, r in current_roles.items() if r == ROLE_SPILL]
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_lights_schema(entry.title, screen, spills),
            errors=errors,
        )

    def _resolve_lights(
        self, user_input: dict[str, Any]
    ) -> tuple[list[str], dict[str, str], dict[str, str]]:
        """Turn a submitted form into (lights, roles, errors)."""
        screen = user_input[CONF_SCREEN_LIGHT]
        spills = [e for e in user_input.get(CONF_SPILL_LIGHTS, []) if e != screen]
        if error := self._validate_light(screen):
            return [], {}, {CONF_SCREEN_LIGHT: error}
        lights = [screen, *spills]
        roles = {screen: ROLE_SCREEN, **dict.fromkeys(spills, ROLE_SPILL)}
        return lights, roles, {}

    def _validate_light(self, entity_id: str) -> str | None:
        """Reject a light that cannot be simulated, with a clear reason.

        A user pointing this at a smart plug and seeing nothing happen has no
        feedback path otherwise.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            return "light_not_found"
        if not detect_capabilities(state).is_simulatable:
            return "light_onoff_only"
        return None

    @staticmethod
    @callback
    def async_get_options_flow(entry: RGBroadcastConfigEntry) -> OptionsFlow:
        return RGBroadcastOptionsFlow()


class RGBroadcastOptionsFlow(OptionsFlow):
    """Structural settings that legitimately warrant an entry reload.

    Runtime dials (style, intensity, ad breaks) are entities, not options, so
    they can be changed from a dashboard without a reload.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Drop unset tick bounds so the engine falls back to capability
            # defaults rather than pinning them to zero.
            cleaned = {k: v for k, v in user_input.items() if v is not None and v != ""}
            return self.async_create_entry(data=cleaned)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BRIGHTNESS_CEILING,
                    default=options.get(
                        CONF_BRIGHTNESS_CEILING, DEFAULT_BRIGHTNESS_CEILING
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=100,
                        step=5,
                        unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    CONF_ON_STOP,
                    default=options.get(CONF_ON_STOP, DEFAULT_ON_STOP),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(ON_STOP_OPTIONS),
                        translation_key="on_stop",
                    )
                ),
                vol.Optional(
                    CONF_FORCE_STEPS,
                    default=options.get(CONF_FORCE_STEPS, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_DISABLE_CCT,
                    default=options.get(CONF_DISABLE_CCT, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_FORCE_TIER,
                    default=options.get(CONF_FORCE_TIER, TIER_AUTO),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TIER_AUTO, *TIERS],
                        translation_key="force_tier",
                    )
                ),
                vol.Optional(
                    CONF_TICK_MIN,
                    description={"suggested_value": options.get(CONF_TICK_MIN)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=10,
                        step=0.1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TICK_MAX,
                    description={"suggested_value": options.get(CONF_TICK_MAX)},
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=15,
                        step=0.1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

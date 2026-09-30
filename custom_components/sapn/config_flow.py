"""Config flow for SA Power Networks meter data."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    DateSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CYCLE_DAYS,
    CONF_CYCLE_START,
    CONF_DAYS_BACK,
    CONF_NMI,
    CONF_RUN_TIMES,
    DEFAULT_CYCLE_DAYS,
    DEFAULT_DAYS_BACK,
    DEFAULT_RUN_TIMES,
    DOMAIN,
)
from .coordinator import parse_run_times
from .portal import SapnAuthError, SapnError, login_and_list_nmis

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)


class SapnConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in to SAPN's Your Meter Data and pick an NMI."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._nmis: list[str] = []

    async def _async_login(self, email: str, password: str) -> tuple[list[str], dict[str, str]]:
        errors: dict[str, str] = {}
        nmis: list[str] = []
        try:
            nmis = await self.hass.async_add_executor_job(login_and_list_nmis, email, password)
        except SapnAuthError:
            errors["base"] = "invalid_auth"
        except SapnError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error signing in to SAPN")
            errors["base"] = "unknown"
        else:
            if not nmis:
                errors["base"] = "no_nmis"
        return nmis, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            nmis, errors = await self._async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            if not errors:
                self._credentials = {
                    CONF_EMAIL: user_input[CONF_EMAIL],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                self._nmis = nmis
                if len(nmis) == 1:
                    return await self._async_create(nmis[0])
                return await self.async_step_nmi()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)}
            ),
            errors=errors,
        )

    async def async_step_nmi(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_create(user_input[CONF_NMI])
        return self.async_show_form(
            step_id="nmi",
            data_schema=vol.Schema(
                {vol.Required(CONF_NMI): SelectSelector(SelectSelectorConfig(options=self._nmis))}
            ),
        )

    async def _async_create(self, nmi: str) -> ConfigFlowResult:
        await self.async_set_unique_id(nmi[:10])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"SAPN {nmi}", data={**self._credentials, CONF_NMI: nmi}
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            _nmis, errors = await self._async_login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SapnOptionsFlow:
        return SapnOptionsFlow()


class SapnOptionsFlow(OptionsFlow):
    """Re-import window, schedule and billing cycle."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_run_times(user_input[CONF_RUN_TIMES])
            except ValueError:
                errors[CONF_RUN_TIMES] = "invalid_run_times"
            else:
                user_input[CONF_DAYS_BACK] = int(user_input[CONF_DAYS_BACK])
                user_input[CONF_CYCLE_DAYS] = int(user_input[CONF_CYCLE_DAYS])
                return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DAYS_BACK, default=options.get(CONF_DAYS_BACK, DEFAULT_DAYS_BACK)
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_RUN_TIMES, default=options.get(CONF_RUN_TIMES, DEFAULT_RUN_TIMES)
                ): TextSelector(),
                vol.Optional(
                    CONF_CYCLE_START,
                    description={"suggested_value": options.get(CONF_CYCLE_START)},
                ): DateSelector(),
                vol.Required(
                    CONF_CYCLE_DAYS, default=options.get(CONF_CYCLE_DAYS, DEFAULT_CYCLE_DAYS)
                ): NumberSelector(
                    NumberSelectorConfig(min=7, max=93, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

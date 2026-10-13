"""Config flow: the server, then sign in once (or paste a token), then the family."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import NestlingAuthError, NestlingClient, NestlingConnectionError, NestlingError, base_url
from .const import CONF_FAMILY_ID, DEFAULT_APP_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Roles that can log: a connected app's own, or a person's (a personal API token works too).
LOGGING_ROLES = ("integration", "owner", "caregiver")


class NestlingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one Nestling family."""

    VERSION = 1

    def __init__(self) -> None:
        self._url: str = ""
        self._session_token: str | None = None
        self._token: str | None = None
        self._families: list[dict[str, Any]] = []

    def _client(self) -> NestlingClient:
        return NestlingClient(async_get_clientsession(self.hass), self._url)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._url = base_url(user_input[CONF_URL])
            try:
                await self._client().setup_status()
            except NestlingError as e:
                _LOGGER.debug("No Nestling server at %s: %s", self._url, e)
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_method()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL))}),
                user_input or {CONF_URL: "https://nestling.example.com"},
            ),
            errors=errors,
        )

    async def async_step_method(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="method", menu_options=["sign_in", "token"])

    async def async_step_sign_in(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """An owner's email and password, used once to add Home Assistant to the family."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = self._client()
            try:
                token = await client.login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
                me = await client.me(token)
            except NestlingAuthError:
                errors["base"] = "invalid_auth"
            except NestlingConnectionError:
                errors["base"] = "cannot_connect"
            except NestlingError:
                errors["base"] = "unknown"
            else:
                self._session_token = token
                self._families = [f for f in me.get("families", []) if f.get("role") == "owner"]
                if not self._families:
                    await self._sign_out()
                    errors["base"] = "not_owner"
                else:
                    return await self._pick_family()
        return self.async_show_form(
            step_id="sign_in",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"url": self._url},
        )

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A token from the app (Family → Connected apps)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                me = await self._client().me(token)
            except NestlingAuthError:
                errors["base"] = "invalid_auth"
            except NestlingConnectionError:
                errors["base"] = "cannot_connect"
            except NestlingError:
                errors["base"] = "unknown"
            else:
                self._token = token
                self._families = [f for f in me.get("families", []) if f.get("role") in LOGGING_ROLES]
                if not self._families:
                    errors["base"] = "no_family"
                else:
                    return await self._pick_family()
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}),
            errors=errors,
            description_placeholders={"url": self._url},
        )

    async def _pick_family(self) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            family_id = self._get_reauth_entry().data[CONF_FAMILY_ID]
            if any(f["id"] == family_id for f in self._families):
                return await self._finish(family_id)
            await self._sign_out()
            return self.async_abort(reason="wrong_family")
        if len(self._families) == 1:
            return await self._finish(self._families[0]["id"])
        return await self.async_step_family()

    async def async_step_family(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._finish(user_input[CONF_FAMILY_ID])
        options = [{"value": f["id"], "label": f["name"]} for f in self._families]
        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema({vol.Required(CONF_FAMILY_ID): SelectSelector(SelectSelectorConfig(options=options))}),
        )

    async def _finish(self, family_id: str) -> ConfigFlowResult:
        if self.source != SOURCE_REAUTH:
            await self.async_set_unique_id(family_id)
            if any(e.unique_id == family_id for e in self._async_current_entries(include_ignore=False)):
                await self._sign_out()
                return self.async_abort(reason="already_configured")
        token = self._token
        if self._session_token:
            try:
                app = await self._client().create_integration(family_id, DEFAULT_APP_NAME, self._session_token)
            except NestlingError as e:
                _LOGGER.warning("Could not add %s to the family: %s", DEFAULT_APP_NAME, e)
                await self._sign_out()
                return self.async_abort(reason="cannot_add_app")
            token = app["token"]
            await self._sign_out()
        data = {CONF_URL: self._url, CONF_TOKEN: token, CONF_FAMILY_ID: family_id}
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        name = next((f["name"] for f in self._families if f["id"] == family_id), "Nestling")
        return self.async_create_entry(title=name, data=data)

    async def _sign_out(self) -> None:
        """Drop the short session made for setup; only the app's own token is kept."""
        if self._session_token:
            with contextlib.suppress(NestlingError):
                await self._client().logout(self._session_token)
            self._session_token = None

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._url = entry_data[CONF_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="reauth_confirm", menu_options=["sign_in", "token"])

"""The config flow, against a real Nestling server."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nestling.const import CONF_FAMILY_ID, DOMAIN

from .conftest import PASSWORD, call, connected_app


async def _to_menu(hass: HomeAssistant, url: str) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: f"{url}/api/v1/"})
    assert result["type"] is FlowResultType.MENU
    return result["flow_id"]


async def test_sign_in_adds_home_assistant_to_the_family(hass: HomeAssistant, nestling: dict[str, Any]) -> None:
    connected_app(nestling, "Probe")  # skips on servers without connected apps
    flow_id = await _to_menu(hass, nestling["url"])
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "sign_in"})
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: nestling["email"], CONF_PASSWORD: "wrong password"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: nestling["email"], CONF_PASSWORD: PASSWORD})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == "Home"
    data = result["data"]
    assert data[CONF_URL] == nestling["url"]
    assert data[CONF_FAMILY_ID] == nestling["family_id"]

    # The stored token is the connected app's own, and the setup session is gone.
    me = call(nestling["url"], "GET", "/me", data[CONF_TOKEN])
    assert me["families"][0]["role"] == "integration"
    apps = call(nestling["url"], "GET", f"/families/{nestling['family_id']}/integrations", nestling["owner"])["integrations"]
    assert [a["name"] for a in apps] == ["Probe", "Home Assistant"]
    sessions = [t for t in call(nestling["url"], "GET", "/me/tokens", nestling["owner"])["tokens"] if t["kind"] == "session"]
    assert len(sessions) == 1  # only the test's own

    # The same family can't be added twice.
    flow_id = await _to_menu(hass, nestling["url"])
    await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "sign_in"})
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_EMAIL: nestling["email"], CONF_PASSWORD: PASSWORD})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_pasted_token(hass: HomeAssistant, nestling: dict[str, Any]) -> None:
    app = connected_app(nestling)
    flow_id = await _to_menu(hass, nestling["url"])
    await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "token"})
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_TOKEN: "not-a-token"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_TOKEN: f" {app['token']} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["data"][CONF_TOKEN] == app["token"]


async def test_wrong_url(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "http://127.0.0.1:9"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

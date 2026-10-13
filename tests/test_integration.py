"""Entities, actions and the live stream, against a real Nestling server."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nestling.const import CONF_FAMILY_ID, DOMAIN, EVENT_ENTRY_LOGGED

from .conftest import call, connected_app


@pytest.fixture
async def entry(hass: HomeAssistant, nestling: dict[str, Any]) -> MockConfigEntry:
    app = connected_app(nestling)
    nestling["app"] = app
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=nestling["family_id"],
        data={CONF_URL: nestling["url"], CONF_TOKEN: app["token"], CONF_FAMILY_ID: nestling["family_id"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    yield entry
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _entity(hass: HomeAssistant, platform: str, key: str, child_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{child_id}_{key}")
    assert entity_id, f"{platform} {key}"
    return entity_id


def _events(nestling: dict[str, Any], types: str) -> list[dict[str, Any]]:
    return call(nestling["url"], "GET", f"/children/{nestling['child_id']}/events?type={types}&limit=10", nestling["owner"])["events"]


async def _wait_for(check, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.1)):
        if check():
            return
        await asyncio.sleep(0.1)
    assert check()


async def test_device_and_logging_actions(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    child_id = nestling["child_id"]
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, child_id)})
    assert device and device.name == "Robin"

    # A bottle, by device; the entry is the connected app's.
    res = await hass.services.async_call(
        DOMAIN,
        "log_feed",
        {"device_id": device.id, "method": "bottle", "amount": 120, "milk": "formula"},
        blocking=True,
        return_response=True,
    )
    assert res["entry"]["amount_ml"] == 120
    assert res["entry"]["created_by"] == nestling["app"]["id"]
    assert hass.states.get(_entity(hass, "sensor", "feeds_today", child_id)).state == "1"
    assert float(hass.states.get(_entity(hass, "sensor", "bottle_today", child_id)).state) == 120
    last_feed = hass.states.get(_entity(hass, "sensor", "last_feed", child_id))
    assert last_feed.attributes["method"] == "bottle"

    # Only one child: no target needed. A sleep with times in Home Assistant's zone.
    await hass.services.async_call(
        DOMAIN, "log_sleep", {"start": "2026-10-08 13:00:00", "end": "2026-10-08 14:20:00", "location": "crib"}, blocking=True
    )
    sleep = _events(nestling, "sleep")[0]
    assert sleep["duration_seconds"] == 80 * 60 and sleep["location"] == "crib"

    # Diaper rules are checked before anything is sent.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "log_diaper", {"child": "Robin"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "log_diaper", {"child": "Nobody", "wet": True}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_diaper", {"child": "robin", "dirty": True, "color": "yellow"}, blocking=True)
    assert _events(nestling, "diaper")[0]["color"] == "yellow"

    await hass.services.async_call(DOMAIN, "log_health", {"kind": "temperature", "temperature": 38.2}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_activity", {"kind": "bath"}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_note", {"note": "first laugh"}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_pump", {"left_amount": 60, "right_amount": 50, "left_minutes": 10}, blocking=True)
    assert float(hass.states.get(_entity(hass, "sensor", "pumped_today", child_id)).state) == 110
    types = {e["type"] for e in _events(nestling, "health,activity,note,pump")}
    assert types == {"health", "activity", "note", "pump"}


async def test_imperial_units(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.services.async_call(DOMAIN, "log_feed", {"method": "bottle", "amount": 4}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_growth", {"weight": 14.5, "length": 25}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_health", {"kind": "temperature", "temperature": 100.4}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_growth", {"weight": 6400, "weight_unit": "g"}, blocking=True)
    assert _events(nestling, "feed")[0]["amount_ml"] == pytest.approx(118.3)
    growth = _events(nestling, "growth")
    assert growth[0]["weight_g"] == 6400
    assert growth[1]["weight_g"] == pytest.approx(6577.1) and growth[1]["length_cm"] == pytest.approx(63.5)
    assert _events(nestling, "health")[0]["temperature_c"] == pytest.approx(38.0)


async def test_timers(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    child_id = nestling["child_id"]
    switch = _entity(hass, "switch", "breastfeed_timer", child_id)
    side = _entity(hass, "select", "breastfeed_side", child_id)
    nursing = _entity(hass, "binary_sensor", "nursing", child_id)

    # The side picked before starting is the side the timer starts on.
    await hass.services.async_call("select", "select_option", {"entity_id": side, "option": "right"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"
    assert hass.states.get(nursing).state == "on"
    assert hass.states.get(side).state == "right"
    assert hass.states.get(_entity(hass, "sensor", "breastfeed_timer_started", child_id)).state not in ("unknown", "unavailable")

    # Switching side, pausing, starting again (no-op), stopping saves the feed.
    await hass.services.async_call("select", "select_option", {"entity_id": side, "option": "left"}, blocking=True)
    assert hass.states.get(side).state == "left"
    await hass.services.async_call(DOMAIN, "pause_timer", {"kind": "breastfeed"}, blocking=True)
    assert hass.states.get(nursing).state == "off"
    assert hass.states.get(switch).state == "on"
    res = await hass.services.async_call(DOMAIN, "start_timer", {"kind": "breastfeed"}, blocking=True, return_response=True)
    assert res["timer"]["running"] is False
    await hass.services.async_call(DOMAIN, "adjust_timer", {"kind": "breastfeed", "left_minutes": 5, "right_minutes": 7}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "off"
    feed = _events(nestling, "feed")[0]
    assert feed["method"] == "breast" and feed["left_seconds"] == 300 and feed["right_seconds"] == 420

    # Continue it, then stop through the action; then nothing is left to stop.
    await hass.services.async_call(DOMAIN, "continue_last", {"kind": "breastfeed"}, blocking=True)
    assert hass.states.get(switch).state == "on"
    res = await hass.services.async_call(
        DOMAIN, "stop_timer", {"kind": "breastfeed", "note": "sleepy"}, blocking=True, return_response=True
    )
    assert res["entry"]["note"] == "sleepy"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "stop_timer", {"kind": "breastfeed"}, blocking=True)

    # Sleep: the switch and awake-since.
    sleep_switch = _entity(hass, "switch", "sleep_timer", child_id)
    await hass.services.async_call("switch", "turn_on", {"entity_id": sleep_switch}, blocking=True)
    assert hass.states.get(_entity(hass, "binary_sensor", "sleeping", child_id)).state == "on"
    assert hass.states.get(_entity(hass, "sensor", "awake_since", child_id)).state == "unknown"
    await hass.services.async_call(DOMAIN, "discard_timer", {"kind": "sleep"}, blocking=True)
    assert hass.states.get(sleep_switch).state == "off"


async def test_live_stream_and_diaper_button(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    child_id = nestling["child_id"]
    fired: list[dict[str, Any]] = []
    hass.bus.async_listen(EVENT_ENTRY_LOGGED, lambda e: fired.append(e.data))
    event_entity = _entity(hass, "event", "entry_logged", child_id)
    await asyncio.sleep(0.5)  # let the stream connect

    # Logged on a phone: Home Assistant hears it through the stream.
    call(nestling["url"], "POST", f"/children/{child_id}/events", nestling["owner"], {"type": "diaper", "wet": True})
    await _wait_for(lambda: hass.states.get(_entity(hass, "sensor", "diapers_today", child_id)).state == "1")
    await _wait_for(lambda: fired)
    assert fired[0]["type"] == "diaper" and fired[0]["child"] == "Robin"
    assert hass.states.get(event_entity).attributes["event_type"] == "diaper"

    # A timer started on a phone turns the switch on.
    call(nestling["url"], "POST", f"/children/{child_id}/timers", nestling["owner"], {"kind": "pump"})
    await _wait_for(lambda: hass.states.get(_entity(hass, "switch", "pump_timer", child_id)).state == "on")

    # The diaper button logs one more.
    await hass.services.async_call("button", "press", {"entity_id": _entity(hass, "button", "diaper_both", child_id)}, blocking=True)
    last = _events(nestling, "diaper")[0]
    assert last["wet"] and last["dirty"]
    assert hass.states.get(_entity(hass, "sensor", "wet_diapers_today", child_id)).state == "2"


async def test_revoked_token_asks_to_sign_in_again(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    call(nestling["url"], "DELETE", f"/families/{nestling['family_id']}/integrations/{nestling['app']['id']}", nestling["owner"])
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_action_descriptions_and_diagnostics(hass: HomeAssistant, nestling: dict[str, Any], entry: MockConfigEntry) -> None:
    from homeassistant.helpers.service import async_get_all_descriptions

    from custom_components.nestling.diagnostics import async_get_config_entry_diagnostics

    descriptions = (await async_get_all_descriptions(hass))[DOMAIN]
    assert len(descriptions) == 16
    assert descriptions["log_feed"]["fields"]["method"]["required"] is True
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"][CONF_TOKEN] == "**REDACTED**"
    assert nestling["child_id"] in diag["summaries"]

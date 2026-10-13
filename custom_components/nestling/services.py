"""Actions: one per kind of entry, plus the timer controls."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import NestlingError
from .const import DOMAIN, TIMER_KINDS
from .coordinator import NestlingCoordinator
from .units import (
    LENGTH_UNITS,
    TEMPERATURE_UNITS,
    VOLUME_UNITS,
    WEIGHT_UNITS,
    length_cm,
    temperature_c,
    volume_ml,
    weight_g,
)

ATTR_CHILD = "child"
SIDES = ("left", "right")

_TIME = vol.Any(cv.datetime, cv.string)
_MINUTES = vol.All(vol.Coerce(float), vol.Range(min=0, max=24 * 60))
_AMOUNT = vol.All(vol.Coerce(float), vol.Range(min=0))

# Who the entry is for, and when; every action takes these.
_TARGET = {
    vol.Optional(ATTR_DEVICE_ID): vol.Any(cv.string, [cv.string]),
    vol.Optional(ATTR_CHILD): cv.string,
}
_WHEN = {
    vol.Optional("start"): _TIME,
    vol.Optional("end"): _TIME,
    vol.Optional("note"): cv.string,
}


def _schema(fields: dict, *, when: bool = True) -> vol.Schema:
    return vol.Schema({**_TARGET, **(_WHEN if when else {}), **fields})


FEED_SCHEMA = _schema(
    {
        vol.Required("method"): vol.In(("breast", "bottle", "combo", "solids")),
        vol.Optional("left_minutes"): _MINUTES,
        vol.Optional("right_minutes"): _MINUTES,
        vol.Optional("start_side"): vol.In(SIDES),
        vol.Optional("amount"): _AMOUNT,
        vol.Optional("volume_unit"): vol.In(VOLUME_UNITS),
        vol.Optional("milk"): vol.In(("breast_milk", "formula", "mixed")),
        vol.Optional("formula_name"): cv.string,
        vol.Optional("foods"): cv.string,
    }
)
SLEEP_SCHEMA = vol.Schema(
    {**_TARGET, **_WHEN, vol.Required("start"): _TIME, vol.Required("end"): _TIME, vol.Optional("location"): cv.string}
)
DIAPER_SCHEMA = _schema(
    {
        vol.Optional("wet", default=False): cv.boolean,
        vol.Optional("dirty", default=False): cv.boolean,
        vol.Optional("dry", default=False): cv.boolean,
        vol.Optional("rash"): cv.boolean,
        vol.Optional("blowout"): cv.boolean,
        vol.Optional("color"): vol.In(("yellow", "green", "brown", "black", "red", "gray")),
        vol.Optional("consistency"): vol.In(("runny", "mushy", "mucousy", "pebbles", "solid")),
        vol.Optional("potty"): vol.In(("sat_dry", "success", "accident")),
    }
)
PUMP_SCHEMA = _schema(
    {
        vol.Optional("left_amount"): _AMOUNT,
        vol.Optional("right_amount"): _AMOUNT,
        vol.Optional("volume_unit"): vol.In(VOLUME_UNITS),
        vol.Optional("left_minutes"): _MINUTES,
        vol.Optional("right_minutes"): _MINUTES,
    }
)
GROWTH_SCHEMA = _schema(
    {
        vol.Optional("weight"): _AMOUNT,
        vol.Optional("weight_unit"): vol.In(WEIGHT_UNITS),
        vol.Optional("length"): _AMOUNT,
        vol.Optional("head"): _AMOUNT,
        vol.Optional("length_unit"): vol.In(LENGTH_UNITS),
    }
)
HEALTH_SCHEMA = _schema(
    {
        vol.Required("kind"): vol.In(("medicine", "temperature", "vaccine", "appointment", "symptom")),
        vol.Optional("name"): cv.string,
        vol.Optional("dose"): _AMOUNT,
        vol.Optional("dose_unit"): cv.string,
        vol.Optional("temperature"): vol.Coerce(float),
        vol.Optional("temperature_unit"): vol.In(TEMPERATURE_UNITS),
    }
)
ACTIVITY_SCHEMA = _schema({vol.Required("kind"): cv.string})
NOTE_SCHEMA = vol.Schema({**_TARGET, vol.Optional("start"): _TIME, vol.Required("note"): cv.string})

_KIND = {vol.Required("kind"): vol.In(TIMER_KINDS)}
START_TIMER_SCHEMA = vol.Schema({**_TARGET, **_KIND, vol.Optional("side"): vol.In((*SIDES, "both")), vol.Optional("start"): _TIME})
SWITCH_SIDE_SCHEMA = vol.Schema(
    {**_TARGET, vol.Optional("kind", default="breastfeed"): vol.In(("breastfeed", "pump")), vol.Optional("side"): vol.In((*SIDES, "both"))}
)
KIND_ONLY_SCHEMA = vol.Schema({**_TARGET, **_KIND})
RESUME_TIMER_SCHEMA = vol.Schema({**_TARGET, **_KIND, vol.Optional("side"): vol.In((*SIDES, "both"))})
ADJUST_TIMER_SCHEMA = vol.Schema(
    {
        **_TARGET,
        **_KIND,
        vol.Optional("start"): _TIME,
        vol.Optional("left_minutes"): _MINUTES,
        vol.Optional("right_minutes"): _MINUTES,
        vol.Optional("minutes"): _MINUTES,
    }
)
STOP_TIMER_SCHEMA = vol.Schema(
    {
        **_TARGET,
        **_KIND,
        vol.Optional("end"): _TIME,
        vol.Optional("note"): cv.string,
        vol.Optional("left_amount"): _AMOUNT,
        vol.Optional("right_amount"): _AMOUNT,
        vol.Optional("volume_unit"): vol.In(VOLUME_UNITS),
        vol.Optional("location"): cv.string,
    }
)
CONTINUE_SCHEMA = vol.Schema({**_TARGET, **_KIND, vol.Optional("side"): vol.In((*SIDES, "both"))})


# Finding the child --------------------------------------------------------------------------


def _coordinators(hass: HomeAssistant) -> list[NestlingCoordinator]:
    return [
        e.runtime_data
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and getattr(e, "runtime_data", None)
    ]


def resolve_child(hass: HomeAssistant, data: dict[str, Any]) -> tuple[NestlingCoordinator, str]:
    """The child an action is for: its device, its name, or the only child there is."""
    coordinators = _coordinators(hass)
    children = [(c, child) for c in coordinators if c.data for child in c.data.children]
    if device_ids := data.get(ATTR_DEVICE_ID):
        if isinstance(device_ids, str):
            device_ids = [device_ids]
        if len(device_ids) != 1:
            raise ServiceValidationError("Pick one child", translation_domain=DOMAIN, translation_key="one_child")
        device = dr.async_get(hass).async_get(device_ids[0])
        ids = {i[1] for i in device.identifiers if i[0] == DOMAIN} if device else set()
        found = [(c, child) for c, child in children if child["id"] in ids]
    elif name := data.get(ATTR_CHILD):
        found = [(c, child) for c, child in children if child["name"].strip().casefold() == name.strip().casefold()]
    else:
        found = children
        if len(found) > 1:
            raise ServiceValidationError("Several children: pick one", translation_domain=DOMAIN, translation_key="which_child")
    if not found:
        raise ServiceValidationError("No such child", translation_domain=DOMAIN, translation_key="no_child")
    coordinator, child = found[0]
    return coordinator, child["id"]


# Helpers ------------------------------------------------------------------------------------


def api_time(value: datetime | str | None) -> str | None:
    """`now`, or a full timestamp; a time without a zone is in Home Assistant's time zone."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().lower() == "now":
            return "now"
        parsed = dt_util.parse_datetime(value.strip())
        if parsed is None:
            raise ServiceValidationError(
                f"Not a time: {value}", translation_domain=DOMAIN, translation_key="bad_time", translation_placeholders={"value": value}
            )
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return value.isoformat(timespec="seconds")


def _seconds(minutes: float | None) -> int | None:
    return None if minutes is None else round(minutes * 60)


def _clean(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


def _when(data: dict[str, Any]) -> dict[str, Any]:
    return {"start": api_time(data.get("start")), "end": api_time(data.get("end")), "note": data.get("note")}


async def _call(coordinator: NestlingCoordinator, child_id: str, request) -> Any:
    try:
        result = await request
    except NestlingError as e:
        raise HomeAssistantError(f"Nestling: {e}") from e
    await coordinator.async_refresh_child(child_id)
    return result


async def _log(hass: HomeAssistant, call: ServiceCall, body: dict[str, Any]) -> ServiceResponse:
    coordinator, child_id = resolve_child(hass, call.data)
    # Our own id: a retried request never logs twice.
    body = _clean({"id": str(uuid.uuid4()), **_when(call.data), **body})
    entry = await _call(coordinator, child_id, coordinator.client.create_event(child_id, body))
    return {"entry": entry}


async def _running(coordinator: NestlingCoordinator, child_id: str, kind: str) -> dict[str, Any] | None:
    try:
        timers = await coordinator.client.timers(child_id)
    except NestlingError as e:
        raise HomeAssistantError(f"Nestling: {e}") from e
    return next((t for t in timers if t.get("kind") == kind), None)


async def _need_timer(coordinator: NestlingCoordinator, child_id: str, kind: str) -> dict[str, Any]:
    timer = await _running(coordinator, child_id, kind)
    if timer is None:
        raise ServiceValidationError(
            f"No {kind} timer is running",
            translation_domain=DOMAIN,
            translation_key="no_timer",
            translation_placeholders={"kind": kind},
        )
    return timer


# Logging ------------------------------------------------------------------------------------


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions (once, for every Nestling family)."""

    async def log_feed(call: ServiceCall) -> ServiceResponse:
        d = call.data
        return await _log(
            hass,
            call,
            {
                "type": "feed",
                "method": d["method"],
                "left_seconds": _seconds(d.get("left_minutes")),
                "right_seconds": _seconds(d.get("right_minutes")),
                "start_side": d.get("start_side"),
                "amount_ml": volume_ml(hass, d.get("amount"), d.get("volume_unit")),
                "milk": d.get("milk"),
                "formula_name": d.get("formula_name"),
                "foods": d.get("foods"),
            },
        )

    async def log_sleep(call: ServiceCall) -> ServiceResponse:
        return await _log(hass, call, {"type": "sleep", "location": call.data.get("location")})

    async def log_diaper(call: ServiceCall) -> ServiceResponse:
        d = call.data
        if not (d["wet"] or d["dirty"] or d["dry"]):
            raise ServiceValidationError("A diaper must be wet, dirty or dry", translation_domain=DOMAIN, translation_key="diaper_empty")
        keys = ("wet", "dirty", "dry", "rash", "blowout", "color", "consistency", "potty")
        return await _log(hass, call, {"type": "diaper", **{k: d.get(k) for k in keys}})

    async def log_pump(call: ServiceCall) -> ServiceResponse:
        d = call.data
        unit = d.get("volume_unit")
        return await _log(
            hass,
            call,
            {
                "type": "pump",
                "left_ml": volume_ml(hass, d.get("left_amount"), unit),
                "right_ml": volume_ml(hass, d.get("right_amount"), unit),
                "left_seconds": _seconds(d.get("left_minutes")),
                "right_seconds": _seconds(d.get("right_minutes")),
            },
        )

    async def log_growth(call: ServiceCall) -> ServiceResponse:
        d = call.data
        if not any(d.get(k) is not None for k in ("weight", "length", "head")):
            raise ServiceValidationError("Give a weight, length or head size", translation_domain=DOMAIN, translation_key="growth_empty")
        unit = d.get("length_unit")
        return await _log(
            hass,
            call,
            {
                "type": "growth",
                "weight_g": weight_g(hass, d.get("weight"), d.get("weight_unit")),
                "length_cm": length_cm(hass, d.get("length"), unit),
                "head_cm": length_cm(hass, d.get("head"), unit),
            },
        )

    async def log_health(call: ServiceCall) -> ServiceResponse:
        d = call.data
        return await _log(
            hass,
            call,
            {
                "type": "health",
                "kind": d["kind"],
                "name": d.get("name"),
                "dose": d.get("dose"),
                "dose_unit": d.get("dose_unit"),
                "temperature_c": temperature_c(hass, d.get("temperature"), d.get("temperature_unit")),
            },
        )

    async def log_activity(call: ServiceCall) -> ServiceResponse:
        return await _log(hass, call, {"type": "activity", "kind": call.data["kind"]})

    async def log_note(call: ServiceCall) -> ServiceResponse:
        return await _log(hass, call, {"type": "note"})

    # Timers ---------------------------------------------------------------------------------

    async def start_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        kind = call.data["kind"]
        if timer := await _running(coordinator, child_id, kind):
            # Already running (maybe started on a phone): nothing to do.
            return {"timer": timer}
        body = _clean({"id": str(uuid.uuid4()), "kind": kind, "side": call.data.get("side"), "start": api_time(call.data.get("start"))})
        return {"timer": await _call(coordinator, child_id, coordinator.client.start_timer(child_id, body))}

    async def switch_side(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        timer = await _need_timer(coordinator, child_id, call.data["kind"])
        body = _clean({"side": call.data.get("side")}) or None
        return {"timer": await _call(coordinator, child_id, coordinator.client.timer_action(timer["id"], "switch", body))}

    async def pause_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        timer = await _need_timer(coordinator, child_id, call.data["kind"])
        if not timer.get("running"):
            return {"timer": timer}
        return {"timer": await _call(coordinator, child_id, coordinator.client.timer_action(timer["id"], "pause"))}

    async def resume_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        timer = await _need_timer(coordinator, child_id, call.data["kind"])
        body = _clean({"side": call.data.get("side")}) or None
        return {"timer": await _call(coordinator, child_id, coordinator.client.timer_action(timer["id"], "resume", body))}

    async def adjust_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        d = call.data
        timer = await _need_timer(coordinator, child_id, d["kind"])
        body = _clean(
            {
                "start": api_time(d.get("start")),
                "left_seconds": _seconds(d.get("left_minutes")),
                "right_seconds": _seconds(d.get("right_minutes")),
                "seconds": _seconds(d.get("minutes")),
            }
        )
        return {"timer": await _call(coordinator, child_id, coordinator.client.edit_timer(timer["id"], body))}

    async def stop_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        d = call.data
        timer = await _need_timer(coordinator, child_id, d["kind"])
        unit = d.get("volume_unit")
        body = _clean(
            {
                "event_id": str(uuid.uuid4()),
                "end": api_time(d.get("end")),
                "note": d.get("note"),
                "left_ml": volume_ml(hass, d.get("left_amount"), unit),
                "right_ml": volume_ml(hass, d.get("right_amount"), unit),
                "location": d.get("location"),
            }
        )
        try:
            entry = await coordinator.client.timer_action(timer["id"], "stop", body)
        except NestlingError as e:
            if e.status == 404:
                # Another phone stopped it a moment ago.
                await coordinator.async_refresh_child(child_id)
                raise ServiceValidationError(
                    "The timer was already stopped", translation_domain=DOMAIN, translation_key="already_stopped"
                ) from e
            raise HomeAssistantError(f"Nestling: {e}") from e
        await coordinator.async_refresh_child(child_id)
        return {"entry": entry}

    async def discard_timer(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        timer = await _need_timer(coordinator, child_id, call.data["kind"])
        await _call(coordinator, child_id, coordinator.client.discard_timer(timer["id"]))
        return {}

    async def continue_last(call: ServiceCall) -> ServiceResponse:
        coordinator, child_id = resolve_child(hass, call.data)
        kind = call.data["kind"]
        event_type, method = {"breastfeed": ("feed", "breast"), "pump": ("pump", None), "sleep": ("sleep", None)}[kind]
        try:
            events = await coordinator.client.events(child_id, [event_type], limit=20)
        except NestlingError as e:
            raise HomeAssistantError(f"Nestling: {e}") from e
        last = next((e for e in events if method is None or e.get("method") == method), None)
        if last is None:
            raise ServiceValidationError(f"No {kind} entry to continue", translation_domain=DOMAIN, translation_key="nothing_to_continue")
        body = _clean({"timer_id": str(uuid.uuid4()), "side": call.data.get("side")})
        return {"timer": await _call(coordinator, child_id, coordinator.client.continue_event(last["id"], body))}

    actions = {
        "log_feed": (log_feed, FEED_SCHEMA),
        "log_sleep": (log_sleep, SLEEP_SCHEMA),
        "log_diaper": (log_diaper, DIAPER_SCHEMA),
        "log_pump": (log_pump, PUMP_SCHEMA),
        "log_growth": (log_growth, GROWTH_SCHEMA),
        "log_health": (log_health, HEALTH_SCHEMA),
        "log_activity": (log_activity, ACTIVITY_SCHEMA),
        "log_note": (log_note, NOTE_SCHEMA),
        "start_timer": (start_timer, START_TIMER_SCHEMA),
        "switch_side": (switch_side, SWITCH_SIDE_SCHEMA),
        "pause_timer": (pause_timer, KIND_ONLY_SCHEMA),
        "resume_timer": (resume_timer, RESUME_TIMER_SCHEMA),
        "adjust_timer": (adjust_timer, ADJUST_TIMER_SCHEMA),
        "stop_timer": (stop_timer, STOP_TIMER_SCHEMA),
        "discard_timer": (discard_timer, KIND_ONLY_SCHEMA),
        "continue_last": (continue_last, CONTINUE_SCHEMA),
    }
    for name, (handler, schema) in actions.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL)

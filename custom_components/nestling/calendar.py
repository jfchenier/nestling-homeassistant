"""A calendar per child: every entry, and the timers running now."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .api import NestlingError
from .const import DOMAIN
from .coordinator import NestlingConfigEntry
from .entity import NestlingEntity, add_child_entities
from .units import metric

# Entries without an end (diapers, bottles, notes...) get this length so they show on the calendar.
POINT_LENGTH = timedelta(minutes=15)
# Entries that started this long before the range can still reach into it (a night's sleep).
LOOKBACK = timedelta(days=1)


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [EntriesCalendar(coordinator, c, "entries")])


def _number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


class Labels:
    """Entry titles in Home Assistant's language (from the `selector` texts)."""

    def __init__(self, texts: dict[str, str], hass: HomeAssistant) -> None:
        self._texts = texts
        self._metric = metric(hass)

    def get(self, selector: str, key: str) -> str:
        return self._texts.get(f"component.{DOMAIN}.selector.{selector}.options.{key}") or key.replace("_", " ").capitalize()

    def volume(self, ml: float) -> str:
        return f"{_number(ml)} mL" if self._metric else f"{_number(ml / 29.5735295625)} fl oz"

    def weight(self, g: float) -> str:
        return f"{_number(g / 1000)} kg" if self._metric else f"{_number(g / 453.59237)} lb"

    def temperature(self, c: float) -> str:
        return f"{_number(c)} °C" if self._metric else f"{_number(c * 9 / 5 + 32)} °F"

    def title(self, e: dict[str, Any]) -> str:
        kind = e.get("type", "")
        parts: list[str] = []
        if kind == "feed":
            method = e.get("method") or "breast"
            parts.append(self.get("calendar_entry", f"feed_{method}"))
            if e.get("amount_ml") is not None:
                parts.append(self.volume(e["amount_ml"]))
            if e.get("foods"):
                parts.append(e["foods"])
        elif kind == "diaper":
            if e.get("potty"):
                parts.append(self.get("potty", e["potty"]))
            else:
                wet, dirty = e.get("wet"), e.get("dirty")
                key = "both" if wet and dirty else "wet" if wet else "dirty" if dirty else "dry" if e.get("dry") else None
                parts.append(self.get("calendar_entry", f"diaper_{key}" if key else "diaper"))
        elif kind == "health":
            parts.append(self.get("health_kind", e.get("kind") or "medicine"))
            if e.get("name"):
                parts.append(e["name"])
            if e.get("temperature_c") is not None:
                parts.append(self.temperature(e["temperature_c"]))
        elif kind == "growth":
            parts.append(self.get("calendar_entry", "growth"))
            if e.get("weight_g") is not None:
                parts.append(self.weight(e["weight_g"]))
        elif kind == "activity":
            parts.append(self.get("calendar_entry", "activity"))
            if e.get("kind"):
                parts.append(e["kind"].replace("_", " "))
        elif kind == "milestone":
            parts.append(e.get("name") or self.get("calendar_entry", "milestone"))
        else:
            parts.append(self.get("calendar_entry", kind))
        return " · ".join(parts)


class EntriesCalendar(NestlingEntity, CalendarEntity):
    """The child's entries; a running timer is the event happening now."""

    _labels: Labels | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._load_labels()

    async def _load_labels(self) -> Labels:
        texts = await async_get_translations(self.hass, self.hass.config.language, "selector", {DOMAIN})
        self._labels = Labels(texts, self.hass)
        return self._labels

    def _timer_events(self, labels: Labels | None) -> list[CalendarEvent]:
        now = dt_util.now()
        events = []
        for timer in self.summary.get("timers", []):
            start = dt_util.parse_datetime(timer.get("started_at") or "")
            if start is None:
                continue
            kind = timer.get("kind", "")
            title = labels.get("timer_kind", kind) if labels else kind
            events.append(CalendarEvent(start=start, end=max(now, start) + timedelta(minutes=1), summary=title, uid=timer.get("id")))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        # Only the running timers are happening now.
        timers = self._timer_events(self._labels)
        return min(timers, key=lambda e: e.start) if timers else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        labels = await self._load_labels()
        try:
            entries = await self.coordinator.client.events_between(self.child_id, (start_date - LOOKBACK).isoformat(), end_date.isoformat())
        except NestlingError as e:
            raise HomeAssistantError(f"Nestling: {e}") from e
        events: list[CalendarEvent] = []
        for e in entries:
            start = dt_util.parse_datetime(e.get("start") or "")
            if start is None:
                continue
            end = dt_util.parse_datetime(e.get("end") or "") or start
            if end <= start:
                end = start + POINT_LENGTH
            if end <= start_date or start >= end_date:
                continue
            events.append(CalendarEvent(start=start, end=end, summary=labels.title(e), description=e.get("note"), uid=e.get("id")))
        events.extend(t for t in self._timer_events(labels) if t.start < end_date and t.end > start_date)
        events.sort(key=lambda e: e.start)
        return events

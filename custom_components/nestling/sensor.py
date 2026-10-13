"""Sensors: last entries, time awake, running timers, today's totals, daily averages, next medicine dose."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import TIMER_KINDS
from .coordinator import NestlingConfigEntry, NestlingCoordinator
from .entity import NestlingEntity, add_child_entities

Summary = dict[str, Any]


def _time(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


def _last(kind: str) -> Callable[[Summary], dict[str, Any] | None]:
    return lambda s: (s.get("last") or {}).get(kind)


def _entry_attrs(kind: str, keys: tuple[str, ...]) -> Callable[[Summary], dict[str, Any] | None]:
    def attrs(s: Summary) -> dict[str, Any] | None:
        entry = _last(kind)(s)
        if not entry:
            return None
        return {k: entry[k] for k in ("id", "end", *keys, "note") if entry.get(k) is not None}

    return attrs


def _timer(s: Summary, kind: str) -> dict[str, Any] | None:
    return next((t for t in s.get("timers", []) if t.get("kind") == kind), None)


def _awake_since(s: Summary) -> datetime | None:
    if _timer(s, "sleep"):
        return None
    sleep = _last("sleep")(s)
    return _time(sleep.get("end") or sleep.get("start")) if sleep else None


def _today(section: str, key: str, scale: float = 1.0) -> Callable[[Summary], float | None]:
    def value(s: Summary) -> float | None:
        v = ((s.get("today") or {}).get(section) or {}).get(key)
        return None if v is None else round(v / scale, 1) if scale != 1 else v

    return value


def _next_dose(s: Summary) -> datetime | None:
    times = [m["next_at"] for m in s.get("medicines", []) if m.get("next_at")]
    return _time(min(times, key=lambda t: _time(t))) if times else None


def _medicines(s: Summary) -> dict[str, Any] | None:
    meds = s.get("medicines") or []
    if not meds:
        return None
    soonest = min((m for m in meds if m.get("next_at")), key=lambda m: _time(m["next_at"]), default=None)
    return {
        "medicine": soonest["name"] if soonest else None,
        "medicines": [{k: m.get(k) for k in ("name", "next_at", "due", "last_at", "doses_24h", "limited")} for m in meds],
    }


def _average(key: str, scale: float = 1.0) -> Callable[[Summary], float | None]:
    """A daily average from the trends (None until a complete day was logged)."""

    def value(t: Summary) -> float | None:
        averages = t.get("averages") or {}
        v = averages.get(key)
        if v is None or not averages.get("days"):
            return None
        return round(v / scale, 1)

    return value


def _average_attrs(key: str, scale: float = 1.0) -> Callable[[Summary], dict[str, Any] | None]:
    def attrs(t: Summary) -> dict[str, Any] | None:
        averages = t.get("averages") or {}
        if not averages.get("days"):
            return None
        previous = (t.get("previous") or {}).get(key)
        return {
            "days": averages["days"],
            "previous_period": None if previous is None else round(previous / scale, 1),
        }

    return attrs


@dataclass(frozen=True, kw_only=True)
class NestlingSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Summary], Any]
    attrs_fn: Callable[[Summary], dict[str, Any] | None] = lambda s: None
    # Reads the child's trends (`GET /children/{id}/trends`) instead of the summary.
    from_trends: bool = False


def _timestamp(
    key: str, fn: Callable[[Summary], Any], attrs: Callable[[Summary], dict[str, Any] | None] = lambda s: None
) -> NestlingSensorDescription:
    return NestlingSensorDescription(key=key, device_class=SensorDeviceClass.TIMESTAMP, value_fn=fn, attrs_fn=attrs)


def _count(key: str, section: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(key=key, state_class=SensorStateClass.TOTAL_INCREASING, value_fn=_today(section, field))


def _minutes(key: str, section: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(
        key=key,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=_today(section, field, 60.0),
    )


def _volume(key: str, section: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(
        key=key,
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=_today(section, field),
    )


def _avg_count(key: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(
        key=key,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_average(field),
        attrs_fn=_average_attrs(field),
        from_trends=True,
    )


def _avg_minutes(key: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(
        key=key,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_average(field, 60.0),
        attrs_fn=_average_attrs(field, 60.0),
        from_trends=True,
    )


def _avg_volume(key: str, field: str) -> NestlingSensorDescription:
    return NestlingSensorDescription(
        key=key,
        # Home Assistant allows no measurement state class on volumes.
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        suggested_display_precision=0,
        value_fn=_average(field),
        attrs_fn=_average_attrs(field),
        from_trends=True,
    )


SENSORS: tuple[NestlingSensorDescription, ...] = (
    _timestamp(
        "last_feed",
        lambda s: _time((_last("feed")(s) or {}).get("start")),
        _entry_attrs("feed", ("method", "amount_ml", "milk", "left_seconds", "right_seconds", "start_side", "foods")),
    ),
    _timestamp(
        "last_diaper",
        lambda s: _time((_last("diaper")(s) or {}).get("start")),
        _entry_attrs("diaper", ("wet", "dirty", "dry", "potty", "rash", "blowout")),
    ),
    _timestamp(
        "last_pump",
        lambda s: _time((_last("pump")(s) or {}).get("start")),
        _entry_attrs("pump", ("left_ml", "right_ml", "left_seconds", "right_seconds")),
    ),
    _timestamp("awake_since", _awake_since, _entry_attrs("sleep", ("start", "location"))),
    *(_timestamp(f"{kind}_timer_started", lambda s, k=kind: _time((_timer(s, k) or {}).get("started_at"))) for kind in TIMER_KINDS),
    _timestamp("next_medicine_dose", _next_dose, _medicines),
    _count("feeds_today", "feed", "count"),
    _volume("bottle_today", "feed", "bottle_ml"),
    _minutes("breastfeeding_today", "feed", "breast_seconds"),
    _minutes("sleep_today", "sleep", "total_seconds"),
    _minutes("day_sleep_today", "sleep", "day_seconds"),
    _minutes("night_sleep_today", "sleep", "night_seconds"),
    _count("naps_today", "sleep", "nap_count"),
    _count("diapers_today", "diaper", "count"),
    _count("wet_diapers_today", "diaper", "wet"),
    _count("dirty_diapers_today", "diaper", "dirty"),
    _volume("pumped_today", "pump", "total_ml"),
    # Daily averages over the last TREND_DAYS complete days.
    _avg_count("feeds_per_day", "feeds_per_day"),
    _avg_volume("bottle_per_day", "bottle_ml_per_day"),
    _avg_minutes("breastfeeding_per_day", "breast_seconds_per_day"),
    _avg_minutes("sleep_per_day", "sleep_seconds_per_day"),
    _avg_minutes("day_sleep_per_day", "day_sleep_seconds_per_day"),
    _avg_minutes("night_sleep_per_day", "night_sleep_seconds_per_day"),
    _avg_count("naps_per_day", "naps_per_day"),
    _avg_count("diapers_per_day", "diapers_per_day"),
    _avg_count("wet_diapers_per_day", "wet_per_day"),
    _avg_count("dirty_diapers_per_day", "dirty_per_day"),
    _avg_volume("pumped_per_day", "pumped_ml_per_day"),
    _avg_minutes("feed_interval", "feed_interval_seconds"),
    _avg_minutes("wake_window", "wake_window_seconds"),
)


async def async_setup_entry(hass: HomeAssistant, entry: NestlingConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    add_child_entities(coordinator, async_add_entities, lambda c: [NestlingSensor(coordinator, c, d) for d in SENSORS])


class NestlingSensor(NestlingEntity, SensorEntity):
    entity_description: NestlingSensorDescription

    def __init__(self, coordinator: NestlingCoordinator, child: dict[str, Any], description: NestlingSensorDescription) -> None:
        super().__init__(coordinator, child, description.key)
        self.entity_description = description

    @property
    def _source(self) -> Summary:
        if self.entity_description.from_trends:
            return self.coordinator.trends(self.child_id) or {}
        return self.summary

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._source)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs_fn(self._source)

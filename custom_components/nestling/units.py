"""Amounts typed in Home Assistant's units, sent to the API in metric (mL, g, cm, °C)."""

from __future__ import annotations

from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM

VOLUME_UNITS = ("ml", "oz")
WEIGHT_UNITS = ("g", "kg", "lb", "oz")
LENGTH_UNITS = ("cm", "in")
TEMPERATURE_UNITS = ("c", "f")

_TO_ML = {"ml": 1.0, "oz": 29.5735295625}
_TO_G = {"g": 1.0, "kg": 1000.0, "lb": 453.59237, "oz": 28.349523125}
_TO_CM = {"cm": 1.0, "in": 2.54}


def metric(hass: HomeAssistant) -> bool:
    return hass.config.units is METRIC_SYSTEM


def volume_ml(hass: HomeAssistant, value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    return round(value * _TO_ML[unit or ("ml" if metric(hass) else "oz")], 1)


def weight_g(hass: HomeAssistant, value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    return round(value * _TO_G[unit or ("kg" if metric(hass) else "lb")], 1)


def length_cm(hass: HomeAssistant, value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    return round(value * _TO_CM[unit or ("cm" if metric(hass) else "in")], 2)


def temperature_c(hass: HomeAssistant, value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    if unit is None:
        unit = "c" if hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS else "f"
    return round((value - 32) * 5 / 9, 2) if unit == "f" else value

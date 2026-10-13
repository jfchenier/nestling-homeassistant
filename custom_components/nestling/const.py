"""Constants for the Nestling integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "nestling"

CONF_FAMILY_ID: Final = "family_id"

# The name the integration's member gets in the family; entries it logs show it.
DEFAULT_APP_NAME: Final = "Home Assistant"

# Safety net next to the live stream.
UPDATE_INTERVAL: Final = timedelta(minutes=5)
# A burst of changes on the stream costs one summary request per child.
REFRESH_DELAY: Final = 1.0

# The server pings every 15 s; this long without anything means the stream is gone.
STREAM_SILENCE: Final = 40.0
STREAM_BACKOFF_MAX: Final = 60.0

TIMER_KINDS: Final = ("breastfeed", "pump", "sleep")
EVENT_TYPES: Final = ("feed", "sleep", "diaper", "pump", "growth", "health", "activity", "milestone", "note")

# Fired on the bus for every new entry (from Home Assistant or a phone).
EVENT_ENTRY_LOGGED: Final = "nestling_entry_logged"
# Dispatcher signal: a new entry came in for (entry_id) with the entry's JSON.
SIGNAL_ENTRY_LOGGED: Final = "nestling_entry_logged_{}"

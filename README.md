# Nestling for Home Assistant

A Home Assistant integration for [Nestling](https://github.com/jfchenier/nestling), the
self-hosted baby tracker. Log feeds, sleep, diapers, pumping, growth, health and activities from
Home Assistant (buttons, automations, voice, NFC tags), run Nestling's timers, and see what the
app's home screen shows. Changes made on a phone show up in Home Assistant within a second.

Home Assistant joins the family as its own **connected app**, so entries it logs show
"Home Assistant" as the caregiver rather than whoever set it up. It can log entries and use
timers, and nothing else.

## Install

Needs a Nestling server with connected apps (Family → Connected apps in the app).

1. In HACS: ⋮ → Custom repositories → add `https://github.com/jfchenier/nestling-homeassistant`,
   type *Integration*. Then install **Nestling** and restart Home Assistant.
   (Or copy `custom_components/nestling` into your configuration's `custom_components` folder.)
2. Settings → Devices & services → Add integration → **Nestling**.
3. Enter the server's address, e.g. `https://nestling.example.com`, then either:
   - **sign in as a family owner**: your password is used once to add Home Assistant to the
     family; only the connected app's token is kept; or
   - **paste a token** made in the app: Family → Connected apps → Connect an app.

Each child becomes a device. Add the integration once per family.

## Entities (per child)

| Kind | Entities |
| --- | --- |
| Buttons | Wet diaper, Dirty diaper, Wet and dirty diaper, Dry diaper |
| Switches | Nursing timer, Pump timer, Sleep timer: on starts, off stops and saves the entry |
| Side switches | Nursing left, Nursing right: on nurses on that side (starts, switches side or resumes), off pauses |
| Select | Nursing side: switches side while nursing; the side the next timer starts on |
| Sensors | Last feed, Last diaper, Last pump, Awake since, Nursing/Pumping/Sleep started, Next medicine dose |
| Today | Feeds, Bottle, Nursing, Sleep, Day sleep, Night sleep, Naps, Diapers, Wet diapers, Dirty diapers, Pumped |
| Per day | Daily averages over the last 7 complete days: Feeds, Bottle, Nursing, Sleep, Day sleep, Night sleep, Naps, Diapers, Wet diapers, Dirty diapers, Pumped; plus Time between feeds and Wake window |
| Calendar | Entries: every entry, and the timers running now |
| Binary sensors | Sleeping, Nursing, Pumping, Medicine due |
| Event | Entry logged: every new entry, from Home Assistant or a phone |

Timestamps show as "2 hours ago" on a dashboard. Last-entry sensors carry the entry's details as
attributes. Daily averages carry `days` (complete days counted) and `previous_period` (the same
average over the days just before). Day and night follow the family's day hours (Settings → Day and night
in the app). Volumes and durations follow your unit settings.

Every new entry also fires a `nestling_entry_logged` event on the bus, with `child`, `type` and
the whole `entry`.

## Actions

| Action | Main fields |
| --- | --- |
| `nestling.log_feed` | `method` (breast, bottle, combo, solids), `left_minutes`, `right_minutes`, `start_side`, `amount`, `milk`, `formula_name`, `foods` |
| `nestling.log_sleep` | `start`, `end`, `location` |
| `nestling.log_diaper` | `wet`, `dirty`, `dry`, `color`, `consistency`, `rash`, `blowout`, `potty` |
| `nestling.log_pump` | `left_amount`, `right_amount`, `left_minutes`, `right_minutes` |
| `nestling.log_growth` | `weight`, `length`, `head` |
| `nestling.log_health` | `kind` (medicine, temperature, vaccine, appointment, symptom), `name`, `dose`, `dose_unit`, `temperature` |
| `nestling.log_activity` | `kind` (e.g. bath, tummy_time, outdoor) |
| `nestling.log_note` | `note` |
| `nestling.start_timer` | `kind` (breastfeed, pump, sleep), `side`, `start` |
| `nestling.switch_side`, `pause_timer`, `resume_timer` | `kind`, `side` |
| `nestling.adjust_timer` | `kind`, `start`, `left_minutes`, `right_minutes`, `minutes` |
| `nestling.stop_timer` | `kind`, `end`, `note`, `left_amount`, `right_amount`, `location` |
| `nestling.discard_timer` | `kind` |
| `nestling.continue_last` | `kind`, `side`: the last nursing, pumping or sleep entry runs again as a timer |

- **Which child**: pick the child's device, or give `child` (the name). Not needed with one child.
- **Times**: `start` and `end` default to now; times without a zone are in Home Assistant's.
- **Units**: amounts, weights, lengths and temperatures are in your unit system (mL or fl oz,
  kg or lb, cm or in, °C or °F) unless `volume_unit`, `weight_unit`, `length_unit` or
  `temperature_unit` says otherwise.
- Actions return the saved entry or timer (`response_variable`), and a retried call never logs
  twice.

## Examples

A Zigbee button by the changing table:

```yaml
automation:
  - alias: Wet diaper button
    triggers:
      - trigger: device
        domain: zha
        device_id: YOUR_BUTTON
        type: remote_button_short_press
        subtype: button
    actions:
      - action: nestling.log_diaper
        data:
          child: Robin
          wet: true
```

An NFC tag on the crib toggles the sleep timer:

```yaml
automation:
  - alias: Crib tag
    triggers:
      - trigger: tag
        tag_id: crib
    actions:
      - action: switch.toggle
        target:
          entity_id: switch.robin_sleep_timer
```

A reminder when nobody has logged a feed for three hours:

```yaml
automation:
  - alias: Feed reminder
    triggers:
      - trigger: template
        value_template: >
          {{ now() - states('sensor.robin_last_feed') | as_datetime > timedelta(hours=3) }}
    actions:
      - action: notify.notify
        data:
          message: "No feed logged for 3 hours"
```

## Development

The tests run against a real Nestling server:

```sh
pip install -r requirements_test.txt
NESTLING_BIN=/path/to/nestling pytest      # starts the server on a free port
NESTLING_URL=http://localhost:8080 pytest  # or a running one with open registration
```

## License

MIT

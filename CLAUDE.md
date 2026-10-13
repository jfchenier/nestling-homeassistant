# Nestling for Home Assistant — context for Claude Code

HACS custom integration (`custom_components/nestling/`) for the Nestling baby tracker
(server and app: github.com/jfchenier/nestling; its `docs/API.md` is the API reference).
Scope: logging entries and timers. No baby book (milestones, photos, book pages).

**Privacy:** never put the owner's name, email, the live server's domain or any other private URL
in code, docs or commits. Use `nestling.example.com`. The GitHub account/repo names are fine.

## How it works

- Config flow (`config_flow.py`): server URL → sign in once as a family owner (the flow adds a
  connected app, `POST /families/{id}/integrations`, keeps its token and logs the session out)
  or paste a token from the app (Family → Connected apps). One config entry per family, unique id
  = family id. Reauth on 401.
- `coordinator.py`: family + `GET /children/{id}/summary` per child; 5-minute poll plus the
  family's SSE stream (`api.NestlingClient.stream`, 40 s silence watchdog, backoff); changes
  refresh only the child they name, debounced 1 s; new events fire `nestling_entry_logged`.
- One device per child (identifier = child id); entity unique ids are `<child_id>_<key>` and the
  key is also the translation key.
- `services.py`: `log_*` actions and timer actions; every write sends its own UUID (`id`,
  `event_id`, `timer_id`) so retries never double-log. Units follow HA's unit system
  (`units.py`) and are sent to the API in metric.
- Texts: `strings.json` and `translations/{en,fr,es,ru}.json` (same languages as the app; keep
  wording in step with the app's ARB files); `services.yaml` holds the selectors.

## Working agreement

- Change code, then `ruff check . && ruff format --check .` and the tests, then commit and push.
  No release/tag unless the owner asks.
- Tests run against a real server: build jfchenier/nestling (`cargo build`) and run
  `NESTLING_BIN=…/target/debug/nestling pytest`. CI builds the server from `NESTLING_REF`.
- Dev env: Python 3.13 venv with `pip install -r requirements_test.txt`.

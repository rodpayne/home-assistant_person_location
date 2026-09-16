# Person Location Integration — Configuration Data vs Options

This document clarifies which settings belong in `config_entry.data` versus `config_entry.options`, and the implications for how the configuration and options flows should be structured.

---

## Separation of Settings

| Setting | Location | Rationale |
|---------|----------|-----------|
| **API keys** (`CONF_GOOGLE_API_KEY`, `CONF_MAPBOX_API_KEY`, `CONF_MAPQUEST_API_KEY`, `CONF_OSM_API_KEY`, `CONF_RADAR_API_KEY`) | `data` | Static credentials; define integration identity. |
| **Region / Language** (`CONF_REGION`, `CONF_LANGUAGE`) | `data` | Affect geocoding behavior; structural. |
| **Output platform** (`CONF_OUTPUT_PLATFORM`) | `data` | Determines entity domain type; structural. |
| **Create sensors list** (`CONF_CREATE_SENSORS`) | `data` | Defines which entities exist. |
| **Devices list** (`CONF_DEVICES`) | `data` | Defines tracked devices; structural. |
| **Providers list** (`CONF_PROVIDERS`) | `data` | Defines external map/camera providers; structural. |
| **Follow person integration** (`CONF_FOLLOW_PERSON_INTEGRATION`) | `data` | Affects entity creation tied to `person` entities. |
| **Friendly name template** (`CONF_FRIENDLY_NAME_TEMPLATE`) | `options` | Purely presentation/UX. |
| **Hours extended away / minutes just arrived / minutes just left** (`CONF_HOURS_EXTENDED_AWAY`, `CONF_MINUTES_JUST_ARRIVED`, `CONF_MINUTES_JUST_LEFT`) | `options` | Behavioral thresholds; runtime‑tunable. |
| **Show zone when away** (`CONF_SHOW_ZONE_WHEN_AWAY`) | `options` | UX toggle; runtime‑tunable. |

---

## Implications for the Flow

### ConfigFlow (`config_entry.data`)
- Handles **structural configuration**:
  - API keys
  - Region / language
  - Output platform
  - Create sensors list
  - Devices
  - Providers
  - Follow person integration
- These define the *identity* of the integration and the entity set.
- Saved via `async_create_entry(..., data=...)`.

### OptionsFlow (`config_entry.options`)
- Handles **runtime‑tunable behavior**:
  - Friendly name template
  - Hours/minutes thresholds
  - Show zone toggle
- These settings adjust behavior or presentation of existing entities.
- Saved via `async_update_entry(..., options=...)`.

---

## Guiding Principle

- If changing a setting **adds or removes entities**, it belongs in **`data`**.  
- If it only **changes how existing entities behave or display**, it belongs in **`options`**.

---
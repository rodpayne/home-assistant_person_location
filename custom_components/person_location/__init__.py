"""Person Location integration."""

import logging
import pprint
from datetime import datetime, timedelta
from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_point_in_time,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .process_trigger import setup_process_trigger
from .reverse_geocode import setup_reverse_geocode
from .const import (
    API_STATE_OBJECT,
    CONF_CREATE_SENSORS,
    CONF_DEVICES,
    CONF_FOLLOW_PERSON_INTEGRATION,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_NAME,
    CONF_PERSON_NAMES,
    CONF_SHOW_ZONE_WHEN_AWAY,
    CONFIG_SCHEMA,
    ALLOWED_OPTIONS_KEYS,
    DATA_ASYNC_SETUP_ENTRY,
    DATA_CONFIG_ENTRY,
    DATA_CONFIGURATION,
    DATA_ENTITY_INFO,
    DATA_UNDO_STATE_LISTENER,
    DATA_UNDO_UPDATE_LISTENER,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
    DEFAULT_SHOW_ZONE_WHEN_AWAY,
    DOMAIN,
    INTEGRATION_LOCK,
    PERSON_LOCATION_INTEGRATION,
    TITLE_PERSON_LOCATION_CONFIG,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SENSOR]

def merge_entry_data(entry: ConfigEntry, conf: dict) -> tuple[dict, dict]:
    """Merge YAML conf into an existing ConfigEntry's data and options.

    ConfigEntry values override YAML:
    - Dict keys (e.g. CONF_DEVICES): entry overrides YAML.
    - List keys (e.g. CONF_CREATE_SENSORS): merged with duplicates removed.
    - Other keys: entry values override YAML.
    """

    # Start with YAML (which has data + options), overlay with entry data + options
    updated_data_options_and_yaml = {**conf, **entry.data, **entry.options}

    # Then, Merge dict CONF_DEVICES key → entry wins
    conf_devices = conf.get(CONF_DEVICES, {})
    entry_devices = entry.data.get(CONF_DEVICES, {})
    updated_data_options_and_yaml[CONF_DEVICES] = {**conf_devices, **entry_devices}

    # Then, merge list CONF_CREATE_SENSORS key → deduped
    conf_sensors = conf.get(CONF_CREATE_SENSORS, [])
    entry_sensors = entry.data.get(CONF_CREATE_SENSORS, [])
    updated_data_options_and_yaml[CONF_CREATE_SENSORS] = list(
        dict.fromkeys(entry_sensors + conf_sensors)
    )

    # Pull out keys that should be in data only
    updated_data = {
        key: value for key, value in updated_data_options_and_yaml.items() if key not in ALLOWED_OPTIONS_KEYS
    }
    _LOGGER.debug("[merge_entry_data] Parsed updated_data: %s", updated_data)

    # Pull out keys that should be in options only
    updated_options = {
        key: value for key, value in updated_data_options_and_yaml.items() if key in ALLOWED_OPTIONS_KEYS
    }
    _LOGGER.debug("[merge_entry_data] Parsed updated_options: %s", updated_options)

    return updated_data, updated_options

# ------------------------------------------------------------------
# YAML setup (bridges into config entries)
# ------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, yaml_config: dict) -> bool:
    """Set up integration from YAML (bridges into config entries)."""

    # get YAML conf defaults
    default_conf = CONFIG_SCHEMA({DOMAIN: {} })[DOMAIN]
    _LOGGER.debug("[async_setup] default_conf: %s", default_conf)
    if not default_conf.get(CONF_DEVICES, {}):
        default_conf[CONF_DEVICES] = {}
 
    if DOMAIN not in yaml_config:
        _LOGGER.debug("[async_setup] %s not found in yaml_config. Supplying defaults only.", DOMAIN)
        conf_with_defaults = default_conf

    else:
        raw_conf = yaml_config.get(DOMAIN)
        _LOGGER.debug("[async_setup] raw_conf: %s", raw_conf)
        try:
            conf = CONFIG_SCHEMA({DOMAIN: raw_conf})[DOMAIN]
        except vol.Invalid as err:
            _LOGGER.error("[async_setup] Invalid yaml configuration: %s", err)
            return False
        conf_with_defaults = {**default_conf, **conf}

        # translate YAML way of specifying 'person_names' into 'devices' format
        conf_person_names = conf_with_defaults.pop(CONF_PERSON_NAMES, [])
        if conf_person_names:
            _LOGGER.debug("[async_setup] conf_person_names: %s", conf_person_names)
            conf_devices = {
                device: person[CONF_NAME]
                for person in conf_person_names
                for device in person[CONF_DEVICES]
            }
            _LOGGER.debug("[async_setup] conf_devices: %s", conf_devices)
            conf_with_defaults[CONF_DEVICES] = conf_devices

    existing_entries = hass.config_entries.async_entries(DOMAIN)
    if existing_entries:
        entry = existing_entries[0]  
        _LOGGER.debug("[async_setup] Updating existing entry %s", entry.entry_id)
        _LOGGER.debug("[async_setup] conf_with_defaults: %s", conf_with_defaults)
        _LOGGER.debug("[async_setup] entry.data: %s", entry.data)
        _LOGGER.debug("[async_setup] entry.options: %s", entry.options)
        
        new_data, new_options = merge_entry_data(entry, conf_with_defaults)
        _LOGGER.debug("[async_setup] new_data: %s", new_data)
        _LOGGER.debug("[async_setup] new_options: %s", new_options)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
        )

    else:

        _LOGGER.debug("[async_setup] Initiating config flow to create entry")
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=conf_with_defaults,
            )
        )

    return True

# ------------------------------------------------------------------
# Options update listener
# ------------------------------------------------------------------

async def async_options_update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle config_flow options updates."""
    return await hass.data[DOMAIN][DATA_ASYNC_SETUP_ENTRY](hass, entry)

# ------------------------------------------------------------------
# Config entry setup
# ------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a ConfigEntry."""

    _LOGGER.debug("[async_setup_entry] Setting up entry: %s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_CONFIG_ENTRY] = entry

    if DATA_UNDO_UPDATE_LISTENER not in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER] = entry.add_update_listener(
            async_options_update_listener
        )

    # Create integration object
    pli = PERSON_LOCATION_INTEGRATION(f"{DOMAIN}.integration", hass, entry.data)
    # Explicit startup flag so logic downstream is predictable
    pli.attributes.setdefault("startup", True)

    # Store integration object
    hass.data[DOMAIN][entry.entry_id] = pli
    # Some code references may expect hass.data[DOMAIN]["integration"]
    hass.data[DOMAIN]["integration"] = pli

    # Setup services used by sensors/camera and triggers
    setup_process_trigger(pli)
    setup_reverse_geocode(pli)

    # Services: geocode on/off
    def handle_geocode_api_on(call):
        _LOGGER.debug("[geocode_api_on] === Start ===")
        with INTEGRATION_LOCK:
            _LOGGER.debug("[geocode_api_on] INTEGRATION_LOCK obtained")
            pli.state = STATE_ON
            pli.attributes["icon"] = "mdi:api"
            pli.set_state()
            _LOGGER.debug("[geocode_api_on] INTEGRATION_LOCK release...")
        _LOGGER.debug("[geocode_api_on] === Return ===")

    def handle_geocode_api_off(call):
        _LOGGER.debug("[geocode_api_off] === Start ===")
        with INTEGRATION_LOCK:
            _LOGGER.debug("[geocode_api_off] INTEGRATION_LOCK obtained")
            pli.state = STATE_OFF
            pli.attributes["icon"] = "mdi:api-off"
            pli.set_state()
            _LOGGER.debug("[geocode_api_off] INTEGRATION_LOCK release...")
        _LOGGER.debug("[geocode_api_off] === Return ===")

    hass.services.async_register(DOMAIN, "geocode_api_on", handle_geocode_api_on)
    hass.services.async_register(DOMAIN, "geocode_api_off", handle_geocode_api_off)

    # ------------------------------------------------------------------
    # Listeners: device tracker state change
    # ------------------------------------------------------------------

    def _handle_device_tracker_state_change(
        event: Event[EventStateChangedData],
    ) -> None:
        """Handle device tracker state change event."""
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        _LOGGER.debug(
            "[_handle_device_tracker_state_change] === Start === (%s)", entity_id
        )

        from_state = getattr(old_state, "state", "unknown")
        service_data = {
            "entity_id": entity_id,
            "from_state": from_state,
            "to_state": new_state.state,
        }
        hass.services.call(DOMAIN, "process_trigger", service_data, False)

        _LOGGER.debug("[_handle_device_tracker_state_change] === Return ===")

    #track_state_change_event = threaded_listener_factory(async_track_state_change_event)

    def _listen_for_device_tracker_state_changes(entity_id: str):
        """Register state listener for a device tracker entity."""
        if entity_id not in pli.entity_info:
            pli.entity_info[entity_id] = {}

        if DATA_UNDO_STATE_LISTENER not in pli.entity_info[entity_id]:
            remove = async_track_state_change_event(
                hass,
                entity_id,
                _handle_device_tracker_state_change,
            )
            if remove:
                pli.entity_info[entity_id][DATA_UNDO_STATE_LISTENER] = remove
                _LOGGER.debug(
                    "[_listen_for_device_tracker_state_changes] Registered for %s",
                    entity_id,
                )
    def _listen_for_configured_entities(hass: HomeAssistant, pli_obj: PERSON_LOCATION_INTEGRATION):
        """Register listeners for configured person/device entities."""
        _LOGGER.debug("[_listen_for_configured_entities] === Start ===")

        if pli_obj.configuration.get(CONF_FOLLOW_PERSON_INTEGRATION):
            for entity_id in hass.states.entity_ids("person"):
                _listen_for_device_tracker_state_changes(entity_id)

        for device in pli_obj.configuration.get(CONF_DEVICES, {}).keys():
            _listen_for_device_tracker_state_changes(device)

        _LOGGER.debug("[_listen_for_configured_entities] === Return ===")

    # ------------------------------------------------------------------
    # Inner _async_setup_entry (options merge + post-merge actions)
    # ------------------------------------------------------------------

    async def _async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Process config_flow configuration and options."""
        _LOGGER.debug(
            "[_async_setup_entry] === Start === -data: %s -options: %s",
            pprint.pformat(entry.data),
            pprint.pformat(entry.options),
        )

        friendly_name_template_changed = (
            (not pli.attributes.get("startup", True))
            and (CONF_FRIENDLY_NAME_TEMPLATE in entry.options)
            and (CONF_FRIENDLY_NAME_TEMPLATE in (pli.configuration or {}))
            and (
                entry.options[CONF_FRIENDLY_NAME_TEMPLATE]
                != pli.configuration[CONF_FRIENDLY_NAME_TEMPLATE]
            )
        )

        # Merge data and options into runtime configuration
        pli.configuration = {
            **(pli.configuration or {}),
            **(entry.data or {}),
            **(entry.options or {}),
        }
        hass.data[DOMAIN][DATA_CONFIGURATION] = pli.configuration

        # Forward setup to platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Wire listeners based on updated configuration
        _listen_for_configured_entities(hass, pli)

        # Re-apply friendly names if template changed
        if friendly_name_template_changed:
            try:
                entity_info = hass.data[DOMAIN].get(DATA_ENTITY_INFO, {})
                for sensor, info in entity_info.items():
                    if info.get("geocode_count", 0) > 0:
                        _LOGGER.debug("[_async_setup_entry] updating sensor %s", sensor)
                        service_data = {
                            "entity_id": sensor,
                            "friendly_name_template": pli.configuration[
                                CONF_FRIENDLY_NAME_TEMPLATE
                            ],
                            "force_update": False,
                        }
                        await hass.services.async_call(
                            DOMAIN, "reverse_geocode", service_data, False
                        )
            except Exception as e:
                _LOGGER.warning(
                    "Exception updating friendly name after template change - %s", e
                )

        _LOGGER.debug("[_async_setup_entry] === Return ===")
        return True

    # Expose entry setup handler for options updates
    hass.data[DOMAIN][DATA_ASYNC_SETUP_ENTRY] = _async_setup_entry
    await _async_setup_entry(hass, entry)

    # ------------------------------------------------------------------
    # Startup timer (defer wiring until HA finishes starting)
    # ------------------------------------------------------------------

    def _handle_startup_is_done(now):
        """Flip startup flag and rewire listeners when HA has started."""
        
        hass_state = hass.state
        _LOGGER.debug("[_handle_startup_is_done] === Start === hass.state = %s", hass_state)

        # Still starting? Wait another minute
        if hass_state == "STARTING":
            _set_timer_startup_is_done(1)
            return

        pli.attributes["startup"] = False
        _listen_for_configured_entities(hass, pli)
        
        # It should now be safe to expand template sensors for restored target sensors.
        if pli._target_sensors_restored:
            _LOGGER.debug("[_handle_startup_is_done] Running delayed reverse_geocode.")
            for entity_id in pli._target_sensors_restored:
                service_data = {
                    "entity_id": entity_id,
                    "friendly_name_template": pli.configuration.get(
                        CONF_FRIENDLY_NAME_TEMPLATE,
                        DEFAULT_FRIENDLY_NAME_TEMPLATE,
                    ),
                    "force_update": True,
                }
                pli.hass.services.call(
                    DOMAIN, "reverse_geocode", service_data, False
                )
        
        _LOGGER.debug(
            "[_handle_startup_is_done] === Return === startup flag turned off"
        )

    def _set_timer_startup_is_done(minutes: int):
        """Start a timer for 'startup is done'."""
        point_in_time = datetime.now() + timedelta(minutes=minutes)
        async_track_point_in_time(
            hass,
            partial(_handle_startup_is_done),
            point_in_time=point_in_time,
        )

    # Initial listener wiring and startup timer
    _listen_for_configured_entities(hass, pli)
    _set_timer_startup_is_done(2)

    # Async state set (avoid sync set_state during startup)
    hass.loop.call_soon_threadsafe(
        lambda: hass.async_create_task(pli.async_set_state())
    )

    _LOGGER.debug("[async_setup_entry] === Return ===")
    return True

# ------------------------------------------------------------------
# Unload entry (cleanup symmetry)
# ------------------------------------------------------------------

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up orphaned devices/entities."""

    _LOGGER.debug("[async_unload_entry] Unloading entry: %s", entry.entry_id)

    # Unload platforms first
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    # Remove services registered by this integration
    hass.services.async_remove(DOMAIN, "geocode_api_on")
    hass.services.async_remove(DOMAIN, "geocode_api_off")

    # Remove integration object and undo listeners
    pli = hass.data[DOMAIN].pop(entry.entry_id, None)
    if pli:
        for entity_id, info in list(pli.entity_info.items()):
            undo = info.get(DATA_UNDO_STATE_LISTENER)
            if undo:
                undo()
                _LOGGER.debug(
                    "[async_unload_entry] Removed state listener for %s", entity_id
                )
        pli.entity_info.clear()

    # Clean up orphaned devices (no entities left)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    devices = [d for d in dev_reg.devices.values() if entry.entry_id in d.config_entries]
    for device in devices:
        entities = [e for e in ent_reg.entities.values() if e.device_id == device.id]
        if not entities:
            dev_reg.async_remove_device(device.id)
            _LOGGER.info("[async_unload_entry] Removed orphaned device %s", device.name)

    # Optional: clear per-domain bookkeeping
    hass.data[DOMAIN].pop(DATA_CONFIG_ENTRY, None)
    if DATA_UNDO_UPDATE_LISTENER in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER]()
        hass.data[DOMAIN].pop(DATA_UNDO_UPDATE_LISTENER, None)

    _LOGGER.debug("[async_unload_entry] === Return ===")
    return True

# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old configuration entry."""
    _LOGGER.debug(
        "[async_migrate_entry] Migrating configuration %s from version %s.%s",
        config_entry.entry_id,
        config_entry.version,
        config_entry.minor_version,
    )

    if str(config_entry.version) > VERSION:
        _LOGGER.error(
            "Component has been downgraded without restoring configuration from backup"
        )
        return False

    new_data = {**config_entry.data}
    new_options = {**config_entry.options}

    if str(config_entry.version) == "1":
        if str(config_entry.minor_version) < "2":
            if CONF_FRIENDLY_NAME_TEMPLATE not in new_options:
                _LOGGER.debug("Adding %s", CONF_FRIENDLY_NAME_TEMPLATE)
                new_options[CONF_FRIENDLY_NAME_TEMPLATE] = DEFAULT_FRIENDLY_NAME_TEMPLATE
            if CONF_SHOW_ZONE_WHEN_AWAY not in new_options:
                _LOGGER.debug("Adding %s", CONF_SHOW_ZONE_WHEN_AWAY)
                new_options[CONF_SHOW_ZONE_WHEN_AWAY] = DEFAULT_SHOW_ZONE_WHEN_AWAY

    _LOGGER.debug("data=%s", new_data)
    _LOGGER.debug("options=%s", new_options)

    hass.config_entries.async_update_entry(
        config_entry,
        data=new_data,
        options=new_options,
        minor_version="1",
        version=VERSION,
        title=TITLE_PERSON_LOCATION_CONFIG,
    )

    _LOGGER.debug(
        "[async_migrate_entry] Migration to configuration version %s.%s complete",
        config_entry.version,
        config_entry.minor_version,
    )

    return True

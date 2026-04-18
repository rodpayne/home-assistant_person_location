"""__init__.py - Person Location integration."""

# pyright: reportMissingImports=false
from datetime import datetime, timedelta
from functools import partial
import logging

# from typing import TYPE_CHECKING, Any
import voluptuous as vol

# import homeassistant.components.device_tracker
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_FRIENDLY_NAME,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

# import homeassistant.helpers.config_validation as cv
# from homeassistant.helpers.entity import Entity, EntityCategory
# from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)

from .const import (
    ALLOWED_OPTIONS_KEYS,
    CONF_CREATE_SENSORS,
    CONF_DEVICES,
    CONF_FOLLOW_PERSON_INTEGRATION,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_FROM_YAML,
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_NAME,
    CONF_OSM_API_KEY,
    CONF_PERSON_NAMES,
    CONF_RADAR_API_KEY,
    CONF_SHOW_ZONE_WHEN_AWAY,
    CONFIG_SCHEMA,
    DATA_ASYNC_SETUP_ENTRY,
    DATA_ATTRIBUTES,
    DATA_CONFIG_ENTRY,
    DATA_CONFIGURATION,
    DATA_ENTITY_INFO,
    DATA_INTEGRATION,
    DATA_STATE,
    DATA_UNDO_STATE_LISTENER,
    DATA_UNDO_UPDATE_LISTENER,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
    DEFAULT_SHOW_ZONE_WHEN_AWAY,
    DOMAIN,
    INFO_GEOCODE_COUNT,
    INTEGRATION_ASYNCIO_LOCK,
    INTEGRATION_NAME,
    ISSUE_URL,
    STARTUP_VERSION,
    TITLE_PERSON_LOCATION_CONFIG,
    VERSION,
)
from .helpers.api import (
    async_test_google_api_key,
    async_test_mapbox_api_key,
    async_test_mapquest_api_key,
    async_test_osm_api_key,
    async_test_radar_api_key,
)
from .helpers.entity import prune_orphan_template_entities
from .helpers.timestamp import now_utc, to_iso
from .process_trigger import async_setup_process_trigger
from .reverse_geocode import async_setup_reverse_geocode

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.SENSOR, Platform.SWITCH]


# ---------------------------------------------------------------------------
# Integration Runtime Controller Object (pli)
# ---------------------------------------------------------------------------


class PersonLocationIntegration(SensorEntity):
    """Runtime controller for the Person Location integration.

    This is NOT a sensor/switch/camera — it is the integration's
    central state container and must be initialized before any platform loads.
    """

    # _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:api"

    def __init__(self, _hass: HomeAssistant) -> None:
        """Initialize the integration instance."""
        # Log startup banner:
        _LOGGER.info(
            STARTUP_VERSION.format(name=DOMAIN, version=VERSION, issue_link=ISSUE_URL)
        )
        _LOGGER.debug("Controller MRO: %s", self.__class__.mro())

        self.hass = _hass

        # self.entity_id = _entity_id
        self._attr_unique_id = f"{DOMAIN}_controller"
        self._attr_has_entity_name = True
        self._attr_name = "Controller"

        # Runtime state (not necessarily the same as HA state)

        # self.state = "on"
        self._attr_native_value = "on"
        self._attr_should_poll = False
        self._attr_extra_state_attributes = {}
        self.configuration = {}
        self.entity_info = {}

        self._target_sensors_restored = []

        # self._attr_extra_state_attributes[ATTR_ICON] = "mdi:api"
        self._attr_extra_state_attributes["api_last_updated"] = to_iso(now_utc())
        self._attr_extra_state_attributes["api_exception_count"] = 0
        self._attr_extra_state_attributes["api_calls_requested"] = 0
        self._attr_extra_state_attributes["api_calls_skipped"] = 0
        self._attr_extra_state_attributes["api_calls_throttled"] = 0
        self._attr_extra_state_attributes[ATTR_FRIENDLY_NAME] = self._attr_name
        self._attr_extra_state_attributes["startup"] = True
        self._attr_extra_state_attributes["waze_error_count"] = 0
        self._attr_extra_state_attributes[ATTR_ATTRIBUTION] = (
            f"System information for the {INTEGRATION_NAME} integration ({DOMAIN}), version {VERSION}."
        )

    async def async_added_to_hass(self) -> None:
        """Write initial state once entity is registered."""
        _LOGGER.debug("[async_added_to_hass] Write initial state")
        await super().async_added_to_hass()
        await self.async_set_state()

    async def async_set_state(self) -> None:
        """Push integration state into hass.data and HA state machine."""
        if not self.hass:
            _LOGGER.warning("async_set_state called with self.hass empty")
            return
        else:
            _LOGGER.debug("async_set_state called with self.hass available")

        self.hass.data[DOMAIN][DATA_STATE] = self._attr_native_value
        self.hass.data[DOMAIN][DATA_ATTRIBUTES] = self._attr_extra_state_attributes
        self.hass.data[DOMAIN][DATA_CONFIGURATION] = self.configuration
        self.hass.data[DOMAIN][DATA_ENTITY_INFO] = self.entity_info

        self.async_write_ha_state()

        _LOGGER.debug(
            "[async_set_state] (%s) -state: %s -attributes: %s",
            self.entity_id,
            self._attr_native_value,
            self._attr_extra_state_attributes,
        )

    @property
    def device_info(self) -> dict:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, "main")},
            "name": "Person Location Integration",
            "manufacturer": DOMAIN,
            "model": "Integration Controller",
        }


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

    # Preserve conf_with_defaults[CONF_FROM_YAML] because that is the copy that knows
    updated_data_options_and_yaml[CONF_FROM_YAML] = conf.get(CONF_FROM_YAML)

    # Pull out keys that should be in data only
    updated_data = {
        key: value
        for key, value in updated_data_options_and_yaml.items()
        if key not in ALLOWED_OPTIONS_KEYS
    }
    _LOGGER.debug("[merge_entry_data] Parsed updated_data: %s", updated_data)

    # Pull out keys that should be in options only
    updated_options = {
        key: value
        for key, value in updated_data_options_and_yaml.items()
        if key in ALLOWED_OPTIONS_KEYS
    }
    _LOGGER.debug("[merge_entry_data] Parsed updated_options: %s", updated_options)

    return updated_data, updated_options


async def _setup_services(pli: PersonLocationIntegration, hass: HomeAssistant) -> None:
    # Services: geocode on/off
    async def handle_geocode_api_on(call: ServiceCall) -> None:
        _LOGGER.debug("[geocode_api_on] === Start ===")
        async with INTEGRATION_ASYNCIO_LOCK:
            _LOGGER.debug("[geocode_api_on] INTEGRATION_ASYNCIO_LOCK obtained")
            pli._attr_native_value = STATE_ON
            pli._attr_extra_state_attributes["icon"] = "mdi:api"
            await pli.async_set_state()
            _LOGGER.debug("[geocode_api_on] INTEGRATION_ASYNCIO_LOCK release...")
        _LOGGER.debug("[geocode_api_on] === Return ===")

    async def handle_geocode_api_off(call: ServiceCall) -> None:
        _LOGGER.debug("[geocode_api_off] === Start ===")
        async with INTEGRATION_ASYNCIO_LOCK:
            _LOGGER.debug("[geocode_api_off] INTEGRATION_ASYNCIO_LOCK obtained")
            pli._attr_native_value = STATE_OFF
            pli._attr_extra_state_attributes["icon"] = "mdi:api-off"
            await pli.async_set_state()
            _LOGGER.debug("[geocode_api_off] INTEGRATION_ASYNCIO_LOCK release...")
        _LOGGER.debug("[geocode_api_off] === Return ===")

    if not hass.services.has_service(DOMAIN, "geocode_api_on"):
        hass.services.async_register(DOMAIN, "geocode_api_on", handle_geocode_api_on)
    if not hass.services.has_service(DOMAIN, "geocode_api_off"):
        hass.services.async_register(DOMAIN, "geocode_api_off", handle_geocode_api_off)

    # Services: integration functionality
    if not hass.services.has_service(DOMAIN, "reverse_geocode"):
        await async_setup_reverse_geocode(pli)
    if not hass.services.has_service(DOMAIN, "process_trigger"):
        await async_setup_process_trigger(pli)


# ------------------------------------------------------------------
# YAML setup (bridges into config entries)
# ------------------------------------------------------------------


async def async_setup(hass: HomeAssistant, yaml_config: dict) -> bool:
    """Set up integration and bridge YAML into config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get or create integration object
    if DATA_INTEGRATION in hass.data[DOMAIN]:
        _LOGGER.debug(
            "[async_setup] Using existing integration object - this may be an options update, not the initial setup"
        )
        pli = hass.data[DOMAIN][DATA_INTEGRATION]
    else:
        _LOGGER.debug("[async_setup} Creating new integration object")
        # Create integration object
        pli = PersonLocationIntegration(hass)
        hass.data[DOMAIN][DATA_INTEGRATION] = pli

    # Explicit startup flag so logic downstream is predictable
    pli._attr_extra_state_attributes.setdefault("startup", True)

    # Some code references expect hass.data[DOMAIN][DATA_INTEGRATION]
    hass.data[DOMAIN][DATA_INTEGRATION] = pli

    # ------- get configuration from YAML -------

    default_conf = CONFIG_SCHEMA({DOMAIN: {}})[DOMAIN]
    # LOGGER.debug("[async_setup] default_conf: %s", default_conf)
    if not default_conf.get(CONF_DEVICES, {}):
        default_conf[CONF_DEVICES] = {}

    if DOMAIN not in yaml_config:
        _LOGGER.debug(
            "[async_setup] %s not found in yaml_config. Supplying defaults only.",
            DOMAIN,
        )
        conf_with_defaults = default_conf
        conf_with_defaults[CONF_FROM_YAML] = False

    else:
        raw_conf = yaml_config.get(DOMAIN)
        _LOGGER.debug("[async_setup] raw_conf: %s", raw_conf)
        try:
            conf = CONFIG_SCHEMA({DOMAIN: raw_conf})[DOMAIN]
        except vol.Invalid as err:
            _LOGGER.error("[async_setup] Invalid yaml configuration: %s", err)
            return False
        conf_with_defaults = {**default_conf, **conf}
        conf_with_defaults[CONF_FROM_YAML] = True

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

        # YAML schema allows a couple of different formats for the list
        # conf_create_sensors = conf_with_defaults.pop(CONF_CREATE_SENSORS, [])
        # conf_with_defaults[CONF_CREATE_SENSORS] = sorted(cv.ensure_list(conf_create_sensors))

    if not pli.configuration:
        pli.configuration = conf_with_defaults

    # ------- register services -------

    await _setup_services(pli, hass)

    # ------- update existing config entry or request a new one -------

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


async def async_options_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Handle config_flow options updates by reloading the entry cleanly."""
    _LOGGER.debug(
        "[async_options_update_listener] Reloading entry %s after options update",
        entry.entry_id,
    )
    await hass.config_entries.async_reload(entry.entry_id)
    return True


# ------------------------------------------------------------------
# Setup from Config Entry
# ------------------------------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a ConfigEntry."""
    _LOGGER.debug("[async_setup_entry] Setting up entry: %s", entry.entry_id)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][DATA_CONFIG_ENTRY] = entry

    # ---------------------------------------------------------
    # 1. Get or create the integration controller (pli)
    # ---------------------------------------------------------
    if DATA_INTEGRATION in hass.data[DOMAIN]:
        pli = hass.data[DOMAIN][DATA_INTEGRATION]
        _LOGGER.debug("[async_setup_entry] Using existing integration object")
    else:
        _LOGGER.debug("[async_setup_entry] Creating new integration object")
        # Create integration object
        pli = PersonLocationIntegration(hass)
        hass.data[DOMAIN][DATA_INTEGRATION] = pli
        # Register services
        await _setup_services(pli, hass)

    pli.hass = hass

    # ---------------------------------------------------------
    # 2. Register the controller entity ONCE
    # ---------------------------------------------------------
    ent_reg = er.async_get(hass)
    existing_entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, "controller")

    if existing_entity_id is None:
        _LOGGER.debug("[async_setup_entry] Registering controller entity")

        async_add_entities = hass.data[DOMAIN].get("sensor_async_add_entities")
        if async_add_entities:
            _LOGGER.debug(
                "[async_setup_entry] Registering controller entity via sensor platform"
            )
            async_add_entities([pli])
            pli._controller_registered = True
        else:
            _LOGGER.debug(
                "[async_setup_entry] sensor_async_add_entities not ready yet; deferring controller registration"
            )
    else:
        _LOGGER.debug(
            "[async_setup_entry] Controller entity already exists (%s); skipping add",
            existing_entity_id,
        )

    # Store integration object for this entry
    hass.data[DOMAIN][entry.entry_id] = pli

    # Initial state push
    # await pli.async_set_state()

    # ---------------------------------------------------------
    # 3. Register options update listener (once)
    # ---------------------------------------------------------
    if DATA_UNDO_UPDATE_LISTENER not in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER] = entry.add_update_listener(
            async_options_update_listener
        )

    # ---------------------------------------------------------
    # Listeners: device tracker state change
    # ---------------------------------------------------------

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

    # track_state_change_event = threaded_listener_factory(async_track_state_change_event)

    def _listen_for_device_tracker_state_changes(entity_id: str) -> None:
        """Register state listener for a device tracker entity."""
        _LOGGER.debug(
            "[_listen_for_device_tracker_state_changes] Registering for %s", entity_id
        )
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

    def _listen_for_configured_entities(
        hass: HomeAssistant, pli_obj: PersonLocationIntegration
    ) -> None:
        """Register listeners for configured person/device entities."""
        _LOGGER.debug("[_listen_for_configured_entities] === Start ===")

        def _register_person_entities() -> None:
            """Register state listeners for person entities (runs in executor)."""
            for entity_id in hass.states.entity_ids("person"):
                _listen_for_device_tracker_state_changes(entity_id)

        if pli_obj.configuration.get(CONF_FOLLOW_PERSON_INTEGRATION):
            # Run the sync call safely in executor
            hass.loop.run_in_executor(None, _register_person_entities)

        for device in pli_obj.configuration.get(CONF_DEVICES, {}).keys():
            _listen_for_device_tracker_state_changes(device)

        _LOGGER.debug("[_listen_for_configured_entities] === Return ===")

    # ------------------------------------------------------------------
    # Inner _async_setup_entry (options merge + post-merge actions)
    # ------------------------------------------------------------------

    async def _async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Process config_flow configuration and options."""
        _LOGGER.debug(
            "[_async_setup_entry] === Start === Set up configuration for entry %s",
            entry.entry_id,
        )

        # Register services (for update where async_setup does not get called)
        await _setup_services(pli, hass)

        # Determine if friendly name template is being changed
        friendly_name_template_changed = (
            (not pli._attr_extra_state_attributes.get("startup", True))
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
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][DATA_CONFIGURATION] = pli.configuration

        # Forward to platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Try again to register controller if sensor platform wasn't ready earlier
        async_add_entities = hass.data[DOMAIN].get("sensor_async_add_entities")
        if async_add_entities and not getattr(pli, "_controller_registered", False):
            _LOGGER.debug("[async_setup_entry] Late controller registration")
            async_add_entities([pli])
            pli._controller_registered = True
            await pli.async_set_state()

        # Wire listeners based on updated configuration
        # _listen_for_configured_entities(hass, pli)

        # Validate API keys
        valid1 = await async_test_google_api_key(
            hass, pli.configuration[CONF_GOOGLE_API_KEY]
        )
        valid2 = await async_test_mapquest_api_key(
            hass, pli.configuration[CONF_MAPQUEST_API_KEY]
        )
        valid3 = await async_test_osm_api_key(hass, pli.configuration[CONF_OSM_API_KEY])
        valid4 = await async_test_mapbox_api_key(
            hass, pli.configuration[CONF_MAPBOX_API_KEY]
        )
        valid5 = await async_test_radar_api_key(
            hass, pli.configuration[CONF_RADAR_API_KEY]
        )

        if all([valid1, valid2, valid3, valid4, valid5]):
            _LOGGER.debug("All configured API keys have passed validation")

        # Re-apply friendly names if template changed
        if friendly_name_template_changed:
            try:
                entity_info = hass.data[DOMAIN].get(DATA_ENTITY_INFO, {})
                for sensor, info in entity_info.items():
                    if info.get(INFO_GEOCODE_COUNT, 0) > 0:
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

    def _handle_startup_is_done(now: datetime) -> None:
        """Flip startup flag and rewire listeners when HA has started."""
        _LOGGER.debug("[_handle_startup_is_done] === Start === %s", now)

        # Still starting? Wait another minute
        if not hass.is_running:
            _LOGGER.debug("[_handle_startup_is_done] === Delay ===")
            _set_timer_startup_is_done(1)
            return

        _listen_for_configured_entities(hass, pli)

        pli._attr_extra_state_attributes["startup"] = False
        # It should now be safe to expand template sensors for restored target sensors.
        if pli._target_sensors_restored:
            _LOGGER.debug("[_handle_startup_is_done] Running delayed reverse_geocode.")
            while pli._target_sensors_restored:
                entity_id = pli._target_sensors_restored.pop()
                service_data = {
                    "entity_id": entity_id,
                    "friendly_name_template": pli.configuration.get(
                        CONF_FRIENDLY_NAME_TEMPLATE,
                        DEFAULT_FRIENDLY_NAME_TEMPLATE,
                    ),
                    "force_update": True,
                }
                hass.services.call(DOMAIN, "reverse_geocode", service_data, False)

        _LOGGER.debug(
            "[_handle_startup_is_done] === Return === startup flag is turned off"
        )

    def _set_timer_startup_is_done(minutes: int) -> None:
        """Start a timer for 'startup is done'."""
        point_in_time = now_utc() + timedelta(minutes=minutes)
        async_track_point_in_time(
            hass,
            partial(_handle_startup_is_done),
            point_in_time=point_in_time,
        )

    _set_timer_startup_is_done(1)

    # Async state set (avoid sync set_state during startup)
    await pli.async_set_state()

    _LOGGER.debug("[async_setup_entry] === Return ===")
    return True


# ------------------------------------------------------------------
# Unload entry (cleanup symmetry)
# ------------------------------------------------------------------


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up orphaned devices/entities."""
    _LOGGER.debug("[async_unload_entry] Unloading entry: %s", entry.entry_id)

    # ---------------------------------------------------------
    # 1. Unload all platforms (sensor, switch, camera)
    # ---------------------------------------------------------
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.debug("[async_unload_entry] async_unload_platforms failed")
        return False

    # ---------------------------------------------------------
    # 2. Remove template sensors created by this integration
    # ---------------------------------------------------------
    removed = await prune_orphan_template_entities(hass)
    if removed:
        _LOGGER.debug(
            "[async_unload_entry] Removed orphan template entities: %s", removed
        )

    # ---------------------------------------------------------
    # 3. Remove API switch entities tied to this entry
    # ---------------------------------------------------------
    ent_reg = er.async_get(hass)
    # Debug: list all entities for this integration
    for e in ent_reg.entities.values():
        if e.platform == DOMAIN:
            _LOGGER.debug(
                "[async_unload_entry] Found entity: entity_id=%s unique_id=%s",
                e.entity_id,
                e.unique_id,
            )

    removed_count = 0
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.domain == "switch":
            ent_reg.async_remove(entity.entity_id)
            removed_count += 1
    _LOGGER.debug("[async_unload_entry] Removed %d API switch entities", removed_count)

    # ---------------------------------------------------------
    # 4. Remove controller entity tied to this entry
    # ---------------------------------------------------------
    ent_reg = er.async_get(hass)
    controller_entity_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_controller"
    )

    if controller_entity_id:
        _LOGGER.debug(
            "[async_unload_entry] Removing controller entity %s", controller_entity_id
        )
        ent_reg.async_remove(controller_entity_id)
    else:
        _LOGGER.debug("[async_unload_entry] Controller entity not found for removal")

    # ---------------------------------------------------------
    # 5. Remove services registered by this integration
    # ---------------------------------------------------------
    for service in (
        "geocode_api_on",
        "geocode_api_off",
        "process_trigger",
        "reverse_geocode",
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    # ---------------------------------------------------------
    # 6. Remove state listeners stored in the controller
    # ---------------------------------------------------------
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION)
    if pli:
        for entity_id, info in list(pli.entity_info.items()):
            undo = info.get(DATA_UNDO_STATE_LISTENER)
            if undo:
                undo()
                _LOGGER.debug(
                    "[async_unload_entry] Removed state listener for %s", entity_id
                )
        pli.entity_info.clear()

    # ---------------------------------------------------------
    # 7. Remove controller device (if no entities remain)
    # ---------------------------------------------------------
    dev_reg = dr.async_get(hass)
    for device in list(dev_reg.devices.values()):
        if entry.entry_id in device.config_entries:
            # Remove only if no entities remain
            entities = [
                e for e in ent_reg.entities.values() if e.device_id == device.id
            ]
            if not entities:
                _LOGGER.debug(
                    "[async_unload_entry] Removing orphaned device %s",
                    device.name,
                )
                dev_reg.async_remove_device(device.id)

    # ---------------------------------------------------------
    # 8. Clean up hass.data bookkeeping
    # ---------------------------------------------------------
    hass.data[DOMAIN].pop(DATA_CONFIG_ENTRY, None)

    pli = hass.data[DOMAIN].pop(DATA_INTEGRATION, None)
    if pli:
        for entity_id, info in list(pli.entity_info.items()):
            undo = info.get(DATA_UNDO_STATE_LISTENER)
            if undo:
                undo()
                _LOGGER.debug(
                    "[async_unload_entry] Removed state listener for %s", entity_id
                )
        # Reset registration flag so next setup will add the controller again
        pli._controller_registered = False

    if DATA_UNDO_UPDATE_LISTENER in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER]()
        hass.data[DOMAIN].pop(DATA_UNDO_UPDATE_LISTENER, None)

    _LOGGER.debug("[async_unload_entry] === Return ===")
    return True


# ------------------------------------------------------------------
# Migration
# ------------------------------------------------------------------

# Note: Update MIGRATION_SCHEMA_VERSION if integration can't be reverted without restore
MIGRATION_SCHEMA_VERSION = 2
MIGRATION_SCHEMA_MINOR = 1


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old configuration entry."""
    _LOGGER.debug(
        "[async_migrate_entry] Migrating configuration %s from version %s.%s",
        config_entry.entry_id,
        config_entry.version,
        config_entry.minor_version,
    )

    # TODO: add this test when there is a migration that can't be reverted
    # if str(config_entry.version) > MIGRATION_SCHEMA_VERSION:
    #    _LOGGER.error(
    #        "Component has been downgraded without restoring configuration from backup"
    #    )
    #    return False

    new_data = {**config_entry.data}
    new_options = {**config_entry.options}

    if str(config_entry.version) == "1":
        if str(config_entry.minor_version) < "2":
            if CONF_FRIENDLY_NAME_TEMPLATE not in new_options:
                _LOGGER.debug("Adding %s", CONF_FRIENDLY_NAME_TEMPLATE)
                new_options[CONF_FRIENDLY_NAME_TEMPLATE] = (
                    DEFAULT_FRIENDLY_NAME_TEMPLATE
                )
            if CONF_SHOW_ZONE_WHEN_AWAY not in new_options:
                _LOGGER.debug("Adding %s", CONF_SHOW_ZONE_WHEN_AWAY)
                new_options[CONF_SHOW_ZONE_WHEN_AWAY] = DEFAULT_SHOW_ZONE_WHEN_AWAY

    hass.config_entries.async_update_entry(
        config_entry,
        data=new_data,
        options=new_options,
        minor_version=MIGRATION_SCHEMA_MINOR,
        version=MIGRATION_SCHEMA_VERSION,
        title=TITLE_PERSON_LOCATION_CONFIG,
    )

    _LOGGER.debug(
        "[async_migrate_entry] Migration to configuration version %s.%s complete",
        config_entry.version,
        config_entry.minor_version,
    )

    return True

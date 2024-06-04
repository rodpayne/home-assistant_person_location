"""
The person_location integration.

This integration supplies a service to reverse geocode the location
using Open Street Map (Nominatim) or Google Maps or MapQuest and
calculate the distance from home (miles and minutes) using
WazeRouteCalculator.
"""

import logging
from datetime import datetime, timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.event import track_point_in_time, track_state_change

from .const import (
    API_STATE_OBJECT,
    CONF_DEVICES,
    CONF_FOLLOW_PERSON_INTEGRATION,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_MINOR_VERSION as new_configuration_minor_version,
    CONF_SHOW_ZONE_WHEN_AWAY,
    CONF_VERSION as new_configuration_version,
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
)
from .process_trigger import setup_process_trigger
from .reverse_geocode import setup_reverse_geocode

_LOGGER = logging.getLogger(__name__)


def setup(hass, config):
    """Setup is called by Home Assistant to load our integration."""

    _LOGGER.debug("[setup] === Start ===")

    pli = PERSON_LOCATION_INTEGRATION(API_STATE_OBJECT, hass, config)
    setup_process_trigger(pli)
    setup_reverse_geocode(pli)

    def handle_geocode_api_on(call):
        """Turn on the geocode service."""

        _LOGGER.debug("[geocode_api_on] === Start===")
        with INTEGRATION_LOCK:
            """Lock while updating the pli(API_STATE_OBJECT)."""
            _LOGGER.debug("[handle_geocode_api_on]" + " INTEGRATION_LOCK obtained")

            _LOGGER.debug("Setting " + API_STATE_OBJECT + " on")
            pli.state = STATE_ON
            pli.attributes["icon"] = "mdi:api"
            pli.set_state()
            _LOGGER.debug("[geocode_api_on]" + " INTEGRATION_LOCK release...")
        _LOGGER.debug("[geocode_api_on] === Return ===")

    def handle_geocode_api_off(call):
        """Turn off the geocode service. """

        _LOGGER.debug("[geocode_api_off] === Start ===")
        with INTEGRATION_LOCK:
            """Lock while updating the pli(API_STATE_OBJECT)."""
            _LOGGER.debug("[handle_geocode_api_off]" + " INTEGRATION_LOCK obtained")

            _LOGGER.debug("Setting " + API_STATE_OBJECT + " off")
            pli.state = STATE_OFF
            pli.attributes["icon"] = "mdi:api-off"
            pli.set_state()
            _LOGGER.debug("[handle_geocode_api_off]" + " INTEGRATION_LOCK release...")
        _LOGGER.debug("[geocode_api_off] === Return ===")

    async def _async_setup_entry(hass, entry):
        """Process config_flow configuration and options."""

        _LOGGER.debug(
            "[_async_setup_entry] === Start === -data: %s -options: %s",
            entry.data,
            entry.options,
        )

        friendly_name_template_changed = (
            entry.options["friendly_name_template"] != pli.configuration["friendly_name_template"]
            )
        pli.configuration.update(entry.data)
        pli.configuration.update(entry.options)

        hass.data[DOMAIN][DATA_CONFIGURATION] = pli.configuration

        await hass.async_add_executor_job(_listen_for_configured_entities)

        if friendly_name_template_changed:

            # Update the friendly_name for all enties that have been geocoded:

            entity_info = hass.data[DOMAIN][DATA_ENTITY_INFO]

            for sensor in entity_info:

                if (
                    "geocode_count" in entity_info[sensor]
                    and entity_info[sensor]["geocode_count"] != 0
                ):

                    _LOGGER.debug(f"sensor to be updated = {sensor}")
                    service_data = {
                        "entity_id": sensor,
                        "friendly_name_template": pli.configuration[CONF_FRIENDLY_NAME_TEMPLATE],
                        "force_update": False,
                    }
                    await pli.hass.services.async_call(
                        DOMAIN, "reverse_geocode", service_data, False
                    )


        _LOGGER.debug("[_async_setup_entry] === Return ===")
        return True

    hass.data[DOMAIN][DATA_ASYNC_SETUP_ENTRY] = _async_setup_entry

    hass.services.register(DOMAIN, "geocode_api_on", handle_geocode_api_on)
    hass.services.register(DOMAIN, "geocode_api_off", handle_geocode_api_off)

    def _handle_device_tracker_state_change(entity_id, old_state, new_state):
        """Handle device tracker state change."""

        _LOGGER.debug("[_handle_device_tracker_state_change]" + " === Start ===")

        _LOGGER.debug("[_handle_device_tracker_state_change]" + " (%s) " % (entity_id))
        if hasattr('old_state', 'state'):
            fromState = old_state.state
        else:
            fromState = 'unknown'
        service_data = {
            "entity_id": entity_id,
            "from_state": fromState,
            "to_state": new_state.state,
        }
        hass.services.call(DOMAIN, "process_trigger", service_data, False)

        _LOGGER.debug("[_handle_device_tracker_state_change]" + " === Return ===")

    def _listen_for_device_tracker_state_changes(entity_id):
        """Request notification of device tracker state changes."""

        if entity_id not in pli.entity_info:
            pli.entity_info[entity_id] = {}
        if DATA_UNDO_STATE_LISTENER not in pli.entity_info[entity_id]:
            remove = track_state_change(
                pli.hass,
                entity_id,
                _handle_device_tracker_state_change,
            )
            if remove:
                pli.entity_info[entity_id][DATA_UNDO_STATE_LISTENER] = remove
                _LOGGER.debug(
                    "[_listen_for_device_tracker_state_changes] _handle_device_tracker_state_change (%s)"
                    % (entity_id)
                )

    def _listen_for_configured_entities():
        """Request notification of state changes for configured entities."""

        _LOGGER.debug("[_listen_for_configured_entities] === Start ===")

        if pli.configuration[CONF_FOLLOW_PERSON_INTEGRATION]:
            for entity_id in pli.hass.states.entity_ids("person"):
                _listen_for_device_tracker_state_changes(entity_id)

        for device in pli.configuration[CONF_DEVICES].keys():
            _listen_for_device_tracker_state_changes(device)

        _LOGGER.debug("[_listen_for_configured_entities] === Return ===")

    # Set a timer for when to stop ignoring stuff during startup:

    def _handle_startup_is_done(now):
        """Handle timer for "startup is done"."""

        hass_state = pli.hass.state
        _LOGGER.debug(
            "[_handle_startup_is_done] === Start === hass.state = %s", hass_state
        )

        if hass_state == "STARTING":
            _set_timer_startup_is_done(1)
            return

        pli.attributes["startup"] = False

        _listen_for_configured_entities()

        _LOGGER.debug(
            "[_handle_startup_is_done] === Return === HA just started flag is now turned off"
        )

    def _set_timer_startup_is_done(minutes):
        """Start a timer for "startup is done"."""

        point_in_time = datetime.now() + timedelta(minutes=minutes)
        track_point_in_time(
            hass,
            partial(
                _handle_startup_is_done,
            ),
            point_in_time=point_in_time,
        )

    _listen_for_configured_entities()

    _set_timer_startup_is_done(2)

    pli.set_state()

    _LOGGER.debug("[setup] === Return ===")
    # Return boolean to indicate that setup was successful.
    return True


# ------------------------------------------------------------------


async def async_setup_entry(hass, entry):
    """Accept conf_flow configuration."""

    hass.data[DOMAIN][DATA_CONFIG_ENTRY] = entry

    if DATA_UNDO_UPDATE_LISTENER not in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER] = entry.add_update_listener(
            async_options_update_listener
        )

    return await hass.data[DOMAIN][DATA_ASYNC_SETUP_ENTRY](hass, entry)


async def async_options_update_listener(hass, entry):
    """Accept conf_flow options."""

    return await hass.data[DOMAIN][DATA_ASYNC_SETUP_ENTRY](hass, entry)


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    if DATA_UNDO_UPDATE_LISTENER in hass.data[DOMAIN]:
        hass.data[DOMAIN][DATA_UNDO_UPDATE_LISTENER]()

    hass.data[DOMAIN].pop(DATA_UNDO_UPDATE_LISTENER)
    hass.data[DOMAIN].pop(DATA_CONFIG_ENTRY)

    return True

# ------------------------------------------------------------------

async def async_migrate_entry(hass, config_entry: ConfigEntry):
    """Migrate old configuration entry."""
    _LOGGER.debug("Migrating configuration from version %s.%s", config_entry.version, config_entry.minor_version)

    if config_entry.version > new_configuration_version:
      _LOGGER.error("Component has been downgraded without restoring configuration from backup")
      return False

    new_data = {**config_entry.data}
    new_options = {**config_entry.options}

    if config_entry.version == 1:

        if config_entry.minor_version < 2:
        #    # merge "options" into "data" to simplify reconfiguration flows:
        #    new_data = {**new_data, **new_options}
        #    new_options = {}

            # Add two new settings:
            if CONF_FRIENDLY_NAME_TEMPLATE not in new_options:
                _LOGGER.debug(f"Adding { CONF_FRIENDLY_NAME_TEMPLATE }")
                new_options[CONF_FRIENDLY_NAME_TEMPLATE] = DEFAULT_FRIENDLY_NAME_TEMPLATE
            if CONF_SHOW_ZONE_WHEN_AWAY not in new_options:
                _LOGGER.debug(f"Adding { CONF_SHOW_ZONE_WHEN_AWAY }")
                new_options[CONF_SHOW_ZONE_WHEN_AWAY] = DEFAULT_SHOW_ZONE_WHEN_AWAY

    _LOGGER.debug(f"data={ new_data }")
    _LOGGER.debug(f"options={ new_options }")

    hass.config_entries.async_update_entry(config_entry,
                                           data=new_data,
                                           options=new_options,
                                           minor_version=new_configuration_minor_version,
                                           version=new_configuration_version,
                                           title="Person Locations",
                                           )

    _LOGGER.debug("Migration to configuration version %s.%s successful", config_entry.version, config_entry.minor_version)

    return True
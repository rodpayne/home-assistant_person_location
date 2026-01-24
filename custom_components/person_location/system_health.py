"""system_health.py - Settings -> System -> Repairs -> ⋮ -> System information."""

# See https://developers.home-assistant.io/blog/2020/11/09/system-health-and-templates

import logging

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_DISTANCE_DURATION_SOURCE,
    CONF_FROM_YAML,
    DATA_ATTRIBUTES,
    DATA_CONFIG_ENTRY,
    DATA_CONFIGURATION,
    DATA_ENTITY_INFO,
    DATA_STATE,
    DATA_SWITCH_ENTITIES,
    DOMAIN,
    INFO_GEOCODE_COUNT,
    INFO_LOCALITY,
    INFO_TRIGGER_COUNT,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)


def get_effective_log_level_name(logger: logging.Logger) -> str:
    """Get the effective logging level name."""
    level = logger.getEffectiveLevel()
    mapping = logging.getLevelNamesMapping()

    # Reverse lookup: find the name for this numeric level
    for name, num in mapping.items():
        if num == level:
            return name

    return "UNKNOWN"


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict:
    """Get system health info (Settings -> System -> Repairs ⋮ System information)."""
    return_info = {}
    return_info["Version"] = VERSION

    ha_version = hass.config.as_dict().get("version", "unknown")
    return_info["Home Assistant Version"] = f"core-{ha_version}"

    if DATA_STATE in hass.data[DOMAIN] and DATA_ATTRIBUTES in hass.data[DOMAIN]:
        apiState = hass.data[DOMAIN][DATA_STATE]
        apiAttributesObject = hass.data[DOMAIN][DATA_ATTRIBUTES]

        return_info["State"] = apiState

        if DATA_CONFIGURATION in hass.data[DOMAIN]:
            configured_from_yaml = hass.data[DOMAIN][DATA_CONFIGURATION][CONF_FROM_YAML]
        else:
            configured_from_yaml = False

        if DATA_CONFIG_ENTRY in hass.data[DOMAIN]:
            flow_config_state = return_info["Integration Configuration"] = hass.data[
                DOMAIN
            ][DATA_CONFIG_ENTRY].state
            if configured_from_yaml:
                return_info["Integration Configuration"] = f"yaml + {flow_config_state}"
            else:
                return_info["Integration Configuration"] = flow_config_state
        else:
            return_info["Integration Configuration"] = "yaml only"

        attr_value = apiAttributesObject["api_calls_requested"]
        if attr_value != 0:
            return_info["Geolocation Calls Requested"] = attr_value

        attr_value = apiAttributesObject["startup"]
        if attr_value:
            return_info["Startup In Progress"] = attr_value

        attr_value = apiAttributesObject["api_calls_skipped"]
        if attr_value != 0:
            return_info["Geolocation Calls Skipped"] = attr_value

        attr_value = apiAttributesObject["api_calls_throttled"]
        if attr_value != 0:
            return_info["Geolocation Calls Throttled"] = attr_value

        attr_value = apiAttributesObject["api_exception_count"]
        if attr_value != 0:
            return_info["Geolocation Exception Count"] = attr_value

        attr_value = apiAttributesObject["waze_error_count"]
        if attr_value != 0:
            return_info["WAZE Error Count"] = attr_value

        # Ensure the switch storage exists
        if DATA_SWITCH_ENTITIES in hass.data.get(DOMAIN, {}):
            switch_entries = hass.data[DOMAIN][DATA_SWITCH_ENTITIES]

            for entry_id, entities in switch_entries.items():
                for entity_id, switch_entity in entities.items():
                    provider_id = switch_entity._provider_id
                    enabled = "Enabled" if switch_entity.is_on else "Disabled"

                    success_count = switch_entity._extra_state_attributes.get(
                        "success_count", 0
                    )
                    error_count = switch_entity._extra_state_attributes.get(
                        "error_count", 0
                    )
                    last_error = switch_entity._extra_state_attributes.get("last_error")
                    if success_count > 0 or error_count > 0:
                        # Build the return_info string
                        status = (
                            f"{enabled} | "
                            f"Successful: {success_count} | "
                            f"Errors: {error_count}"
                        )
                        return_info[provider_id] = status

                        if last_error:
                            return_info[provider_id + " Last Error"] = last_error

        if DATA_CONFIGURATION in hass.data[DOMAIN]:
            distance_duration_source = hass.data[DOMAIN][DATA_CONFIGURATION].get(
                CONF_DISTANCE_DURATION_SOURCE, "unknown"
            )
            return_info["Driving Distance/Duration source"] = distance_duration_source
        """
        if DATA_ENTITY_INFO in hass.data.get(DOMAIN, {}):
            entity_info = hass.data[DOMAIN][DATA_ENTITY_INFO]

            for sensor in entity_info:
                if (
                    INFO_TRIGGER_COUNT in entity_info[sensor]
                    and entity_info[sensor][INFO_TRIGGER_COUNT] != 0
                ):
                    return_info[sensor] = (
                        str(entity_info[sensor][INFO_GEOCODE_COUNT])
                        + " geolocated for "
                        + str(entity_info[sensor][INFO_TRIGGER_COUNT])
                        + " triggers, last = "
                        + entity_info[sensor][INFO_LOCALITY]
                    )
        """

        return_info["Effective Logging Level"] = get_effective_log_level_name(_LOGGER)
    return return_info

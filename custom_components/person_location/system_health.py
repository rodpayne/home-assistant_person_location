"""Provide info to system health."""

# https://developers.home-assistant.io/blog/2020/11/09/system-health-and-templates

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_FROM_YAML,
    DATA_ATTRIBUTES,
    DATA_CONFIG_ENTRY,
    DATA_CONFIGURATION,
    DATA_ENTITY_INFO,
    DATA_STATE,
    DOMAIN,
    VERSION,
)


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info)


async def system_health_info(hass):
    """Get info for the system health info (Configuration > Info)."""

    return_info = {}
    return_info["Version"] = VERSION

    if (
        DATA_STATE in hass.data[DOMAIN]
        and DATA_ATTRIBUTES in hass.data[DOMAIN]
        and DATA_ENTITY_INFO in hass.data[DOMAIN]
    ):
        apiState = hass.data[DOMAIN][DATA_STATE]
        apiAttributesObject = hass.data[DOMAIN][DATA_ATTRIBUTES]
        entity_info = hass.data[DOMAIN][DATA_ENTITY_INFO]

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

        attr_value = apiAttributesObject["api_error_count"]
        if attr_value != 0:
            return_info["Geolocation Error Count"] = attr_value

        attr_value = apiAttributesObject["waze_error_count"]
        if attr_value != 0:
            return_info["WAZE Error Count"] = attr_value

        for sensor in entity_info:
            if (
                "trigger_count" in entity_info[sensor]
                and entity_info[sensor]["trigger_count"] != 0
            ):
                return_info[sensor] = (
                    str(entity_info[sensor]["geocode_count"])
                    + " geolocated for "
                    + str(entity_info[sensor]["trigger_count"])
                    + " triggers, last = "
                    + entity_info[sensor]["locality"]
                )

    return return_info

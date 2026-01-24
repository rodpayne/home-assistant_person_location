"""
diagnostics.py - ccessed through Settings → Devices & Services → person_location → Download Diagnostics.

Diagnostics output is a sanitized JSON document containing configuration metadata, provider information,
and recent runtime state. Sensitive values such as API keys are automatically redacted.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    DATA_ATTRIBUTES,
    DATA_CONFIGURATION,
    DATA_INTEGRATION,
    DOMAIN,
    PERSON_LOCATION_INTEGRATION,
    REDACT_KEYS,
)

_LOGGER = logging.getLogger(__name__)


def _redact(data: dict) -> dict:
    """Return a copy of data with sensitive fields redacted."""
    redacted = {}

    for key, value in data.items():
        if key in REDACT_KEYS:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, dict):
            redacted[key] = _redact(value)
        else:
            redacted[key] = value

    return redacted


def get_effective_log_level_name(logger) -> str:
    """Get the effective logging level name."""
    level = logger.getEffectiveLevel()
    mapping = logging.getLevelNamesMapping()

    # Reverse lookup: find the name for this numeric level
    for name, num in mapping.items():
        if num == level:
            return name

    return "UNKNOWN"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Return diagnostics for a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    #
    # --- Registries ---------------------------------------------------------
    #
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entities = []
    devices = []

    for entity_id, entity_entry in ent_reg.entities.items():
        if entity_entry.config_entry_id == entry.entry_id:
            entity_dict = {
                "entity_id": entity_id,
                "unique_id": entity_entry.unique_id,
                "platform": entity_entry.platform,
                "device_id": entity_entry.device_id,
            }
            if entity_id.startswith("switch.api_provider"):
                state_obj = hass.states.get(entity_id)
                enabled = "Enabled" if state_obj.state == "on" else "Disabled"
                success_count = state_obj.attributes.get("success_count", 0)
                error_count = state_obj.attributes.get("error_count", 0)
                entity_dict["status"] = (
                    f"{enabled} | Successful: {success_count} | Errors: {error_count}"
                )
                last_error = state_obj.attributes.get("last_error")
                if last_error:
                    entity_dict["last_error"] = last_error
            elif entity_id.startswith("camera."):
                state_obj = hass.states.get(entity_id)
                attributes = {
                    "api_provider": state_obj.attributes["api_provider"],
                    "key_used": state_obj.attributes["key_used"],
                }
                entity_dict["attributes"] = attributes

            entities.append(entity_dict)

    entities = sorted(entities, key=lambda e: e["entity_id"])

    for device_id, device_entry in dev_reg.devices.items():
        if entry.entry_id in device_entry.config_entries:
            devices.append(
                {
                    "id": device_id,
                    "name": device_entry.name,
                    "model": device_entry.model,
                    "manufacturer": device_entry.manufacturer,
                }
            )

    """
    #
    # --- Providers ----------------------------------------------------------
    #
    providers = {}
    provider_map = data.get("providers", {})

    for provider_id, provider in provider_map.items():
        providers[provider_id] = {
            "name": getattr(provider, "name", None),
            "enabled": getattr(provider, "enabled", None),
            "healthy": getattr(provider, "healthy", None),
            "fail_count": getattr(provider, "fail_count", None),
            "last_error": str(getattr(provider, "last_error", None)),
        }
    """
    """
    #
    # --- Runtime cache ------------------------------------------------------
    #
    cache = data.get("cache", {})
    cache_summary = {
        "last_geocode": cache.get("last_geocode"),
        "last_travel_time": cache.get("last_travel_time"),
        "last_update": cache.get("last_update"),
    }
    """
    """
    #
    # --- Templates (if your integration uses them) --------------------------
    #
    templates = {}
    template_map = data.get("templates", {})

    for key, tpl in template_map.items():
        try:
            info = await tpl.async_render_to_info()
            templates[key] = {
                "template": tpl.template,
                "result": info.result,
                "entities": sorted(info.entities),
                "devices": sorted(info.devices),
            }
        except Exception as err:
            templates[key] = {
                "template": tpl.template,
                "error": str(err),
            }
    """

    pli: PERSON_LOCATION_INTEGRATION = hass.data[DOMAIN][DATA_INTEGRATION]
    pli_dict = pli.__dict__.copy()
    pli_dict.pop("hass")
    pli_dict.pop("DATA_CONFIGURATION")
    pli_dict.pop("configuration")

    # attributes: dict = hass.data[DOMAIN][DATA_ATTRIBUTES]

    logging_effective_level = logging.getLevelNamesMapping().get(
        _LOGGER.getEffectiveLevel(), "UNKNOWN"
    )

    #
    # --- Final diagnostics structure ----------------------------------------
    #
    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
        "runtime": {
            "pli": pli_dict,
            # "attributes": attributes,
            #        "providers": providers,
            #        "cache": cache_summary,
            #        "templates": templates,
        },
        "entities": entities,
        "devices": devices,
        "logging": {
            "effective_level": get_effective_log_level_name(_LOGGER),
        },
    }

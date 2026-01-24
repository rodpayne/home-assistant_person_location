"""Switch platform for API provider toggles."""

import logging
from typing import Any

# from homeassistant.components.logbook.const import DOMAIN as LOGBOOK_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_PROVIDER_SWITCHES,
    DATA_CONFIGURATION,
    DATA_INTEGRATION,
    DATA_SWITCH_ENTITIES,
    DEFAULT_API_KEY_NOT_SET,
    DOMAIN,
    PERSON_LOCATION_INTEGRATION,
    warn_once,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up API provider switches."""
    hass.data[DOMAIN].setdefault(DATA_SWITCH_ENTITIES, {})
    hass.data[DOMAIN][DATA_SWITCH_ENTITIES].setdefault(entry.entry_id, {})

    cfg = hass.data[DOMAIN][DATA_CONFIGURATION]

    entry_switches = hass.data[DOMAIN][DATA_SWITCH_ENTITIES][entry.entry_id]

    entities = []
    for provider_id, key_id in API_PROVIDER_SWITCHES:
        _LOGGER.debug(f"provider_id: {provider_id}, key_id: {key_id}")
        if key_id and key_id in cfg and cfg[key_id] == DEFAULT_API_KEY_NOT_SET:
            _LOGGER.debug("Skipping provider_id %s", provider_id)
            continue
        entity = ApiProviderSwitch(hass, entry, provider_id, key_id)
        entities.append(entity)

        # Store entity reference under this entry_id for lookup helper
        entity_id = f"switch.api_{provider_id}"
        entry_switches[entity_id] = entity

    async_add_entities(entities)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("[async_unload_entry] Unloading $s", entry.id)
    # TODO: Does this actually ever get called?
    warn_once(_LOGGER, "Unexpected call of async_unload_entry")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["switch"])

    if unload_ok:
        # Remove stored entity references for this entry
        entities = hass.data[DOMAIN][DATA_SWITCH_ENTITIES].pop(entry.entry_id, None)
        if entities:
            _LOGGER.debug(
                "Cleaned up %d API provider switch entities for entry %s",
                len(entities),
                entry.entry_id,
            )

    return unload_ok


class ApiProviderSwitch(SwitchEntity):
    """Representation of a switch controlling an API provider."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, provider_id: str, key_id: str
    ) -> None:
        """Initialize a switch entity."""
        self._provider_id = provider_id
        self._hass = hass
        self._pli: PERSON_LOCATION_INTEGRATION = hass.data[DOMAIN].get(
            DATA_INTEGRATION, {}
        )
        self.entry = entry
        self._api_key_id = key_id
        self._enabled = (
            not self._api_key_id
            or self._pli.configuration[self._api_key_id] != DEFAULT_API_KEY_NOT_SET
        )
        self._attr_has_entity_name = True
        self._attr_name = f"{provider_id.replace('_', ' ').title()}"
        self._attr_unique_id = f"{entry.entry_id}_api_provider_{provider_id}"
        # Mark all switches as diagnostic by default
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._extra_state_attributes = {
            "provider_id": self._provider_id,
            "success_count": 0,
            "error_count": 0,
        }

    @property
    def is_on(self) -> bool:
        """Return true if the API provider is enabled."""
        return self._enabled

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the API provider on request, if an API key is configured."""
        if (
            self._api_key_id
            and self._pli.configuration[self._api_key_id] == DEFAULT_API_KEY_NOT_SET
        ):
            self.hass.bus.async_fire(
                "logbook_entry",
                {
                    "name": f"API Provider Switch {self._provider_id.replace('_', ' ').title()}",
                    "message": "cannot be enabled because the API Key is not configured",
                    "domain": DOMAIN,
                    "entity_id": self.entity_id,
                },
            )
            _LOGGER.debug(
                "Cannot Turn ON API provider switch: %s because the API Key is not configured",
                self._provider_id,
            )
            return
        _LOGGER.debug("Turning ON API provider: %s", self._provider_id)
        self._enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the API provider."""
        _LOGGER.debug("Turning OFF API provider: %s", self._provider_id)
        self._enabled = False
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes for debugging."""
        return self._extra_state_attributes

    @property
    def device_info(self) -> dict:
        """Return device info so all switches group under one device."""
        return {
            "identifiers": {(DOMAIN, "main")},
            "name": "API Provider Switch",
            "manufacturer": DOMAIN,
            "model": "Integration Switch",
        }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_switch_entity(
    hass: HomeAssistant, provider_id: str
) -> ApiProviderSwitch | None:
    """Get the switch entity for the specified provider."""
    entity_id = f"switch.api_{provider_id}"

    switch_entries = hass.data[DOMAIN][DATA_SWITCH_ENTITIES]

    for entry_id, entities in switch_entries.items():
        if entity_id in entities:
            return entities[entity_id]
    return None


def is_provider_enabled(hass: HomeAssistant, provider_id: str) -> bool:
    """
    Check if a given API provider switch is currently enabled.

    Args:
        hass: HomeAssistant instance
        provider_id: provider identification (e.g. "google_maps", "mapquest")

    Returns:
        True if the provider switch is ON, False otherwise.
    """
    switch_entity = _get_switch_entity(hass, provider_id)

    if switch_entity is None:
        _LOGGER.debug(
            "[is_provider_enabled] Provider switch not found: %s", provider_id
        )
        return False
    return switch_entity._enabled


def provider_error_count(hass: HomeAssistant, provider_id: str) -> int:
    """
    Return the current error_count for this provider.

    Args:
        hass: HomeAssistant instance
        provider_id: provider identification (e.g. "google_maps", "mapquest")

    Returns:
        error_count.
    """
    switch_entity = _get_switch_entity(hass, provider_id)

    if switch_entity is None:
        _LOGGER.warning(
            "[provider_error_count] Provider switch not found: %s", provider_id
        )
        return 0

    api_error_count = switch_entity._extra_state_attributes.get("error_count", 0)
    return api_error_count


def record_api_success(hass: HomeAssistant, provider_id: str) -> bool:
    """
    Update the use count.

    Args:
        hass: HomeAssistant instance
        provider_id: provider identification (e.g. "google_maps", "mapquest")

    Returns:
        True if the provider switch exists, False otherwise.
    """
    switch_entity = _get_switch_entity(hass, provider_id)

    if not switch_entity:
        _LOGGER.warning(
            "[record_api_success] Provider switch not found: %s", provider_id
        )
        return False

    # With the API disabled, this success must be coming from the config flow test
    switch_entity._enabled = True

    # ⭐ Clear repair notification
    registry = ir.async_get(hass)
    if registry.async_get_issue(DOMAIN, f"{provider_id}_disabled"):
        ir.async_delete_issue(hass, DOMAIN, f"{provider_id}_disabled")

    api_success_count = switch_entity._extra_state_attributes.get("success_count", 0)
    switch_entity._extra_state_attributes["success_count"] = api_success_count + 1
    switch_entity.async_write_ha_state()
    return True


def record_api_error(
    hass: HomeAssistant, provider_id: str, error: str, turn_off: bool = False
) -> bool:
    """
    Update the failure count and last_error.

    Args:
        hass: HomeAssistant instance
        provider_id: provider identification (e.g. "google_maps", "mapquest")

    Returns:
        True if the provider switch exists, False otherwise.
    """
    switch_entity = _get_switch_entity(hass, provider_id)
    if not switch_entity:
        _LOGGER.warning("[record_api_error] Provider switch not found: %s", provider_id)
        return False

    api_error_count = switch_entity._extra_state_attributes.get("error_count", 0)
    switch_entity._extra_state_attributes["error_count"] = api_error_count + 1
    switch_entity._extra_state_attributes["last_error"] = error
    if turn_off:
        switch_entity._enabled = False
        _LOGGER.warning(
            "[record_api_error] Provider switch disabled: %s - %s", provider_id, error
        )
        # ⭐ Create a repair notification - Credentials are invalid or expired
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{provider_id}_disabled",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="provider_disabled",
            translation_placeholders={
                "provider": provider_id.replace("_", " ").title(),
                "error": error,
            },
            data={"provider_id": provider_id},
        )

    # Logbook entry
    switch_entity.hass.bus.async_fire(
        "logbook_entry",
        {
            "name": f"API Provider Switch {switch_entity._provider_id.replace('_', ' ').title()}",
            "message": error,
            "domain": DOMAIN,
            "entity_id": switch_entity.entity_id,
        },
    )
    switch_entity.async_write_ha_state()
    return True

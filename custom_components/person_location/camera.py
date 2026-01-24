"""Support for map as a camera (hybrid config)."""

import asyncio
import logging
from typing import Any

import httpx

from homeassistant.components.camera import Camera
from homeassistant.const import STATE_PROBLEM, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from .const import (
    CONF_CONTENT_TYPE,
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_NAME,
    CONF_OSM_API_KEY,
    CONF_PROVIDERS,
    CONF_RADAR_API_KEY,
    CONF_STATE,
    CONF_STILL_IMAGE_URL,
    CONF_VERIFY_SSL,
    DATA_CONFIGURATION,
    DOMAIN,
    IMAGE_API_PROVIDER_SWITCHES,
    VERSION,
)
from .switch import (
    is_provider_enabled,
    provider_error_count,
    record_api_error,
    record_api_success,
)

_LOGGER = logging.getLogger(__name__)

GET_IMAGE_TIMEOUT = 10

CAMERA_PARENT_DEVICE = DeviceInfo(
    identifiers={(DOMAIN, "map_camera")},
    name="Map Camera",
    manufacturer="rodpayne",
    model="Map Camera Group",
)


def normalize_provider(hass: HomeAssistant, provider: dict) -> dict:
    """Ensure provider dict has Template objects and defaults."""

    def _as_template(value):
        if isinstance(value, Template):
            tpl = value
        else:
            tpl = Template(str(value), hass)
        tpl.hass = hass
        return tpl

    return {
        CONF_NAME: provider[CONF_NAME],
        CONF_STILL_IMAGE_URL: _as_template(provider[CONF_STILL_IMAGE_URL]),
        CONF_STATE: _as_template(provider[CONF_STATE]),
        CONF_CONTENT_TYPE: provider.get(CONF_CONTENT_TYPE, "image/jpeg"),
        CONF_VERIFY_SSL: provider.get(CONF_VERIFY_SSL, True),
    }


async def async_setup_platform(
    hass, config, async_add_entities, discovery_info=None
) -> None:
    """Set up cameras from YAML config."""

    _LOGGER.debug("async_setup_platform: config = %s", config)
    async_add_entities([PersonLocationCamera(hass, normalize_provider(hass, config))])


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    """Set up cameras from config entry providers."""
    _LOGGER.debug("[async_setup_entry] entry: %s", entry)
    providers = entry.data.get(CONF_PROVIDERS, [])
    entities = [
        PersonLocationCamera(hass, normalize_provider(hass, p)) for p in providers
    ]
    async_add_entities(entities, update_before_add=True)


def find_api_key_in_template(
    template_obj: Template | str, variables: dict
) -> str | None:
    """Find API key in template or string."""
    if hasattr(template_obj, "template"):
        template_str = template_obj.template
    else:
        template_str = str(template_obj)

    for key in variables:
        if key in template_str:
            return key

    return None


def provider_for_key(key_used: str) -> str | None:
    """Return provider switch ID that uses the given API key."""
    # return [provider for provider, key in API_PROVIDER_SWITCHES if key == key_used]
    return IMAGE_API_PROVIDER_SWITCHES.get(key_used, None)


class PersonLocationCamera(Camera):
    """A person_location implementation of a map camera."""

    def __init__(self, hass: HomeAssistant, provider) -> None:
        super().__init__()
        self.hass = hass
        self._name = provider[CONF_NAME]
        _LOGGER.debug("PersonLocationCamera: creating name = %s", self._name)
        self._attr_unique_id = f"map_camera_{slugify(self._name)}"
        self._attr_has_entity_name = True
        self._attr_name = provider[CONF_NAME].replace("_", " ").title()
        self._still_image_url = provider[CONF_STILL_IMAGE_URL]
        self._still_image_url.hass = self.hass
        self._state_template = provider[CONF_STATE]
        self._state_template.hass = self.hass
        self.content_type = provider.get(CONF_CONTENT_TYPE, "image/jpeg")
        self.verify_ssl = provider.get(CONF_VERIFY_SSL, True)
        self._auth = None
        self._state = STATE_UNKNOWN
        self._attr_icon = "mdi:map-outline"
        self._last_url = None
        self._last_image = None
        self._attr_device_info = CAMERA_PARENT_DEVICE

        cfg = self.hass.data[DOMAIN][DATA_CONFIGURATION]
        self._template_variables = {
            CONF_GOOGLE_API_KEY: cfg[CONF_GOOGLE_API_KEY],
            CONF_MAPBOX_API_KEY: cfg[CONF_MAPBOX_API_KEY],
            CONF_MAPQUEST_API_KEY: cfg[CONF_MAPQUEST_API_KEY],
            CONF_OSM_API_KEY: cfg[CONF_OSM_API_KEY],
            CONF_RADAR_API_KEY: cfg[CONF_RADAR_API_KEY],
        }
        self._key_used = find_api_key_in_template(
            self._still_image_url, self._template_variables
        )
        self._api_key = cfg[self._key_used]
        self._api_provider = provider_for_key(self._key_used)

        self._attr_extra_state_attributes = {
            "key_used": self._key_used,
            "api_provider": self._api_provider,
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self):
        return self._state

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        """Return bytes of camera image."""
        provider_id = self._attr_extra_state_attributes["api_provider"]
        if not is_provider_enabled(self.hass, provider_id):
            _LOGGER.debug(
                "[async_camera_image] %s not enabled",
                provider_id,
            )
            self._state = STATE_PROBLEM
            return None

        _LOGGER.debug(
            "[async_camera_image] %s is being updated",
            provider_id,
        )

        try:
            self._last_url, self._last_image = await asyncio.shield(
                self._async_camera_image()
            )
        except asyncio.CancelledError as err:
            _LOGGER.warning("Task cancelled getting camera image from %s", self._name)
            raise err

        return self._last_image

    async def _async_camera_image(self) -> tuple[str | None, bytes | None]:
        """Return a still image response from the camera."""
        provider_id = self._attr_extra_state_attributes["api_provider"]
        if not self.enabled:
            self._state = STATE_PROBLEM
            return self._last_url, self._last_image
        try:
            url = self._still_image_url.async_render(**self._template_variables)
        except TemplateError as err:
            _LOGGER.error(
                "Error parsing url template %s: %s", self._still_image_url, err
            )
            return self._last_url, self._last_image

        try:
            new_state = self._state_template.async_render(parse_result=False)
        except TemplateError as err:
            _LOGGER.error(
                "Error parsing state template %s: %s", self._state_template, err
            )
            new_state = STATE_PROBLEM

        if new_state != self._state:
            self._state = new_state
            self.async_schedule_update_ha_state()

        if (url == self._last_url) or url == "None":
            return self._last_url, self._last_image

        error_message = None
        if provider_error_count(self.hass, provider_id) >= 10:
            turn_off = True
        else:
            turn_off = False
        response = None
        try:
            async_client = get_async_client(self.hass, verify_ssl=self.verify_ssl)
            response = await async_client.get(
                url, auth=self._auth, timeout=GET_IMAGE_TIMEOUT
            )
            response.raise_for_status()

            image = response.content
            record_api_success(self.hass, provider_id)
            return url, image

        except asyncio.CancelledError:
            error_message = f"Task cancelled getting camera image from {self._name}"
        except httpx.TimeoutException:
            error_message = f"Timeout getting camera image from {self._name}"
        except httpx.RequestError as err:
            error_message = err.message
        except httpx.HTTPStatusError as err:
            error_message = f"{err}"
            if err.response.status_code == 401:
                turn_off = True
        finally:
            if response:
                await response.aclose()

        record_api_error(
            self.hass,
            provider_id,
            error_message.replace(self._api_key, "**redacted**"),
            turn_off=turn_off,
        )

        self._state = STATE_PROBLEM
        # return self._last_url, self._last_image
        return None, None

    async def async_added_to_hass(self) -> None:
        """Update to expose self._attr_extra_state_attributes."""
        await super().async_added_to_hass()
        _LOGGER.debug("(%s) added to HASS", self.name)

        self.async_write_ha_state()

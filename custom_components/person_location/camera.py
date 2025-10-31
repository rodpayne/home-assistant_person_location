"""Support for map as a camera (hybrid config)."""

import asyncio
import logging
import httpx

from homeassistant.components.camera import Camera
from homeassistant.const import STATE_PROBLEM, STATE_UNKNOWN
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.template import Template

from .const import (
    DOMAIN,
    DATA_CONFIGURATION,
    CONF_PROVIDERS,
    CONF_NAME,
    CONF_STATE,
    CONF_STILL_IMAGE_URL,
    CONF_CONTENT_TYPE,
    CONF_VERIFY_SSL,
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
)

_LOGGER = logging.getLogger(__name__)
GET_IMAGE_TIMEOUT = 10

CAMERA_PARENT_DEVICE = DeviceInfo(
    identifiers={(DOMAIN, "map_cameras")},
    name="Map Cameras",
    manufacturer="rodpayne",
    model="Map Camera Group",
)

def normalize_provider(hass, provider: dict) -> dict:
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

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up cameras from YAML config."""

    _LOGGER.debug("async_setup_platform: config = %s", config)
    async_add_entities([PersonLocationCamera(hass, normalize_provider(hass, config))])

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up cameras from config entry providers."""

    _LOGGER.debug("async_setup_entry: entry = %s", entry)
    providers = entry.data.get(CONF_PROVIDERS, [])
    entities = [PersonLocationCamera(hass, normalize_provider(hass, p)) for p in providers]
    async_add_entities(entities, update_before_add=True)

class PersonLocationCamera(Camera):
    """A person_location implementation of a map camera."""

    def __init__(self, hass, provider):
        super().__init__()
        self.hass = hass
        self._name = provider[CONF_NAME]
        _LOGGER.debug("PersonLocationCamera: creating name = %s", self._name)
        self._attr_unique_id = f"map_camera_{slugify(self._name)}"
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
            "parse_result": False,
            "google_api_key": cfg[CONF_GOOGLE_API_KEY],
            "mapbox_api_key": cfg[CONF_MAPBOX_API_KEY],
            "mapquest_api_key": cfg[CONF_MAPQUEST_API_KEY],
            "osm_api_key": cfg[CONF_OSM_API_KEY],
            "radar_api_key": cfg[CONF_RADAR_API_KEY],
        }

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    async def async_camera_image(self, width=None, height=None):
        """Return bytes of camera image."""
        try:
            self._last_url, self._last_image = await asyncio.shield(
                self._async_camera_image()
            )
        except asyncio.CancelledError as err:
            _LOGGER.warning("Timeout getting camera image from %s", self._name)
            raise err
        return self._last_image

    async def _async_camera_image(self):
        """Return a still image response from the camera."""
        if not self.enabled:
            return self._last_url, self._last_image

        try:
            url = self._still_image_url.async_render(**self._template_variables)
        except TemplateError as err:
            _LOGGER.error("Error parsing url template %s: %s", self._still_image_url, err)
            return self._last_url, self._last_image

        try:
            new_state = self._state_template.async_render(parse_result=False)
        except TemplateError as err:
            _LOGGER.error("Error parsing state template %s: %s", self._state_template, err)
            new_state = STATE_PROBLEM

        if new_state != self._state:
            self._state = new_state
            self.async_schedule_update_ha_state()

        if (url == self._last_url) or url == "None":
            return self._last_url, self._last_image

        response = None
        try:
            async_client = get_async_client(self.hass, verify_ssl=self.verify_ssl)
            response = await async_client.get(url, auth=self._auth, timeout=GET_IMAGE_TIMEOUT)
            response.raise_for_status()
            image = response.content
        except httpx.TimeoutException:
            _LOGGER.error("Timeout getting camera image from %s", self._name)
            self._state = STATE_PROBLEM
            return self._last_url, self._last_image
        except (httpx.RequestError, httpx.HTTPStatusError) as err:
            _LOGGER.error("Error getting new camera image from %s: %s", self._name, err)
            self._state = STATE_PROBLEM
            return self._last_url, self._last_image
        finally:
            if response:
                await response.aclose()

        return url, image
    
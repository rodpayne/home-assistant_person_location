"""Support for map as a camera."""
import asyncio
import logging

import httpx
import voluptuous as vol
from homeassistant.components.camera import (
    DEFAULT_CONTENT_TYPE,
    PLATFORM_SCHEMA,
    Camera,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_STATE,
    CONF_VERIFY_SSL,
    STATE_IDLE,
    STATE_PROBLEM,
    STATE_UNKNOWN,
)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.template import Template

from .const import (
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    DATA_CONFIGURATION,
    DOMAIN,
)


PLATFORMS = ["camera"]

_LOGGER = logging.getLogger(__name__)

CONF_CONTENT_TYPE = "content_type"
CONF_STILL_IMAGE_URL = "still_image_url"

DEFAULT_NAME = "Location Camera"
GET_IMAGE_TIMEOUT = 10

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_STILL_IMAGE_URL): cv.template,
        vol.Optional(CONF_STATE, default=STATE_IDLE): cv.template,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_CONTENT_TYPE, default=DEFAULT_CONTENT_TYPE): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up a location IP Camera."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    async_add_entities([PersonLocationCamera(hass, config)])


class PersonLocationCamera(Camera):
    """A person_location implementation of an IP camera."""

    def __init__(self, hass, device_info):
        """Initialize a person_location camera."""
        super().__init__()
        self.hass = hass
        self._name = device_info.get(CONF_NAME)
        self._still_image_url = device_info[CONF_STILL_IMAGE_URL]
        self._still_image_url.hass = self.hass
        self._state_template = device_info[CONF_STATE]
        self._state_template.hass = self.hass
        self._limit_refetch = True
        self._frame_interval = 1 / 2
        self._supported_features = 0
        self.content_type = device_info[CONF_CONTENT_TYPE]
        self.verify_ssl = device_info[CONF_VERIFY_SSL]
        self._auth = None
        self._state = STATE_UNKNOWN

        self._last_url = None
        self._last_image = None

        google_api_key = self.hass.data[DOMAIN][DATA_CONFIGURATION][CONF_GOOGLE_API_KEY]
        mapbox_api_key = self.hass.data[DOMAIN][DATA_CONFIGURATION][CONF_MAPBOX_API_KEY]
        mapquest_api_key = self.hass.data[DOMAIN][DATA_CONFIGURATION][
            CONF_MAPQUEST_API_KEY
        ]
        osm_api_key = self.hass.data[DOMAIN][DATA_CONFIGURATION][CONF_OSM_API_KEY]
        self._template_variables = {
            "parse_result": False,
            "google_api_key": google_api_key,
            "mapbox_api_key": mapbox_api_key,
            "mapquest_api_key": mapquest_api_key,
            "osm_api_key": osm_api_key,
        }

        self._entities = list(self._extract_entities(self._still_image_url))
        for entity in list(self._extract_entities(self._state_template)):
            if not entity in self._entities:
                self._entities.append(entity)
        _LOGGER.debug("(%s) entities = %s", self._name, self._entities)
        # TODO: set up a Track State Change to do update when template entities change?
        # https://developers.home-assistant.io/docs/core/entity/
        # (It currently checks every ten seconds.)

    def _extract_entities(self, template):
        info = template.async_render_to_info(**self._template_variables)
        return info.entities

    def camera_image(self):
        """Return bytes of camera image."""
        return asyncio.run_coroutine_threadsafe(
            self.async_camera_image(), self.hass.loop
        ).result()

    async def async_camera_image(self):
        """Wrap _async_camera_image with an asyncio.shield."""
        # Shield the request because of https://github.com/encode/httpx/issues/1461
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
        new_state = self._state
        try:
            url = self._still_image_url.async_render(**self._template_variables)
        except TemplateError as err:
            _LOGGER.error("Error parsing template %s: %s", self._still_image_url, err)
            return self._last_url, self._last_image

        try:
            new_state = self._state_template.async_render(
                parse_result=False,
            )
        except TemplateError as err:
            _LOGGER.error("Error parsing template %s: %s", self._state_template, err)
            new_state = STATE_PROBLEM
        if new_state != self._state:
            self._state = new_state
            self.async_schedule_update_ha_state()

        if (url == self._last_url and self._limit_refetch) or url == "None":
            return self._last_url, self._last_image

        response = None
        try:
            async_client = get_async_client(self.hass, verify_ssl=self.verify_ssl)
            response = await async_client.get(
                url, auth=self._auth, timeout=GET_IMAGE_TIMEOUT
            )
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

    @property
    def name(self):
        """Return the name of this device."""
        return self._name

    @property
    def state(self):
        """Return the camera state."""
        return self._state

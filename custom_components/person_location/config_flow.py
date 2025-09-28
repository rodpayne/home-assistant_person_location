"""Add config flow for Person Location."""

import httpx
import logging
import re

from homeassistant.config_entries import ConfigEntry, OptionsFlow

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.httpx_client import get_async_client

from .api import PersonLocation_aiohttp_Client
from .const import (
    CONF_CREATE_SENSORS,
    CONF_DEVICES,
    CONF_FOLLOW_PERSON_INTEGRATION,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_GOOGLE_API_KEY,
    CONF_HOURS_EXTENDED_AWAY,
    CONF_LANGUAGE,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_OSM_API_KEY,
    CONF_OUTPUT_PLATFORM,
    CONF_RADAR_API_KEY,
    CONF_REGION,
    CONF_SHOW_ZONE_WHEN_AWAY,
    DATA_CONFIGURATION,
    DEFAULT_API_KEY_NOT_SET,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
    DEFAULT_HOURS_EXTENDED_AWAY,
    DEFAULT_LANGUAGE,
    DEFAULT_MINUTES_JUST_ARRIVED,
    DEFAULT_MINUTES_JUST_LEFT,
    DEFAULT_OUTPUT_PLATFORM,
    DEFAULT_REGION,
    DEFAULT_SHOW_ZONE_WHEN_AWAY,
    DOMAIN,
    VALID_CREATE_SENSORS,
    VALID_ENTITY_DOMAINS,
    VALID_OUTPUT_PLATFORM,
)

CONF_NEW_DEVICE = "new_device"
CONF_NEW_NAME = "new_person_name"

GET_IMAGE_TIMEOUT = 10

# Platforms
BINARY_SENSOR = "binary_sensor"
SENSOR = "sensor"
SWITCH = "switch"
PLATFORMS = [BINARY_SENSOR, SENSOR, SWITCH]

_LOGGER = logging.getLogger(__name__)


class PersonLocationFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Person Location config flow handler."""

    from .const import (
        CONF_MINOR_VERSION as MINOR_VERSION,
    )
    from .const import (
        CONF_VERSION as VERSION,
    )

    def __init__(self):
        """Initialize config flow."""
        self._errors = {}  # error messages for the data entry flow
        self._user_input = {}  # validated user_input to be saved

    # ------------------------------------------------------------------

    async def async_step_user(self, user_input=None):
        """Handle config flow initiated by the user."""

        self._load_previous_integration_config_data()

        if user_input is not None:
            valid1 = await self._test_google_api_key(user_input[CONF_GOOGLE_API_KEY])
            valid4 = await self._test_mapbox_api_key(user_input[CONF_MAPBOX_API_KEY])
            valid2 = await self._test_mapquest_api_key(
                user_input[CONF_MAPQUEST_API_KEY]
            )
            valid3 = await self._test_osm_api_key(user_input[CONF_OSM_API_KEY])
            valid5 = await self._test_radar_api_key(user_input[CONF_RADAR_API_KEY])
            if valid1 and valid2 and valid3 and valid4 and valid5:
                self._user_input.update(user_input)
                return await self.async_step_sensors()

            return await self._async_show_config_geocode_form(user_input)

        user_input = {}
        user_input[CONF_GOOGLE_API_KEY] = self.integration_config_data.get(
            CONF_GOOGLE_API_KEY, DEFAULT_API_KEY_NOT_SET
        )
        user_input[CONF_LANGUAGE] = self.integration_config_data.get(
            CONF_LANGUAGE, DEFAULT_LANGUAGE
        )
        user_input[CONF_MAPBOX_API_KEY] = self.integration_config_data.get(
            CONF_MAPBOX_API_KEY, DEFAULT_API_KEY_NOT_SET
        )
        user_input[CONF_MAPQUEST_API_KEY] = self.integration_config_data.get(
            CONF_MAPQUEST_API_KEY, DEFAULT_API_KEY_NOT_SET
        )
        user_input[CONF_OSM_API_KEY] = self.integration_config_data.get(
            CONF_OSM_API_KEY, DEFAULT_API_KEY_NOT_SET
        )
        user_input[CONF_RADAR_API_KEY] = self.integration_config_data.get(
            CONF_RADAR_API_KEY, DEFAULT_API_KEY_NOT_SET
        )
        user_input[CONF_REGION] = self.integration_config_data.get(
            CONF_REGION, DEFAULT_REGION
        )

        return await self._async_show_config_geocode_form(user_input)

    def _load_previous_integration_config_data(self):
        """Get current config entry data and options; get current hass domain data."""

        our_currently_configured_entries = self._async_current_entries()
        if our_currently_configured_entries:
            for our_current_entry in our_currently_configured_entries:
                self.config_entry = our_current_entry
                self.config_entry_data = {**our_current_entry.data}
                self.config_entry_options = {**our_current_entry.options}
        else:
            self.config_entry = None
            self.config_entry_data = {}
            self.config_entry_options = {}
        _LOGGER.debug("config_entry_data = %s", self.config_entry_data)
        _LOGGER.debug("config_entry_options = %s", self.config_entry_options)

        try:
            self.integration_config_data
        except AttributeError:
            if DOMAIN in self.hass.data:
                self.integration_config_data = self.hass.data[DOMAIN][
                    DATA_CONFIGURATION
                ]
            else:
                self.integration_config_data = {}
        _LOGGER.debug("integration_config_data = %s", self.integration_config_data)

    async def _async_save__integration_config_data(self):
        config_title = "Person Locations"

        our_current_entry_configured = False
        our_currently_configured_entries = self._async_current_entries()
        if our_currently_configured_entries:
            for our_current_entry in our_currently_configured_entries:
                if our_current_entry.title == config_title:
                    our_current_entry_configured = True
                    break
        if our_current_entry_configured:
            new_config_data = {**our_current_entry.data}
            new_config_data.update(self._user_input)
            changed = self.hass.config_entries.async_update_entry(
                our_current_entry, data=new_config_data
            )
            if changed:
                # TODO: Figure out how to exit the flow gracefully:
                # return self.async_create_entry(title="Ignored", data={})
                # return self.async_create_entry(title="", data={})
                # return self.async_create_entry(title="", data=None)
                return self.async_abort(reason="normal exit")
            self._errors["base"] = "nothing_was_changed"
            return await self.async_step_user()
        return self.async_create_entry(title=config_title, data=self._user_input)

    async def _async_show_config_geocode_form(self, user_input):  # pylint: disable=unused-argument
        """Show the configuration form for reverse geocoding."""

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LANGUAGE, default=user_input[CONF_LANGUAGE]): str,
                    vol.Optional(CONF_REGION, default=user_input[CONF_REGION]): str,
                    vol.Optional(
                        CONF_GOOGLE_API_KEY, default=user_input[CONF_GOOGLE_API_KEY]
                    ): str,
                    vol.Optional(
                        CONF_MAPBOX_API_KEY,
                        default=user_input[CONF_MAPBOX_API_KEY],
                    ): str,
                    vol.Optional(
                        CONF_MAPQUEST_API_KEY,
                        default=user_input[CONF_MAPQUEST_API_KEY],
                    ): str,
                    vol.Optional(
                        CONF_OSM_API_KEY, default=user_input[CONF_OSM_API_KEY]
                    ): str,
                    vol.Optional(
                        CONF_RADAR_API_KEY, default=user_input[CONF_RADAR_API_KEY]
                    ): str,
                }
            ),
            errors=self._errors,
            last_step=False,
        )

    # ------------------------------------------------------------------

    async def async_step_sensors(self, user_input=None):
        """Step to collect which sensors to create."""

        _LOGGER.debug("async_step_sensors user_input = %s", user_input)
        self._errors = {}

        if user_input is not None:
            valid = True
            if user_input[CONF_CREATE_SENSORS] == "":
                create_sensors_list = []
            else:
                if type(user_input[CONF_CREATE_SENSORS] == str):
                    create_sensors_list = [
                        x.strip() for x in user_input[CONF_CREATE_SENSORS].split(",")
                    ]
                else:
                    create_sensors_list = user_input[CONF_CREATE_SENSORS]
                _LOGGER.debug("create_sensors_list before = %s", create_sensors_list)
                create_sensors_list = sorted(list(set(create_sensors_list)))
                _LOGGER.debug("create_sensors_list after  = %s", create_sensors_list)
                for sensor_name in create_sensors_list:
                    if sensor_name not in VALID_CREATE_SENSORS:
                        _LOGGER.debug(
                            "Configured %s: %s is not valid",
                            CONF_CREATE_SENSORS,
                            sensor_name,
                        )
                        self._errors[CONF_CREATE_SENSORS] = "sensor_invalid"
                        valid = False
            if valid:
                self._user_input[CONF_OUTPUT_PLATFORM] = user_input[
                    CONF_OUTPUT_PLATFORM
                ]
                self._user_input[CONF_CREATE_SENSORS] = create_sensors_list

                return await self.async_step_triggers()

            return await self._show_config_sensors_form(user_input)

        # user_input is None, initialize for first display of form:

        user_input = {}
        create_sensors_list = self.integration_config_data.get(CONF_CREATE_SENSORS, "")
        _LOGGER.debug("create_sensors_list = %s", create_sensors_list)
        user_input[CONF_CREATE_SENSORS] = ",".join(create_sensors_list)

        user_input[CONF_OUTPUT_PLATFORM] = self.integration_config_data.get(
            CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM
        )

        return await self._show_config_sensors_form(user_input)

    async def _show_config_sensors_form(self, user_input):  # pylint: disable=unused-argument
        """Show the configuration form for sensors."""

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CREATE_SENSORS, default=user_input[CONF_CREATE_SENSORS]
                    ): str,
                    vol.Optional(
                        CONF_OUTPUT_PLATFORM, default=user_input[CONF_OUTPUT_PLATFORM]
                    ): vol.In(VALID_OUTPUT_PLATFORM),
                }
            ),
            errors=self._errors,
            last_step=False,
        )

    # ------------------------------------------------------------------

    async def async_step_triggers(self, user_input=None):
        """Step to collect which triggers to listen for."""

        self._errors = {}

        if user_input is not None:
            _LOGGER.debug("user_input = %s", user_input)
            valid = True

            if valid:
                self._user_input.update(user_input)

                # ----------------------------------------------------------------------
                return await self._async_save__integration_config_data()
                # ----------------------------------------------------------------------

            return await self._show_config_triggers_form(user_input)

        user_input = {}
        user_input[CONF_FOLLOW_PERSON_INTEGRATION] = self.integration_config_data.get(
            CONF_FOLLOW_PERSON_INTEGRATION, False
        )

        return await self._show_config_triggers_form(user_input)

    async def _show_config_triggers_form(self, user_input):  # pylint: disable=unused-argument
        """Show the configuration form for triggers."""

        return self.async_show_form(
            step_id="triggers",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_FOLLOW_PERSON_INTEGRATION,
                        default=user_input[CONF_FOLLOW_PERSON_INTEGRATION],
                    ): bool,
                }
            ),
            errors=self._errors,
        )

    # ------------------------------------------------------------------

    async def _test_google_api_key(self, google_api_key):
        """Return true if api_key is valid."""

        try:
            if google_api_key == DEFAULT_API_KEY_NOT_SET:
                return True
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            google_url = (
                "https://maps.googleapis.com/maps/api/geocode/json?language="
                + "en"
                + "&region="
                + "us"
                + "&latlng="
                + str(latitude)
                + ","
                + str(longitude)
                + "&key="
                + google_api_key
            )
            session = async_create_clientsession(self.hass)
            client = PersonLocation_aiohttp_Client(session)
            google_decoded = await client.async_get_data("get", google_url)
            if "error" in google_decoded:
                _LOGGER.debug("google_api_key test error = %s", google_decoded["error"])
            else:
                if "status" in google_decoded:
                    google_status = google_decoded["status"]
                    _LOGGER.debug("google_api_key test status = %s", google_status)
                    if google_status == "OK":
                        return True
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.debug(
                "google_api_key test exception %s: %s", type(e).__name__, str(e)
            )
        self._errors[CONF_GOOGLE_API_KEY] = "invalid_key"
        return False

    async def _test_mapbox_api_key(self, mapbox_api_key):
        """Return true if api_key is valid."""

        try:
            if mapbox_api_key == DEFAULT_API_KEY_NOT_SET:
                return True
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = (
                "https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/"
                + str(longitude)
                + ","
                + str(latitude)
                + ",5,0/300x200?access_token="
                + mapbox_api_key
            )

            response = None
            try:
                async_client = get_async_client(self.hass)
                response = await async_client.get(url, timeout=GET_IMAGE_TIMEOUT)
                response.raise_for_status()
                # image = response.content
                _LOGGER.debug("Success testing Mapbox API Access Token")
                return True
            except httpx.TimeoutException:
                _LOGGER.error("Timeout testing Mapbox API Access Token")
                self._errors[CONF_MAPBOX_API_KEY] = "invalid_key"
                return False
            except (httpx.RequestError, httpx.HTTPStatusError) as err:
                _LOGGER.error("Error testing Mapbox API Access Token: %s", err)
                self._errors[CONF_MAPBOX_API_KEY] = "invalid_key"
                return False
            finally:
                if response:
                    await response.aclose()

        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Exception testing mapbox_api_key %s: %s", type(e).__name__, str(e)
            )
        self._errors[CONF_MAPBOX_API_KEY] = "invalid_key"
        return False

    async def _test_mapquest_api_key(self, mapquest_api_key):
        """Return true if api_key is valid."""

        try:
            if mapquest_api_key == DEFAULT_API_KEY_NOT_SET:
                return True
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            mapquest_url = (
                "https://www.mapquestapi.com/geocoding/v1/reverse"
                + "?location="
                + str(latitude)
                + ","
                + str(longitude)
                + "&thumbMaps=false"
                + "&key="
                + mapquest_api_key
            )

            session = async_create_clientsession(self.hass)
            client = PersonLocation_aiohttp_Client(session)
            mapquest_decoded = await client.async_get_data("get", mapquest_url)
            if "error" in mapquest_decoded:
                _LOGGER.debug(
                    "mapquest_api_key test error = %s", mapquest_decoded["error"]
                )
            else:
                mapquest_statuscode = mapquest_decoded["info"]["statuscode"]
                _LOGGER.debug(
                    "mapquest_api_key test statuscode = %s", str(mapquest_statuscode)
                )
                if mapquest_statuscode == 0:
                    return True
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.debug(
                "mapquest_api_key test exception %s: %s", type(e).__name__, str(e)
            )
        self._errors[CONF_MAPQUEST_API_KEY] = "invalid_key"
        return False

    async def _test_osm_api_key(self, osm_api_key):
        """Return true if api_key is valid."""

        try:
            if osm_api_key == DEFAULT_API_KEY_NOT_SET:
                return True
            regex = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$"
            valid = re.search(regex, osm_api_key)
            _LOGGER.debug("osm_api_key test valid = %s", valid)
            if valid:
                return True
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.debug("osm_api_key test exception %s: %s", type(e).__name__, str(e))

        self._errors[CONF_OSM_API_KEY] = "invalid_email"
        return False

    async def _test_radar_api_key(self, radar_api_key):
        """Return true if api_key is valid."""

        try:
            if radar_api_key == DEFAULT_API_KEY_NOT_SET:
                return True
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = (
                "https://api.radar.io/v1/geocode/reverse?coordinates="
                + str(latitude)
                + ","
                + str(longitude)
            )
            headers = {
                'Authorization': radar_api_key,
                'Content-Type': 'application/json'
            }


            response = None
            try:
                async_client = get_async_client(self.hass)
                response = await async_client.get(url, timeout=GET_IMAGE_TIMEOUT, headers=headers)
                response.raise_for_status()
                # image = response.content
                _LOGGER.debug("Success testing Radar API Access Token")
                return True
            except httpx.TimeoutException:
                _LOGGER.error("Timeout testing Radar API Access Token")
                self._errors[CONF_RADAR_API_KEY] = "invalid_key"
                return False
            except (httpx.RequestError, httpx.HTTPStatusError) as err:
                _LOGGER.error("Error testing Radar API Access Token: %s", err)
                self._errors[CONF_RADAR_API_KEY] = "invalid_key"
                return False
            finally:
                if response:
                    await response.aclose()

        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.debug(
                "Exception testing radar_api_key %s: %s", type(e).__name__, str(e)
            )
        self._errors[CONF_RADAR_API_KEY] = "invalid_key"
        return False

    # ------------------------------------------------------------------

    async def async_step_reconfigure(self, user_input=None):
        return await self.async_step_user(user_input)

    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PersonLocationOptionsFlowHandler()


class PersonLocationOptionsFlowHandler(config_entries.OptionsFlow):
    """Person Location options flow handler."""

    def __init__(self):
        """Initialize options flow."""

        self._errors = {}  # error messages for the data entry flow

        self._user_input = {}  # validated user_input to be saved
        self.config_entry_data = {}
        self.config_entry_options = {}


    async def async_step_init(self, user_input=None):
        """Handle option flow initiated by the user."""

        if not self.config_entry_data:
            self.config_entry_data = {**self.config_entry.data}
            self.config_entry_options = {**self.config_entry.options}

        if user_input is not None:
            self.config_entry_options.update(user_input)
            self._user_input.update(user_input)
            return await self.async_step_triggers()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_HOURS_EXTENDED_AWAY,
                        default=self.config_entry_options.get(
                            CONF_HOURS_EXTENDED_AWAY, DEFAULT_HOURS_EXTENDED_AWAY
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_ARRIVED,
                        default=self.config_entry_options.get(
                            CONF_MINUTES_JUST_ARRIVED, DEFAULT_MINUTES_JUST_ARRIVED
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_LEFT,
                        default=self.config_entry_options.get(
                            CONF_MINUTES_JUST_LEFT, DEFAULT_MINUTES_JUST_LEFT
                        ),
                    ): int,
                    vol.Optional(
                        CONF_SHOW_ZONE_WHEN_AWAY,
                        default=self.config_entry_options.get(
                            CONF_SHOW_ZONE_WHEN_AWAY, DEFAULT_SHOW_ZONE_WHEN_AWAY
                        ),
                    ): cv.boolean,
                    vol.Optional(
                        CONF_FRIENDLY_NAME_TEMPLATE,
                        default=self.config_entry_options.get(
                            CONF_FRIENDLY_NAME_TEMPLATE, DEFAULT_FRIENDLY_NAME_TEMPLATE
                        ),
                    ): str,
                }
            ),
            errors=self._errors,
            last_step=False,
        )

    async def _update_options(self):
        """Update config entry options."""

        _LOGGER.debug("===== _update_options options = %s", self.config_entry_options)
        config_title = "Person Locations"
        return self.async_create_entry(
            title=config_title, data=self.config_entry_options
        )

    async def async_step_triggers(self, user_input=None):
        """Handle the option flow."""

        self._errors = {}  # error messages for the data entry flow

        if user_input is not None:
            redisplay = False
            # remove entity from self._all_devices if not in user_input[CONF_DEVICES]
            updated_all_devices = {}
            for device in self._all_devices.keys():
                if device in user_input[CONF_DEVICES]:
                    updated_all_devices[device] = self._all_devices[device]
                else:
                    redisplay = True
            _LOGGER.debug("self._all_devices = %s", updated_all_devices)
            self._all_devices = updated_all_devices

            valid = True
            # add any new entity to self._all_devices
            new_device = user_input[CONF_NEW_DEVICE]
            if (new_device != "") or (user_input[CONF_NEW_NAME] != ""):
                if new_device != "":
                    _LOGGER.debug("new_device  = %s", new_device)
                    new_device_state = self.hass.states.get(new_device)
                    if new_device_state is None:
                        self._errors[CONF_NEW_DEVICE] = "new_device_not_found"
                        valid = False
                    else:
                        entity_domain = new_device.split(".")[0]
                        if entity_domain not in VALID_ENTITY_DOMAINS:
                            _LOGGER.debug(
                                "new_device_state.state = %s", new_device_state.state
                            )
                            self._errors[CONF_NEW_DEVICE] = "new_device_wrong_domain"
                            valid = False
                if user_input[CONF_NEW_DEVICE] == "":
                    self._errors[CONF_NEW_DEVICE] = "device_and_name_required"
                    valid = False
                if user_input[CONF_NEW_NAME] == "":
                    self._errors[CONF_NEW_NAME] = "device_and_name_required"
                    valid = False
                if valid:  # valid at this point is for CONF_NEW_DEVICE/CONF_NEW_NAME
                    self._all_devices[new_device] = (
                        new_device + " = " + user_input[CONF_NEW_NAME]
                    )
                    _LOGGER.debug("_all_devices = %s", self._all_devices)
                    user_input[CONF_NEW_DEVICE] = ""
                    user_input[CONF_NEW_NAME] = ""
                    redisplay = True  # return to the form to add more

            if valid and not redisplay:
                changed = False
                # make new dictionary for CONF_DEVICES
                _LOGGER.debug("self._all_devices = %s", self._all_devices)
                updated_conf_devices = {}
                for device in self._all_devices.keys():
                    person_name = self._all_devices[device].split(" = ")[1]
                    updated_conf_devices[device] = person_name
                # save CONF_DEVICES to configuration
                if updated_conf_devices != self.integration_config_data[CONF_DEVICES]:
                    _LOGGER.debug("updated_conf_devices = %s", updated_conf_devices)
                    # config_title = self.hass.config.config_title
                    # our_currently_configured_entries = self._async_current_entries()
                    # if our_currently_configured_entries:
                    #     for our_current_entry in our_currently_configured_entries:
                    #         if our_current_entry.title == config_title:
                    self.config_entry_data.update({CONF_DEVICES: updated_conf_devices})
                    our_current_entry = self.config_entry
                    changed = self.hass.config_entries.async_update_entry(
                        our_current_entry, data=self.config_entry_data
                    )
                    _LOGGER.debug("updated CONF_DEVICES saved (changed=%s)", changed)
                    # break

                if changed or self.config_entry_options != dict(
                    self.config_entry.options
                ):
                    return await self._update_options()
                else:
                    self._errors["base"] = "nothing_was_changed"
                    return await self.async_step_init()

        else:
            self.integration_config_data = self.hass.data[DOMAIN][DATA_CONFIGURATION]

            user_input = {}
            user_input[CONF_NEW_DEVICE] = ""
            user_input[CONF_NEW_NAME] = ""

            _LOGGER.debug(
                "self.integration_config_data[CONF_DEVICES] = %s",
                self.integration_config_data[CONF_DEVICES],
            )
            self._all_devices = {}
            for device in self.integration_config_data[CONF_DEVICES].keys():
                self._all_devices[device] = (
                    device + " = " + self.integration_config_data[CONF_DEVICES][device]
                )
            _LOGGER.debug("_all_devices = %s", self._all_devices)

        return self.async_show_form(
            step_id="triggers",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICES, default=list(self._all_devices.keys())
                    ): cv.multi_select(self._all_devices),
                    vol.Optional(
                        CONF_NEW_DEVICE,
                        default=user_input[CONF_NEW_DEVICE],
                        # ): cv.entities_domain(VALID_ENTITY_DOMAINS),
                    ): str,
                    vol.Optional(
                        CONF_NEW_NAME,
                        default=user_input[CONF_NEW_NAME],
                    ): str,
                },
            ),
            errors=self._errors,
        )

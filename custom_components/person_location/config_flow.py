"""Config flow for Person Location integration."""

import logging
import re
#import httpx
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import selector

from .api import PersonLocation_aiohttp_Client
from .const import (
    DOMAIN,
    DATA_CONFIGURATION,
    # Data (structural)
    CONF_GOOGLE_API_KEY,
    CONF_MAPBOX_API_KEY,
    CONF_MAPQUEST_API_KEY,
    CONF_OSM_API_KEY,
    CONF_RADAR_API_KEY,
    CONF_REGION,
    CONF_LANGUAGE,
    CONF_OUTPUT_PLATFORM,
    CONF_CREATE_SENSORS,
    CONF_DEVICES,
    CONF_PROVIDERS,
        CONF_NAME,
        CONF_STATE,
        CONF_STILL_IMAGE_URL,
    CONF_FOLLOW_PERSON_INTEGRATION,
    DEFAULT_API_KEY_NOT_SET,
    DEFAULT_REGION,
    DEFAULT_LANGUAGE,
    DEFAULT_OUTPUT_PLATFORM,
    VALID_CREATE_SENSORS,
    VALID_OUTPUT_PLATFORM,
    # Options (behavioral)
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
    CONF_FRIENDLY_NAME_TEMPLATE,
    DEFAULT_HOURS_EXTENDED_AWAY,
    DEFAULT_MINUTES_JUST_ARRIVED,
    DEFAULT_MINUTES_JUST_LEFT,
    DEFAULT_SHOW_ZONE_WHEN_AWAY,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
)

CONF_NEW_DEVICE = "new_device_entity"
CONF_NEW_PERSON_NAME = "new_person_name"

_LOGGER = logging.getLogger(__name__)
GET_IMAGE_TIMEOUT = 10


# ============================================================
# ConfigFlow — handles DATA (structural configuration)
# ============================================================

class PersonLocationFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial config flow for Person Location."""

    # ----------------- Entry Points from Home Assistant -----------------

    def __init__(self):
        self._errors = {}
        self._user_input = {}
        self.integration_config_data = {}
        self._provider_to_edit = None
        self._device_to_edit = None
        self._source_create = None      # Create new entry at end of flow?

        self._last_step = None  # Tracks last completed step
        self._step_order = ["geocode", "sensors", "triggers", "providers", "done"]

    async def async_step_user(self, user_input=None):
        """Handle first configuration or when Add Service is clicked."""
        _LOGGER.debug("[async_step_user] user_input = %s", user_input)

        # Load the existing entry’s data if it exists _user_input so edits persist
        self._load_previous_integration_config_data()

        return await self.async_step_menu(user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfigure initiated from the three‑dot menu."""
        _LOGGER.debug("[async_step_reconfigure] user_input = %s", user_input)

        # Load the existing entry’s data into _user_input so edits persist
        self._load_previous_integration_config_data()
        self._user_input.update(self.config_entry_data)

        # Jump straight to the menu so the user can pick what to edit
        return await self.async_step_menu(user_input)

    # ----------------- Menu for Configuration Steps -----------------

    async def async_step_menu(self, user_input=None):
        """Initial menu for ConfigFlow."""
        _LOGGER.debug("[async_step_menu] user_input = %s", user_input)

        if not self.config_entry_data:
            self.config_entry_data = {**self.config_entry.data}
            self.config_entry_options = {**self.config_entry.options}

        if user_input is not None:
            choice = user_input.get("menu_selection")
            self._last_step = choice  # Track what was just selected

            if choice == "geocode":
                return await self.async_step_geocode()
            if choice == "sensors":
                return await self.async_step_sensors()
            if choice == "triggers":
                return await self.async_step_triggers()
            if choice == "providers":
                return await self.async_step_providers()
        #    if choice == "triggers":
        #        return await self.async_step_triggers()
            if choice == "done":
                return await self._async_save__integration_config_data()
            
        default_choice = "geocode"
        if self._last_step in self._step_order:
            idx = self._step_order.index(self._last_step)
            if idx + 1 < len(self._step_order):
                default_choice = self._step_order[idx + 1]

        return self.async_show_form(
            step_id="menu",
            data_schema=vol.Schema(
                {
                    vol.Required("menu_selection", default=default_choice): vol.In({
                        "geocode": "Geocode API keys, region, language",
                        "sensors": "Sensors to be created",
                        "triggers": "Manage triggers/devices",
                        "providers": "Manage map camera providers",
                        "done": "Save and exit configuration setup",
                    })
                }
            )
        )
    
    # ----------------- Geocode: API keys, region, language -----------------

    async def async_step_geocode(self, user_input=None):
        """Step: Collect API keys, region, language."""

        if user_input is not None:
            valid1 = await self._test_google_api_key(user_input[CONF_GOOGLE_API_KEY])
            valid2 = await self._test_mapquest_api_key(user_input[CONF_MAPQUEST_API_KEY])
            valid3 = await self._test_osm_api_key(user_input[CONF_OSM_API_KEY])
            valid4 = await self._test_mapbox_api_key(user_input[CONF_MAPBOX_API_KEY])
            valid5 = await self._test_radar_api_key(user_input[CONF_RADAR_API_KEY])

            if all([valid1, valid2, valid3, valid4, valid5]):
                self._user_input.update(user_input)
                return await self.async_step_menu()
            return await self._async_show_config_geocode_form(user_input)

        user_input = {
            CONF_GOOGLE_API_KEY: self.integration_config_data.get(CONF_GOOGLE_API_KEY, DEFAULT_API_KEY_NOT_SET),
            CONF_LANGUAGE: self.integration_config_data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
            CONF_MAPBOX_API_KEY: self.integration_config_data.get(CONF_MAPBOX_API_KEY, DEFAULT_API_KEY_NOT_SET),
            CONF_MAPQUEST_API_KEY: self.integration_config_data.get(CONF_MAPQUEST_API_KEY, DEFAULT_API_KEY_NOT_SET),
            CONF_OSM_API_KEY: self.integration_config_data.get(CONF_OSM_API_KEY, DEFAULT_API_KEY_NOT_SET),
            CONF_RADAR_API_KEY: self.integration_config_data.get(CONF_RADAR_API_KEY, DEFAULT_API_KEY_NOT_SET),
            CONF_REGION: self.integration_config_data.get(CONF_REGION, DEFAULT_REGION),
        }
        return await self._async_show_config_geocode_form(user_input)

    async def async_step_sensors(self, user_input=None):
        """Step: Collect sensor creation and output platform."""
        if user_input is not None:
            create_sensors_list = []
            if user_input[CONF_CREATE_SENSORS]:
                if isinstance(user_input[CONF_CREATE_SENSORS], str):
                    create_sensors_list = [x.strip() for x in user_input[CONF_CREATE_SENSORS].split(",")]
                else:
                    create_sensors_list = user_input[CONF_CREATE_SENSORS]
                create_sensors_list = sorted(set(create_sensors_list))
                for sensor_name in create_sensors_list:
                    if sensor_name not in VALID_CREATE_SENSORS:
                        self._errors[CONF_CREATE_SENSORS] = "sensor_invalid"
                        return await self._show_config_sensors_form(user_input)

            self._user_input[CONF_OUTPUT_PLATFORM] = user_input[CONF_OUTPUT_PLATFORM]
            self._user_input[CONF_CREATE_SENSORS] = create_sensors_list
            return await self.async_step_menu()

        create_sensors_list = self.integration_config_data.get(CONF_CREATE_SENSORS, [])
        user_input = {
            CONF_CREATE_SENSORS: ",".join(create_sensors_list),
            CONF_OUTPUT_PLATFORM: self.integration_config_data.get(CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM),
        }
        return await self._show_config_sensors_form(user_input)

    # ----------------- Triggers: Manage triggers/devices -----------------

    async def async_step_triggers(self, user_input=None):
        """Manage triggers list: pairs of device entities/person names."""
        _LOGGER.debug("[async_step_triggers] user_input = %s", user_input)

        self._errors = {}

        return_to_menu = "__return__"
        return_to_menu_choice = "🔙 Return to menu"

        # Note: devices = dict of {entity_id: person_name}
        devices = self._user_input.get(CONF_DEVICES, self.integration_config_data.get(CONF_DEVICES, []))
        _LOGGER.debug("[async_step_triggers] devices = %s", devices)

        if user_input is None:

            self._valid_device_entities = [return_to_menu_choice,""]
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("device_tracker")))
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("binary_sensor")))
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("person")))

            user_input = {
                CONF_FOLLOW_PERSON_INTEGRATION: self.integration_config_data.get(CONF_FOLLOW_PERSON_INTEGRATION, False),
                CONF_NEW_DEVICE: "",
                CONF_NEW_PERSON_NAME: ""
            }

        else:

            soft_return = False

            # Add new trigger:

            new_device = user_input.get(CONF_NEW_DEVICE, "").strip()
            new_person = user_input.get(CONF_NEW_PERSON_NAME, "").strip()
            
            if CONF_NEW_DEVICE in user_input and new_device and new_device != return_to_menu_choice:
                if new_device in devices.keys():
                    self._errors[CONF_NEW_DEVICE] = "duplicate_device"
                elif not new_person:
                    self._errors[CONF_NEW_PERSON_NAME] = "missing_person"
                else:
                    # Add the Trigger and return to triggers list
                    devices[new_device] = new_person
                    self._user_input[CONF_DEVICES] = devices
                    user_input[CONF_NEW_DEVICE] = ""
                    user_input[CONF_NEW_PERSON_NAME] = ""
                    return await self.async_step_triggers() 
            else:
                if new_person:
                    self._errors[CONF_NEW_DEVICE] = "missing_device"
                else:
                    # Both empty, soft return
                    soft_return = True

            # Choose Trigger to update or remove:

            choice = user_input.get("device_choice")
            if choice == return_to_menu:
                soft_return = True
            else:
                self._device_to_edit = choice
                return await self.async_step_trigger_edit()

            # Return to the menu if we are done:

            if soft_return and not self._errors:
                return await self.async_step_menu()                           

        existing_names = {}
        for device in devices.keys():
            existing_names[device] = (
                device + " = " + devices[device]
            )
        _LOGGER.debug("[async_step_triggers] existing_names = %s", existing_names)

        choices = {
            **existing_names,
            return_to_menu: return_to_menu_choice,
        }

        return self.async_show_form(
            step_id="triggers",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_FOLLOW_PERSON_INTEGRATION,
                    default=user_input[CONF_FOLLOW_PERSON_INTEGRATION]
                ): bool,
                vol.Optional(
                    CONF_NEW_DEVICE,
                    default=user_input[CONF_NEW_DEVICE],
                ): vol.In(self._valid_device_entities),
                vol.Optional(
                    CONF_NEW_PERSON_NAME,
                    default=user_input[CONF_NEW_PERSON_NAME],
                ): str,
                vol.Required("device_choice", default=return_to_menu): vol.In(choices),
            }),
            description_placeholders={
                "existing": ", ".join(existing_names) if existing_names else "None"
            },
            errors=self._errors,
        )

    async def async_step_trigger_edit(self, user_input=None):
        """Edit an existing device (update/remove)."""
        _LOGGER.debug("[async_step_trigger_edit] user_input = %s", user_input)

        # Note: devices = dict of {entity_id: person_name}
        devices = self._user_input.get(CONF_DEVICES, self.integration_config_data.get(CONF_DEVICES, []))
        device = self._device_to_edit
        if device not in devices:
            return await self.async_step_triggers()

        updateLabel = "Update"
        removeLabel = "Remove"

        if user_input is None:
            user_input = {
                CONF_NEW_DEVICE: device,
                CONF_NEW_PERSON_NAME: devices[device]
            }

        else:

            new_device_name = user_input.get(CONF_NEW_DEVICE, "").strip()
            new_person_name = user_input.get(CONF_NEW_PERSON_NAME, "").strip()

            action = user_input.get("edit_action")
            if action == removeLabel:
                old_device_entry = devices.pop(device)
                _LOGGER.debug("[async_step_trigger_edit] devices after remove = %s", devices)
            elif action == updateLabel:
                old_device_entry = devices.pop(device)
                devices[new_device_name] = new_person_name
                _LOGGER.debug("[async_step_trigger_edit] devices after update = %s", devices)
            self._user_input[CONF_DEVICES] = devices
            return await self.async_step_triggers()

        return self.async_show_form(
            step_id="trigger_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "edit_action",
                        default=updateLabel
                    ): vol.In([updateLabel, removeLabel]),
                    vol.Optional(
                        CONF_NEW_DEVICE,
                        default=user_input[CONF_NEW_DEVICE],
                    ): vol.In(self._valid_device_entities),
                    vol.Optional(
                        CONF_NEW_PERSON_NAME,
                        default=user_input[CONF_NEW_PERSON_NAME],
                    ): str,
                }
            ),
            description_placeholders={"device": self._device_to_edit},
            errors=self._errors,
        )

    # ----------------- Providers: Manage map cameras -----------------

    async def async_step_providers(self, user_input=None):
        """Step: Manage providers list (structural)."""
        _LOGGER.debug("[async_step_providers] user_input = %s", user_input)

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))
        existing_names = {p["name"]: p["name"] for p in providers}
        _LOGGER.debug("[async_step_providers] providers = %s", providers)
        _LOGGER.debug("[async_step_providers] existing_names = %s", existing_names)

        if not existing_names:
            return await self.async_step_provider_add()

        if user_input is not None:
            choice = user_input.get("provider_choice")
            if choice == "__return__":
                return await self.async_step_menu()
            elif choice == "__add__":
                return await self.async_step_provider_add()
            else:
                self._provider_to_edit = choice
                return await self.async_step_provider_edit()

        choices = {
            **existing_names,
            "__add__": "➕ Add new provider",
            "__return__": "🔙 Return to menu",
        }
        return self.async_show_form(
            step_id="providers",
            data_schema=vol.Schema(
                {vol.Required("provider_choice", default="__return__"): vol.In(choices)}
            ),
            description_placeholders={
                "existing": ", ".join(existing_names) if existing_names else "None"
            },
            errors=self._errors,
        )

    async def async_step_provider_add(self, user_input=None):
        """Add a new provider."""
        _LOGGER.debug("[async_step_provider_add] user_input = %s", user_input)

        self._errors = {}

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))

        if user_input is None:
            new_provider_name = ""
            new_provider_state = ""
            new_provider_url = ""
        else:
            new_provider_name = user_input.get(CONF_NAME,"").strip()
            new_provider_state = user_input.get(CONF_STATE,"").strip()
            new_provider_url = user_input.get(CONF_STILL_IMAGE_URL,"").strip()

            _LOGGER.debug("[async_step_provider_add] new_provider_name = %s", new_provider_name)
            _LOGGER.debug("[async_step_provider_add] new_provider_state = %s", new_provider_state)
            _LOGGER.debug("[async_step_provider_add] new_provider_url = %s", new_provider_url)

            if (new_provider_name or new_provider_state or new_provider_url):
                if not new_provider_name:
                    self._errors[CONF_NAME] = "missing_three"
                elif any(p["name"].lower() == new_provider_name.lower() for p in providers):
                    self._errors[CONF_NAME] = "duplicate_name"
                if not new_provider_state:
                    self._errors[CONF_STATE] = "missing_three"
                if not new_provider_url:
                    self._errors[CONF_STILL_IMAGE_URL] = "missing_three"
                if not self._errors:
                    new_provider = {
                        "name": user_input[CONF_NAME],
                        CONF_STATE: user_input[CONF_STATE],
                        CONF_STILL_IMAGE_URL: user_input[CONF_STILL_IMAGE_URL],
                    }
                    providers.append(new_provider)
                    self._user_input[CONF_PROVIDERS] = providers
                    return await self.async_step_providers()
            else:
                return await self.async_step_providers()

        return self.async_show_form(
            step_id="provider_add",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME,default=new_provider_name): str,
                    vol.Optional(CONF_STATE,default=new_provider_state): str,
                    vol.Optional(CONF_STILL_IMAGE_URL,default=new_provider_url): str,
                }
            ),
            errors=self._errors,
        )

    async def async_step_provider_edit(self, user_input=None):
        """Edit an existing map provider (update/remove)."""
        _LOGGER.debug("[async_step_provider_edit] user_input = %s", user_input)

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))
        provider = next((p for p in providers if p["name"] == self._provider_to_edit), None)
        if not provider:
            return await self.async_step_providers()

        updateLabel = "Update"
        removeLabel = "Remove"

        if user_input is None:
            user_input = {
                CONF_STATE: provider.get(CONF_STATE, ""),
                CONF_STILL_IMAGE_URL: provider.get(CONF_STILL_IMAGE_URL, ""),
            }

        else:
            new_state = user_input.get(CONF_STATE, "").strip()
            new_still_image_url = user_input.get(CONF_STILL_IMAGE_URL, "").strip()

            action = user_input.get("edit_action")
            if action == removeLabel:
                providers.remove(provider)
            elif action == updateLabel:
                provider[CONF_STATE] = user_input[CONF_STATE]
                provider[CONF_STILL_IMAGE_URL] = user_input[CONF_STILL_IMAGE_URL]

            self._user_input[CONF_PROVIDERS] = providers
            return await self.async_step_providers()

        return self.async_show_form(
            step_id="provider_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "edit_action",
                        default=updateLabel
                    ): vol.In([updateLabel, removeLabel]),
                    vol.Optional(
                        CONF_STATE,
                        default=provider.get(CONF_STATE, "")
                    ): str,
                    vol.Optional(
                        CONF_STILL_IMAGE_URL,
                        default=provider.get(CONF_STILL_IMAGE_URL, "")
                    ): str,
                }
            ),
            description_placeholders={"provider": self._provider_to_edit},
            errors=self._errors,
        )

    # ----------------- Helpers -----------------

    def _load_previous_integration_config_data(self):
        """Load any existing config entry data/options into memory."""
        entries = self._async_current_entries()
        if entries:
            _LOGGER.debug(
                "[_load_previous_integration_config_data] loading config entry, " \
                "self.source = %s",
                self.source
            )
            self._source_create = False
            self.config_entry = entries[0]
            self.config_entry_data = {**self.config_entry.data}
            self.config_entry_options = {**self.config_entry.options}
        else:
            _LOGGER.debug(
                "[_load_previous_integration_config_data] empty configuration, " \
                "self.source = %s",
                 self.source
            )
            self._source_create = True
            self.config_entry = None
            self.config_entry_data = {}
            self.config_entry_options = {}
        self.integration_config_data = self.hass.data.get(DOMAIN, {}).get(DATA_CONFIGURATION, {})

    async def _async_save__integration_config_data(self):
        """Save collected user_input into the config entry.

        - On first install: create a new entry
        - On reconfigure: update the existing entry
        """
        if not self._source_create and self.config_entry:
            _LOGGER.debug(
                "[_async_save__integration_config_data] updating existing entry, " \
                "self.source = %s",
                self.source
            )
            # Update the existing entry in place
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=self._user_input,
            )
            # Reload so changes take effect immediately
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_abort(reason="Configuration successfully saved.")
        else:
            _LOGGER.debug(
                "[_async_save__integration_config_data] creating entry, " \
                "self.source = %s",
                self.source
            )
            # First-time setup
            return self.async_create_entry(title="Person Location Config", data=self._user_input)

    async def _async_show_config_geocode_form(self, user_input):
        """Show the initial form for API keys and geocoding settings."""
        return self.async_show_form(
            step_id="geocode",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LANGUAGE, default=user_input[CONF_LANGUAGE]): str,
                    vol.Optional(CONF_REGION, default=user_input[CONF_REGION]): str,
                    vol.Optional(CONF_GOOGLE_API_KEY, default=user_input[CONF_GOOGLE_API_KEY]): str,
                    vol.Optional(CONF_MAPBOX_API_KEY, default=user_input[CONF_MAPBOX_API_KEY]): str,
                    vol.Optional(CONF_MAPQUEST_API_KEY, default=user_input[CONF_MAPQUEST_API_KEY]): str,
                    vol.Optional(CONF_OSM_API_KEY, default=user_input[CONF_OSM_API_KEY]): str,
                    vol.Optional(CONF_RADAR_API_KEY, default=user_input[CONF_RADAR_API_KEY]): str,
                }
            ),
            errors=self._errors,
        )

    async def _show_config_sensors_form(self, user_input):
        """Show the form for sensor creation and output platform."""
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CREATE_SENSORS, default=user_input[CONF_CREATE_SENSORS]): str,
                    vol.Optional(CONF_OUTPUT_PLATFORM, default=user_input[CONF_OUTPUT_PLATFORM]): vol.In(VALID_OUTPUT_PLATFORM),
                }
            ),
            errors=self._errors,
        )

    # ----------------- API Key Tests -----------------

    async def _test_google_api_key(self, key):
        if key == DEFAULT_API_KEY_NOT_SET:
            return True
        try:
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={latitude},{longitude}&key={key}"
            session = async_create_clientsession(self.hass)
            client = PersonLocation_aiohttp_Client(session)
            resp = await client.async_get_data("get", url)
            return resp.get("status") == "OK"
        except Exception as e:
            _LOGGER.debug("Google API key test failed: %s", e)
        self._errors[CONF_GOOGLE_API_KEY] = "invalid_key"
        return False

    async def _test_mapbox_api_key(self, key):
        if key == DEFAULT_API_KEY_NOT_SET:
            return True
        try:
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = f"https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/{longitude},{latitude},5,0/300x200?access_token={key}"
            async_client = get_async_client(self.hass)
            response = await async_client.get(url, timeout=GET_IMAGE_TIMEOUT)
            response.raise_for_status()
            await response.aclose()
            return True
        except Exception as e:
            _LOGGER.debug("Mapbox API key test failed: %s", e)
        self._errors[CONF_MAPBOX_API_KEY] = "invalid_key"
        return False

    async def _test_mapquest_api_key(self, key):
        if key == DEFAULT_API_KEY_NOT_SET:
            return True
        try:
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = f"https://www.mapquestapi.com/geocoding/v1/reverse?location={latitude},{longitude}&thumbMaps=false&key={key}"
            session = async_create_clientsession(self.hass)
            client = PersonLocation_aiohttp_Client(session)
            resp = await client.async_get_data("get", url)
            return resp.get("info", {}).get("statuscode") == 0
        except Exception as e:
            _LOGGER.debug("MapQuest API key test failed: %s", e)
        self._errors[CONF_MAPQUEST_API_KEY] = "invalid_key"
        return False

    async def _test_osm_api_key(self, key):
        if key == DEFAULT_API_KEY_NOT_SET:
            return True
        try:
            regex = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$"
            if re.search(regex, key):
                return True
        except Exception as e:
            _LOGGER.debug("OSM API key test failed: %s", e)
        self._errors[CONF_OSM_API_KEY] = "invalid_email"
        return False

    async def _test_radar_api_key(self, key):
        if key == DEFAULT_API_KEY_NOT_SET:
            return True
        try:
            latitude = self.hass.config.latitude
            longitude = self.hass.config.longitude
            url = f"https://api.radar.io/v1/geocode/reverse?coordinates={latitude},{longitude}"
            headers = {"Authorization": key, "Content-Type": "application/json"}
            async_client = get_async_client(self.hass)
            response = await async_client.get(url, timeout=GET_IMAGE_TIMEOUT, headers=headers)
            response.raise_for_status()
            await response.aclose()
            return True
        except Exception as e:
            _LOGGER.debug("Radar API key test failed: %s", e)
        self._errors[CONF_RADAR_API_KEY] = "invalid_key"
        return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PersonLocationOptionsFlowHandler(config_entry)


# ============================================================
# OptionsFlow — handles OPTIONS (runtime behavior)
# ============================================================

class PersonLocationOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Person Location."""

    # ----------------- Entry Points from Home Assistant -----------------

    def __init__(self, config_entry: ConfigEntry):
        self.config_entry = config_entry
        self._errors = {}

    async def async_step_init(self, user_input=None):
        """Entry point for options flow."""
        _LOGGER.debug("[async_step_init] user_input = %s", user_input)
        
        return await self.async_step_general()
    
    # ----------------- General Options -----------------

    async def async_step_general(self, user_input=None):
        """General runtime options (thresholds, templates, UX toggles)."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_HOURS_EXTENDED_AWAY,
                        default=self.config_entry.options.get(
                            CONF_HOURS_EXTENDED_AWAY, DEFAULT_HOURS_EXTENDED_AWAY
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_ARRIVED,
                        default=self.config_entry.options.get(
                            CONF_MINUTES_JUST_ARRIVED, DEFAULT_MINUTES_JUST_ARRIVED
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_LEFT,
                        default=self.config_entry.options.get(
                            CONF_MINUTES_JUST_LEFT, DEFAULT_MINUTES_JUST_LEFT
                        ),
                    ): int,
                    vol.Optional(
                        CONF_SHOW_ZONE_WHEN_AWAY,
                        default=self.config_entry.options.get(
                            CONF_SHOW_ZONE_WHEN_AWAY, DEFAULT_SHOW_ZONE_WHEN_AWAY
                        ),
                    ): cv.boolean,
                    vol.Optional(
                        CONF_FRIENDLY_NAME_TEMPLATE,
                        default=self.config_entry.options.get(
                            CONF_FRIENDLY_NAME_TEMPLATE, DEFAULT_FRIENDLY_NAME_TEMPLATE
                        ),
                    ): str,
                }
            ),
            errors=self._errors,
        )
    
 
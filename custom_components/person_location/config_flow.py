"""Config flow for Person Location integration."""

import copy
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client
#from homeassistant.helpers.selector import selector
from homeassistant.helpers import selector
from homeassistant.helpers.template import Template as HATemplate
from urllib.parse import urlparse

from .helpers.api import PersonLocation_aiohttp_Client
from .helpers.template import normalize_template
from .helpers.template import validate_template

from typing import Any, Dict, Optional

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
    TITLE_IMPORTED_YAML_CONFIG,
    TITLE_PERSON_LOCATION_CONFIG,
    CONFIG_SCHEMA,
    ALLOWED_OPTIONS_KEYS,
)

CONF_NEW_DEVICE = "new_device_entity"
CONF_NEW_PERSON_NAME = "new_person_name"

_LOGGER = logging.getLogger(__name__)
GET_IMAGE_TIMEOUT = 10

def _split_conf_data_and_options(conf: dict) -> tuple[dict, dict]:
    _LOGGER.debug("[_split_conf_data_and_options] conf: %s", conf)
    return (
        {k: v for k, v in conf.items() if k not in ALLOWED_OPTIONS_KEYS},
        {k: v for k, v in conf.items() if k in ALLOWED_OPTIONS_KEYS},
    )

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
        """Handle reconfigure initiated from the three-dot menu."""
        _LOGGER.debug("[async_step_reconfigure] user_input = %s", user_input)

        # Load the existing entry’s data into _user_input so edits persist
        self._load_previous_integration_config_data()
        self._user_input.update(self.config_entry_data)

        # Jump straight to the menu so the user can pick what to edit
        return await self.async_step_menu(user_input)

    async def async_step_import(self, conf: dict):
        """Handle configuration from __init__.py async_setup:

            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=conf,
            )
        """
        _LOGGER.debug("[async_step_import] conf = %s", conf)

        title = TITLE_IMPORTED_YAML_CONFIG

        # Check for duplicates by title
        for entry in self._async_current_entries():
            if entry.title == title:
                _LOGGER.debug("[async_step_import] Skipping duplicate with matching title: %s", title)
                return self.async_abort(reason="already_configured")

        conf_data, conf_options = _split_conf_data_and_options(conf)

        # Create config entry
        _LOGGER.debug("[async_step_import] Creating entry with title: %s", title)
        return self.async_create_entry(
            title=title, 
            data=conf_data,
            options=conf_options,
        )
    
    # ----------------- Menu for Configuration Steps -----------------

    async def async_step_menu(self, user_input=None):
        """Initial menu for ConfigFlow."""
        _LOGGER.debug("[async_step_menu] user_input = %s", user_input)

        if not self.config_entry_data:
            if self.config_entry:
                self.config_entry_data = {**self.config_entry.data}
                self.config_entry_options = {**self.config_entry.options}
            else:
                self.config_entry_data = {}
                self.config_entry_options = {}

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
            if choice == "done":
                return await self._async_save_integration_config_data()
            
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
        _LOGGER.debug("[async_step_geocode] user_input = %s", user_input)

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

    # ----------------- Sensors to be created -----------------

    async def async_step_sensors(self, user_input=None):
        """Step: Collect sensor creation and output platform, with cleanup support."""
        _LOGGER.debug("[async_step_sensors] user_input = %s", user_input)

        if user_input is not None:
            # ┌───────────────────────────────────────────────┐
            # │ 1. Collect toggles: each sensor is a bool     │
            # │    True → include, False → exclude            │
            # └───────────────────────────────────────────────┘
            create_sensors_list = [
                sensor for sensor in VALID_CREATE_SENSORS if user_input.get(sensor)
            ]

            # ┌───────────────────────────────────────────────┐
            # │ 2. Persist values (even if empty list)        │
            # │    Ensures clearing sensors is saved          │
            # └───────────────────────────────────────────────┘
            self._user_input[CONF_CREATE_SENSORS] = create_sensors_list
            self._user_input[CONF_OUTPUT_PLATFORM] = user_input.get(
                CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM
            )

            # ┌───────────────────────────────────────────────┐
            # │ 3. Cleanup orphaned *template* entities       │
            # │    Only prune sensors ending in "_template"   │
            # └───────────────────────────────────────────────┘

            # TODO: look at doing this in _async_save_integration_config_data when 
            # committed to making the update? Only remove for the ones that were
            # removed from the create_sensors list?

            if not create_sensors_list:
                registry = er.async_get(self.hass)
                for entity_id, entry in list(registry.entities.items()):
                    if (
                        entry.platform == DOMAIN
                        and entry.unique_id.endswith("_template")
                    ):
                        _LOGGER.debug("Removing orphaned template sensor entity: %s", entity_id)
                        registry.async_remove(entity_id)

            return await self.async_step_menu()

        # ┌───────────────────────────────────────────────┐
        # │ Initial form population                       │
        # │ Prefer self._user_input if present            │
        # │ Fall back to integration_config_data only     │
        # │ on very first render                          │
        # └───────────────────────────────────────────────┘
        if CONF_CREATE_SENSORS in self._user_input:
            existing_sensors = set(self._user_input[CONF_CREATE_SENSORS])
        else:
            existing_sensors = set(self.integration_config_data.get(CONF_CREATE_SENSORS, []))

        user_input = {
            CONF_OUTPUT_PLATFORM: self._user_input.get(
                CONF_OUTPUT_PLATFORM,
                self.integration_config_data.get(CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM),
            ),
            **{sensor: sensor in existing_sensors for sensor in VALID_CREATE_SENSORS},
        }
        return await self._show_config_sensors_form(user_input)


    async def _show_config_sensors_form(self, user_input):
        """Show the form for sensor creation and output platform."""
        # ┌───────────────────────────────────────────────┐
        # │ Form schema                                   │
        # │ - Each sensor: toggle (bool)                  │
        # │ - Platform: optional, with safe default       │
        # └───────────────────────────────────────────────┘
        data_schema = {
            #vol.Optional(
            #    CONF_OUTPUT_PLATFORM,
            #    default=user_input.get(CONF_OUTPUT_PLATFORM, DEFAULT_OUTPUT_PLATFORM),
            #): vol.In(VALID_OUTPUT_PLATFORM),
        }

        # Add a boolean toggle for each valid sensor
        for sensor in VALID_CREATE_SENSORS:
            data_schema[vol.Optional(sensor, default=user_input.get(sensor, False))] = bool

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(data_schema),
            errors=self._errors,
        )

    # ----------------- Triggers: Manage triggers/devices -----------------

    async def async_step_triggers(self, user_input=None):
        """Manage triggers list: pairs of device entities/person names."""
        _LOGGER.debug("[async_step_triggers] user_input = %s", user_input)

        self._errors = {}

        skip_add_device_choice = "— None to be added —"
        return_to_menu = "__return__"
        return_to_menu_choice = "🔙 Return to menu"

        devices = self._user_input.get(CONF_DEVICES, self.integration_config_data.get(CONF_DEVICES, {}))
        if not devices:
            devices = {}
        _LOGGER.debug("[async_step_triggers] devices = %s", devices)

        if user_input is None:
            self._valid_device_entities = [skip_add_device_choice]
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("device_tracker")))
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("binary_sensor")))
            self._valid_device_entities.extend(sorted(self.hass.states.async_entity_ids("person")))
            self._valid_device_entities.append("")

            user_input = {
                CONF_FOLLOW_PERSON_INTEGRATION: self._user_input.get(
                    CONF_FOLLOW_PERSON_INTEGRATION,
                    self.integration_config_data.get(CONF_FOLLOW_PERSON_INTEGRATION, False)
                ),
                CONF_NEW_DEVICE: skip_add_device_choice,
                CONF_NEW_PERSON_NAME: ""
            }

        else:
            # Persist follow_person_integration setting
            if CONF_FOLLOW_PERSON_INTEGRATION in user_input:
                self._user_input[CONF_FOLLOW_PERSON_INTEGRATION] = user_input[CONF_FOLLOW_PERSON_INTEGRATION]
            else:
                user_input[CONF_FOLLOW_PERSON_INTEGRATION] = self._user_input.get(CONF_FOLLOW_PERSON_INTEGRATION, False)

            soft_return = False

            new_device = user_input.get(CONF_NEW_DEVICE, "").strip()
            new_person = user_input.get(CONF_NEW_PERSON_NAME, "").strip()

            if CONF_NEW_DEVICE in user_input and new_device and new_device != skip_add_device_choice:
                if new_device in devices.keys():
                    self._errors[CONF_NEW_DEVICE] = "duplicate_device"
                elif not new_person:
                    self._errors[CONF_NEW_PERSON_NAME] = "missing_person"
                else:
                    devices[new_device] = new_person
                    self._user_input[CONF_DEVICES] = devices
                    user_input[CONF_NEW_DEVICE] = ""
                    user_input[CONF_NEW_PERSON_NAME] = ""
                    return await self.async_step_triggers()
            else:
                if new_person:
                    self._errors[CONF_NEW_DEVICE] = "missing_device"
                else:
                    soft_return = True

            choice = user_input.get("device_choice")
            if choice == return_to_menu:
                soft_return = True
            else:
                self._device_to_edit = choice
                return await self.async_step_trigger_edit()

            if soft_return and not self._errors:
                return await self.async_step_menu()

        existing_names = {
            device: f"{device} = {devices[device]}"
            for device in devices.keys()
        }
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
                    default=self._user_input.get(CONF_FOLLOW_PERSON_INTEGRATION, False)
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
                "existing": ", ".join(existing_names.values()) if existing_names else "None"
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
        removeLabel = "❌ Remove"

        if user_input is None:
            user_input = {
                CONF_NEW_DEVICE: device,
                CONF_NEW_PERSON_NAME: devices[device]
            }
            # A non-existent device could come from YAML...
            self._valid_device_entities_plus_device_to_edit = []
            self._valid_device_entities_plus_device_to_edit.extend(self._valid_device_entities)
            self._valid_device_entities_plus_device_to_edit.append(self._device_to_edit)

        else:

            new_device_name = user_input.get(CONF_NEW_DEVICE, "").strip()
            new_person_name = user_input.get(CONF_NEW_PERSON_NAME, "").strip()

            action = user_input.get("edit_action")
            if action == removeLabel:
                old_device_entry = devices.pop(device)
                _LOGGER.debug("[async_step_trigger_edit] devices after remove = %s", devices)
            elif action == updateLabel:
                x = devices.pop(device)
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
                    ): vol.In(self._valid_device_entities_plus_device_to_edit),
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

        cfg = self.hass.data[DOMAIN][DATA_CONFIGURATION]
        self._camera_template_variables = {
            "parse_result": False,      # TODO: Investigate whether parse_result belongs here.
            "google_api_key": cfg[CONF_GOOGLE_API_KEY],
            "mapbox_api_key": cfg[CONF_MAPBOX_API_KEY],
            "mapquest_api_key": cfg[CONF_MAPQUEST_API_KEY],
            "osm_api_key": cfg[CONF_OSM_API_KEY],
            "radar_api_key": cfg[CONF_RADAR_API_KEY],
        }

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
        # from homeassistant.helpers import selector

        _LOGGER.debug("[async_step_provider_add] user_input = %s", user_input)

        errors = {}
        placeholders = {}

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))

        if user_input is None:
            user_input = {
                CONF_NAME: "",
                CONF_STATE: "",
                CONF_STILL_IMAGE_URL: "",
            }
        else:
            new_provider_name = user_input.get(CONF_NAME,"").strip()

            raw_provider_state = user_input.get(CONF_STATE, "")
            new_provider_state = normalize_template(raw_provider_state)
            
            raw_provider_url = user_input.get(CONF_STILL_IMAGE_URL, "")
            new_provider_url = normalize_template(raw_provider_url)

            if (new_provider_name or new_provider_state or new_provider_url):
                if not new_provider_name:
                    errors[CONF_NAME] = "missing_three"
                elif any(p["name"].lower() == new_provider_name.lower() for p in providers):
                    errors[CONF_NAME] = "duplicate_name"
                if not new_provider_state:
                    errors[CONF_STATE] = "missing_three"
                if not new_provider_url:
                    errors[CONF_STILL_IMAGE_URL] = "missing_three"
                
                if not errors:
                    # Validate 'state' template
                    v1 = await validate_template(
                        self.hass,
                        new_provider_state,
                        self._camera_template_variables,
                        expected="text",
                    )
                    if not v1["ok"]:
                        errors[CONF_STATE] = "invalid_state_template"
                        placeholders["state_error"] = v1["error"]
                    elif v1["missing_entities"]:
                        # Not fatal, but useful feedback
                        placeholders["state_missing"] = ", ".join(v1["missing_entities"])

                    # Validate 'still_image_url' template as a URL
                    v2 = await validate_template(
                        self.hass,
                        new_provider_url,
                        self._camera_template_variables,
                        expected="url",
                    )
                    if not v2["ok"]:
                        errors[CONF_STILL_IMAGE_URL] = "invalid_url_template"
                        placeholders["url_error"] = v2["error"]

                    if not errors:
                    
                        self._provider_to_edit = new_provider_name
                        new_provider = {
                            CONF_NAME: user_input[CONF_NAME],
                            CONF_STATE: user_input[CONF_STATE],
                            CONF_STILL_IMAGE_URL: user_input[CONF_STILL_IMAGE_URL],
                        }
                        providers.append(new_provider)
                        self._user_input[CONF_PROVIDERS] = providers
                        return await self.async_step_providers()
            else:
                # nothing added
                if providers:
                    # go back and choose a provider action
                    return await self.async_step_providers()
                else:
                    # nothing to see until one is added
                    return await self.async_step_menu()

        _LOGGER.debug(
            "[async_step_provider_add] errors=%s, placeholders=%s",
            errors,
            placeholders,
        )

        return self.async_show_form(
            step_id="provider_add",
            data_schema=vol.Schema({
                vol.Optional(CONF_NAME,
                    default=user_input.get(CONF_NAME, "")
                ): str,
                vol.Optional(CONF_STATE,
                    default=user_input.get(CONF_STATE, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional(CONF_STILL_IMAGE_URL,
                    default=user_input.get(CONF_STILL_IMAGE_URL, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_provider_edit(self, user_input=None):
        """Edit an existing map provider (update/remove)."""
        # from homeassistant.helpers import selector
        
        _LOGGER.debug("[async_step_provider_edit] user_input = %s", user_input)

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))
        provider = next((p for p in providers if p["name"] == self._provider_to_edit), None)
        if not provider:
            return await self.async_step_providers()

        errors = {}
        placeholders = {"provider": self._provider_to_edit}

        updateLabel = "Update and preview result"
        removeLabel = "❌ Remove"

        if user_input is None:
            user_input = {
                CONF_STATE: provider.get(CONF_STATE, ""),
                CONF_STILL_IMAGE_URL: provider.get(CONF_STILL_IMAGE_URL, ""),
            }
            action = updateLabel

        else:
            raw_state = user_input.get(CONF_STATE, "")
            new_state = normalize_template(raw_state)

            raw_still_image_url = user_input.get(CONF_STILL_IMAGE_URL, "")
            new_still_image_url = normalize_template(raw_still_image_url)

            action = user_input.get("edit_action")
            if action == removeLabel:
                providers.remove(provider)
            elif action == updateLabel:

                # Validate 'state' template
                v1 = await validate_template(self.hass,new_state,self._camera_template_variables, expected="text")
                if not v1["ok"]:
                    errors[CONF_STATE] = "invalid_state_template"
                    placeholders["state_error"] = v1["error"]
                elif v1["missing_entities"]:
                    # Not fatal, but useful feedback
                    placeholders["state_missing"] = ", ".join(v1["missing_entities"])

                # Validate 'still_image_url' template as a URL
                v2 = await validate_template(self.hass,new_still_image_url,self._camera_template_variables, expected="url")
                if not v2["ok"]:
                    errors[CONF_STILL_IMAGE_URL] = "invalid_url_template"
                    placeholders["url_error"] = v2["error"]
                elif v2["missing_entities"]:
                    # Not fatal, but useful feedback
                    placeholders["url_missing"] = ", ".join(v2["missing_entities"])

                if not errors:

                    provider[CONF_STATE] = user_input[CONF_STATE]
                    provider[CONF_STILL_IMAGE_URL] = user_input[CONF_STILL_IMAGE_URL]

            if not errors:

                self._user_input[CONF_PROVIDERS] = providers
                return await self.async_step_provider_preview()

        _LOGGER.debug(
            "[async_step_provider_edit] errors=%s, placeholders=%s",
            errors,
            placeholders,
        )

        return self.async_show_form(
            step_id="provider_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "edit_action",
                        default=action
                    ): vol.In([updateLabel, removeLabel]),
                    vol.Optional(
                        CONF_STATE,
                        default=user_input.get(CONF_STATE, "")
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(
                        CONF_STILL_IMAGE_URL,
                        default=user_input.get(CONF_STILL_IMAGE_URL, "")
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_provider_preview(self, user_input=None):
        """Preview an existing map provider."""
        
        _LOGGER.debug("[async_step_provider_prieview] user_input = %s", user_input)

        # note: providers = list of dicts with keys name, state, url
        providers = self._user_input.get(CONF_PROVIDERS, self.integration_config_data.get(CONF_PROVIDERS, []))
        provider = next((p for p in providers if p["name"] == self._provider_to_edit), None)
        if not provider:
            return await self.async_step_providers()

        provider_name = provider.get(CONF_NAME,"")

        raw_state = provider.get(CONF_STATE, "")
        provider_state = normalize_template(raw_state)

        raw_still_image_url = provider.get(CONF_STILL_IMAGE_URL, "")
        provider_url = normalize_template(raw_still_image_url)

        _LOGGER.debug(
            "[async_step_provider_preview] provider_name=%s, provider_state=%s, provider_url=%s",
            provider_name,
            provider_state,
            provider_url,
        )

        errors = {}
        placeholders = {
            "provider_name": self._provider_to_edit,
            "provider_state_preview": "",
            "state_missing_entities": "",
            "provider_url_preview": "",
            "url_missing_entities": "",
        }
        edit_this_provider = "__edit__"
        edit_this_provider_choice = "🖊️ Edit this provider"
        
        add_new_provider = "__add__"
        add_new_provider_choice = "➕ Add new provider"

        return_to_menu = "__return__"
        return_to_menu_choice = "🔙 Return to Map Camera List"
        
        return_to_main_menu = "__main__"
        return_to_main_menu_choice = "🔙 🔙 Return to Configuration Menu"
        
        choices = {
            edit_this_provider: edit_this_provider_choice,
            add_new_provider: add_new_provider_choice,
            return_to_menu: return_to_menu_choice,
            return_to_main_menu: return_to_main_menu_choice,
        }

        # Validate 'state' template
        v1 = await validate_template(self.hass,provider_state,self._camera_template_variables, expected="text")
        if not v1["ok"]:
            errors[CONF_STATE] = "invalid_state_template"
            placeholders["provider_state_preview"] = v1["error"]
        else:
            placeholders["provider_state_preview"] = v1["rendered"]
            if v1["missing_entities"]:
                # Not fatal, but useful feedback
                placeholders["state_missing_entities"] = "⚠ Missing entities: " + ", ".join(v1["missing_entities"])

        # Validate 'still_image_url' template as a URL
        v2 = await validate_template(self.hass,provider_url,self._camera_template_variables, expected="url")
        if not v2["ok"]:
            errors[CONF_STILL_IMAGE_URL] = "invalid_url_template"
            placeholders["provider_url_preview"] = v2["error"]
        else:
            placeholders["provider_url_preview"] = v2["rendered"]
            if v2["missing_entities"]:
                # Not fatal, but useful feedback
                placeholders["url_missing_entities"] = "⚠ Missing entities: " + ", ".join(v2["missing_entities"])

        if user_input is None:
            user_input = {
                CONF_STATE: provider_state,
                CONF_STILL_IMAGE_URL: provider_url,
            }
            action = edit_this_provider
        else:
            if not errors:
                action = user_input.get("next_action")
                if action == edit_this_provider:
                    return await self.async_step_provider_edit()
                if action == add_new_provider:
                    return await self.async_step_provider_add()
                if action == return_to_menu:
                    return await self.async_step_providers()
                if action == return_to_main_menu:
                    return await self.async_step_menu()
            

        _LOGGER.debug(
            "[async_step_provider_preview] user_input=%s, errors=%s, placeholders=%s",
            user_input,
            errors,
            placeholders,
        )

        return self.async_show_form(
            step_id="provider_preview",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "next_action",
                        default=action
                    ): vol.In(choices),
                }
            ),
            description_placeholders=placeholders,
            errors=errors,
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
            self.config_entry_data = copy.deepcopy(dict(self.config_entry.data))
            self.config_entry_options = copy.deepcopy(dict(self.config_entry.options))
        else:
            _LOGGER.debug(
                "[_load_previous_integration_config_data] No previous configuration, " \
                "self.source = %s",
                self.source
            )
            self._source_create = True
            self.config_entry = None

            # get YAML conf defaults
            default_conf = CONFIG_SCHEMA({DOMAIN: {} })[DOMAIN]

            self.config_entry_data, self.config_entry_options = _split_conf_data_and_options(default_conf)

        _LOGGER.debug(
            "[_load_previous_integration_config_data] " \
            "self.config_entry_data = %s",
            self.config_entry_data
        )
        _LOGGER.debug(
            "[_load_previous_integration_config_data] " \
            "self.config_entry_options = %s",
            self.config_entry_options
        )

        if self.hass:
            self.integration_config_data = self.hass.data.get(DOMAIN, {}).get(DATA_CONFIGURATION, {})
        else:
            self.integration_config_data = {}

    async def _async_save_integration_config_data(self):
        """Save collected user_input into the config entry.

        - On first install: create a new entry
        - On reconfigure: update the existing entry
        """
        if not self._source_create and self.config_entry and self.config_entry.title == TITLE_PERSON_LOCATION_CONFIG:
            _LOGGER.debug(
                "[_async_save_integration_config_data] updating existing entry, source=%s, entry_id=%s, data=%s",
                self.source,
                self.config_entry.entry_id,
                self._user_input,
            )
            # Update the existing entry in place
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=self._user_input,
            )

            # Reload so changes take effect immediately
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)

            # End the flow cleanly (no new entry created)
            return self.async_abort(reason="reconfigure_successful")

        else:
            _LOGGER.debug(
                "[_async_save_integration_config_data] creating entry, source=%s, data=%s",
                self.source,
                self._user_input,
            )
            # First-time setup
            return self.async_create_entry(
                title=TITLE_PERSON_LOCATION_CONFIG,
                data=self._user_input,
            )
    
    # ----------------- API Key Tests -----------------

    async def _test_google_api_key(self, key):
        # from .api import PersonLocation_aiohttp_Client
        # from homeassistant.helpers.aiohttp_client import async_create_clientsession

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
        # from homeassistant.helpers.httpx_client import get_async_client

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
        # from .api import PersonLocation_aiohttp_Client
        # from homeassistant.helpers.aiohttp_client import async_create_clientsession

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
        import re

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
        # from homeassistant.helpers.httpx_client import get_async_client

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

# --------------------------- Options Factory ---------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PersonLocationOptionsFlowHandler()

# ============================================================
# OptionsFlow — handles OPTIONS (runtime behavior)
# ============================================================

class PersonLocationOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Person Location."""

    # ----------------- Entry Points from Home Assistant -----------------

    def __init__(self):
        self._errors = {}

    async def async_step_init(self, user_input=None):
        """Entry point for options flow."""
        _LOGGER.debug("[async_step_init] user_input = %s", user_input)
        
        return await self.async_step_general()
    
    # ----------------- General Options -----------------

    async def async_step_general(self, user_input=None):
        """General runtime options (thresholds, templates, UX toggles)."""

        from .helpers.template import test_friendly_name_template

        conf_preview_friendly_name = "_preview_friendly_name"

        errors = {}
        friendly_preview = "Preview after submit?"

        if user_input is None:

            user_input = {
                CONF_HOURS_EXTENDED_AWAY: self.config_entry.options.get(
                    CONF_HOURS_EXTENDED_AWAY,
                    DEFAULT_HOURS_EXTENDED_AWAY
                ),
                CONF_MINUTES_JUST_ARRIVED: self.config_entry.options.get(
                    CONF_MINUTES_JUST_ARRIVED,
                    DEFAULT_MINUTES_JUST_ARRIVED
                ),
                CONF_MINUTES_JUST_LEFT: self.config_entry.options.get(
                    CONF_MINUTES_JUST_LEFT,
                    DEFAULT_MINUTES_JUST_LEFT
                ),
                CONF_SHOW_ZONE_WHEN_AWAY: self.config_entry.options.get(
                    CONF_SHOW_ZONE_WHEN_AWAY,
                    DEFAULT_SHOW_ZONE_WHEN_AWAY
                ),
                CONF_FRIENDLY_NAME_TEMPLATE: self.config_entry.options.get(
                    CONF_FRIENDLY_NAME_TEMPLATE,
                    DEFAULT_FRIENDLY_NAME_TEMPLATE
                ),
                conf_preview_friendly_name: False,
            }

        else:

            # Validate the friendly name template.
            template_str = user_input.get(CONF_FRIENDLY_NAME_TEMPLATE, "")
            if not template_str:
                errors[CONF_FRIENDLY_NAME_TEMPLATE] = "template_required"
            else:
                result = await test_friendly_name_template(
                    self.hass,
                    template_str)
                if result is None:
                    errors[CONF_FRIENDLY_NAME_TEMPLATE] = "template_required"
                elif not result["ok"]:
                    errors[CONF_FRIENDLY_NAME_TEMPLATE] = result["error"]
                else:
                    # Provide a preview of the friendly name template.
                    if user_input.get(conf_preview_friendly_name):
                        if result["ok"]:
                            friendly_preview = "Preview: `" + result["rendered"] + "`"

            if not errors and not user_input[conf_preview_friendly_name]:
                x = user_input.pop(conf_preview_friendly_name)
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_HOURS_EXTENDED_AWAY,
                        default=user_input.get(
                            CONF_HOURS_EXTENDED_AWAY, DEFAULT_HOURS_EXTENDED_AWAY
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_ARRIVED,
                        default=user_input.get(
                            CONF_MINUTES_JUST_ARRIVED, DEFAULT_MINUTES_JUST_ARRIVED
                        ),
                    ): int,
                    vol.Optional(
                        CONF_MINUTES_JUST_LEFT,
                        default=user_input.get(
                            CONF_MINUTES_JUST_LEFT, DEFAULT_MINUTES_JUST_LEFT
                        ),
                    ): int,
                    vol.Optional(
                        CONF_SHOW_ZONE_WHEN_AWAY,
                        default=user_input.get(
                            CONF_SHOW_ZONE_WHEN_AWAY, DEFAULT_SHOW_ZONE_WHEN_AWAY
                        ),
                    ): cv.boolean,
                    vol.Optional(
                        CONF_FRIENDLY_NAME_TEMPLATE,
                        default=user_input.get(
                            CONF_FRIENDLY_NAME_TEMPLATE, DEFAULT_FRIENDLY_NAME_TEMPLATE
                        ),
                    ): str,
                    vol.Optional(
                        conf_preview_friendly_name,
                        default=user_input[conf_preview_friendly_name]
                    ): cv.boolean,
                }
            ),
            description_placeholders={"friendly_preview": friendly_preview},
            errors=errors,
        )

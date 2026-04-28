"""sensor.py - Sensor platform for person_location integration."""

# pyright: reportMissingImports=false
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from . import PersonLocationIntegration


from datetime import datetime, timedelta, timezone
import logging

from homeassistant.components.mobile_app.const import ATTR_VERTICAL_ACCURACY
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.zone import DOMAIN as ZONE_DOMAIN
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNKNOWN,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ALTITUDE,
    ATTR_AWAY_TIMESTAMP,
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_DRIVING_KM,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    ATTR_PERSON_NAME,
    ATTR_REPORTED_STATE,
    ATTR_SOURCE,
    ATTR_ZONE,
    CONF_CREATE_SENSORS,
    CONF_FOLLOW_PERSON_INTEGRATION,
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
    DATA_CONFIGURATION,
    DATA_INTEGRATION,
    DOMAIN,
    IC3_STATIONARY_ZONE_PREFIX,
    INFO_GEOCODE_COUNT,
    INFO_LOCALITY,
    INFO_TRIGGER_COUNT,
    INTEGRATION_NAME,
    STATE_EXTENDED_AWAY,
)
from .helpers.timestamp import now_utc, to_iso
from .trigger import PersonLocationTrigger

TARGET_STATE_DESCRIPTION = SensorEntityDescription(
    key="person_location_target_state",
    translation_key="person_location_target_state",
    name="Target State",
    # device_class="enum",
    # options=[
    #    "home",
    #    "not_home",
    #    "just_arrived",
    #    "just_left",
    #    "extended_away",
    #    "unknown",
    # ],
)

_LOGGER = logging.getLogger(__name__)


# =====================================================================
# Target Sensor — the main sensor representing the person's location
# =====================================================================


class PersonLocationTargetSensor(SensorEntity, RestoreEntity):
    """Main target sensor created by this integration."""

    entity_description = TARGET_STATE_DESCRIPTION

    def __init__(
        self, entity_id: str, pli: PersonLocationIntegration, person_name: str
    ) -> None:
        """Initialize the target sensor."""
        #    self._attr_device_class = TARGET_STATE_DESCRIPTION.device_class
        #    self._attr_options = TARGET_STATE_DESCRIPTION.options
        self._attr_translation_key = TARGET_STATE_DESCRIPTION.key
        self._attr_translate_state = True

        self._entity_id = entity_id
        self._pli = pli

        self._person_name = person_name

        self._attr_unique_id = f"{entity_id}_target"
        self._attr_name = f"{person_name} Location"

        # Internal state storage
        self._state = STATE_UNKNOWN

        # Enable translation of known states
        self._attr_extra_state_attributes = {}

        # Attributes
        self._attr_extra_state_attributes.update(
            {
                ATTR_PERSON_NAME: person_name,
                ATTR_REPORTED_STATE: STATE_UNKNOWN,
                ATTR_SOURCE: STATE_UNKNOWN,
                ATTR_BREAD_CRUMBS: "",
            }
        )

        self.last_changed = None
        self.last_updated = None

        self.this_entity_info = {
            INFO_GEOCODE_COUNT: 0,
            INFO_LOCALITY: "?",
            INFO_TRIGGER_COUNT: 0,
        }

        self._previous_state = STATE_UNKNOWN
        self._initialized = False

        _LOGGER.debug("[PersonLocationTargetSensor] (%s) constructed", entity_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Group target sensor under the person's device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._person_name.lower()}_location")},
            name=f"{self._person_name} Location",
            manufacturer=INTEGRATION_NAME,
            model="Person Tracker",
        )

    @property
    def native_value(self) -> str:
        """Return the state of the sensor (to be translated)."""
        return self._state

    # -----------------------------------------------------------------
    # HA lifecycle
    # -----------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore state after reboot."""
        old = await self.async_get_last_state()

        if old:
            self._state = old.state
            self._attr_extra_state_attributes.update(old.attributes)
            self._previous_state = old.state

            # Remove legacy attribute if present
            obsolete = self._attr_extra_state_attributes.pop("reported_zone", None)
            if obsolete:
                _LOGGER.debug(
                    "[async_added_to_hass] (%s) Removed obsolete 'reported_zone' attribute with value '%s'",
                    self._entity_id,
                    obsolete,
                )

            if self._entity_id not in self._pli._target_sensors_restored:
                self._pli._target_sensors_restored.append(self._entity_id)

            self.last_changed = old.last_changed or now_utc()
            self.last_updated = old.last_updated or now_utc()
        else:
            self._state = "Unknown"
            self.last_changed = now_utc()
            self.last_updated = self.last_changed

        # -----------------------------------------------------------------
        # Schedule delayed state transitions
        # -----------------------------------------------------------------
        match self._state:
            case "home" | "extended_away" | "none":
                # Nothing to schedule
                pass

            case "just_left":
                minutes = self._pli.configuration[CONF_MINUTES_JUST_LEFT]
                if minutes != 0:
                    self.schedule_state_change(
                        from_state=self._state,
                        to_state=STATE_NOT_HOME,
                        minutes=minutes,
                    )

            case "just_arrived":
                minutes = self._pli.configuration[CONF_MINUTES_JUST_ARRIVED]
                if minutes != 0:
                    self.schedule_state_change(
                        from_state=self._state,
                        to_state=STATE_HOME,
                        minutes=minutes,
                    )

            case _:
                # Everything else is a variation of Away
                hours = self._pli.configuration[CONF_HOURS_EXTENDED_AWAY]
                if hours != 0:
                    if ATTR_AWAY_TIMESTAMP in self._attr_extra_state_attributes:
                        away_time_raw = self._attr_extra_state_attributes[
                            ATTR_AWAY_TIMESTAMP
                        ]
                        away_timestamp = dt_util.parse_datetime(away_time_raw)
                        if away_timestamp.tzinfo is None:
                            away_timestamp = away_timestamp.replace(
                                tzinfo=datetime.timezone.utc
                            )
                    else:
                        away_timestamp = self.last_changed
                        self._attr_extra_state_attributes[ATTR_AWAY_TIMESTAMP] = to_iso(
                            away_timestamp
                        )
                    minutes_left = (hours * 60) - (
                        (now_utc() - away_timestamp).total_seconds() / 60
                    )
                    if minutes_left > 0:
                        self.schedule_state_change(
                            from_state=self._state,
                            to_state=STATE_EXTENDED_AWAY,
                            minutes=minutes_left,
                        )
                    else:
                        # Already past the Extended Away threshold, apply immediately
                        self._state = STATE_EXTENDED_AWAY

        await self.async_set_state()
        _LOGGER.debug(
            "[async_added_to_hass] Restored %s with state=%s",
            self._entity_id,
            self._state,
        )

        self._initialized = True

    # -----------------------------------------------------------------
    # Atomic async state write
    # -----------------------------------------------------------------

    async def async_set_state(self) -> None:
        """Write state using HA's entity system (required for enum translation)."""
        if not self._initialized:
            _LOGGER.debug(
                "[async_set_state] called before initialization for %s",
                self._entity_id,
            )
            return

        now = now_utc()
        self.last_updated = now

        if self._previous_state != self._state:
            self.last_changed = now
            self._previous_state = self._state

        # Let HA handle translation, device_class, options, etc.
        self.async_write_ha_state()

        _LOGGER.debug(
            "[async_set_state] (%s) value=%s attrs=%s",
            self._entity_id,
            self._state,
            self._attr_extra_state_attributes,
        )

        def set_state(self: PersonLocationTargetSensor) -> None:
            """Legacy sync wrapper (kept for compatibility)."""
            if self.hass:
                self.hass.loop.call_soon_threadsafe(
                    lambda: self.hass.async_create_task(self.async_set_state())
                )

    # -----------------------------------------------------------------
    # Delayed state transitions (Just Arrived → Home, Just Left → Away, Away → Extended Away)
    # -----------------------------------------------------------------

    def schedule_state_change(
        self,
        *,
        from_state: str,
        to_state: str,
        minutes: int,
    ) -> None:
        """Schedule a delayed state transition for this target entity."""
        _LOGGER.debug(
            "[schedule_state_change] (%s) === Start === from_state=%s; to_state=%s; minutes=%d",
            self.entity_id,
            from_state,
            to_state,
            minutes,
        )

        # Cancel any previous timer for this entity
        self.cancel_state_change_timer()

        point_in_time = now_utc() + timedelta(minutes=minutes)

        async def _cb(now: datetime) -> None:
            await self._handle_delayed_state_change(
                now=now,
                from_state=from_state,
                to_state=to_state,
                minutes=minutes,
            )

        remove_function = async_track_point_in_time(self.hass, _cb, point_in_time)
        if remove_function:
            self._undo_state_change_timer = remove_function
            _LOGGER.debug(
                "[schedule_state_change] (%s) === Scheduled ===",
                self.entity_id,
            )
        else:
            _LOGGER.error(
                "[schedule_state_change] (%s) === Failed === Could not schedule state change",
                self.entity_id,
            )

    def cancel_state_change_timer(self) -> None:
        """Cancel any pending delayed state transition for this target entity."""
        undo = getattr(self, "_undo_state_change_timer", None)
        if undo:
            undo()
            self._undo_state_change_timer = None
            _LOGGER.debug(
                "[cancel_state_change_timer] (%s) === Cancelled === Pending timer cancelled",
                self.entity_id,
            )

    async def _handle_delayed_state_change(
        self,
        *,
        now: datetime,
        from_state: str,
        to_state: str,
        minutes: int,
    ) -> bool:
        """Handle the delayed state change for this target entity."""
        _LOGGER.debug(
            "[_handle_delayed_state_change] (%s) === Start === %s from_state=%s; to_state=%s",
            self.entity_id,
            now,
            from_state,
            to_state,
        )

        # Precursor state check
        if self._state != from_state:
            _LOGGER.debug(
                "[_handle_delayed_state_change] (%s) Skip: state %s is no longer %s",
                self.entity_id,
                self._state,
                from_state,
            )
            return True

        # Apply the new state
        self._state = to_state

        # -----------------------------
        # State-specific side effects
        # -----------------------------
        if to_state == STATE_HOME:
            self._attr_extra_state_attributes[ATTR_BREAD_CRUMBS] = "Home"
            self._attr_extra_state_attributes[ATTR_DIRECTION] = "home"
            self._attr_extra_state_attributes[ATTR_COMPASS_BEARING] = 0
            self._attr_extra_state_attributes.pop(ATTR_AWAY_TIMESTAMP, None)
        elif to_state == STATE_NOT_HOME:
            # Optional: show zone name instead of plain "Away"
            if self._pli.configuration.get(CONF_SHOW_ZONE_WHEN_AWAY, False):
                reported_zone = self._attr_extra_state_attributes.get(ATTR_ZONE)
                zone_state = None
                if reported_zone:
                    zone_state = self.hass.states.get(f"{ZONE_DOMAIN}.{reported_zone}")

                if zone_state is not None and not reported_zone.startswith(
                    IC3_STATIONARY_ZONE_PREFIX
                ):
                    zone_attrs = zone_state.attributes.copy()
                    friendly = zone_attrs.get("friendly_name")
                    if friendly:
                        self._state = friendly
                else:
                    _LOGGER.debug(
                        "[_handle_delayed_state_change] (%s) Skipping zone %s for Away",
                        self.entity_id,
                        reported_zone,
                    )

            # Schedule Extended Away if configured
            hours_ext = self._pli.configuration.get(CONF_HOURS_EXTENDED_AWAY, 0)
            if hours_ext:
                self.schedule_state_change(
                    from_state=self._state,
                    to_state=STATE_EXTENDED_AWAY,
                    minutes=hours_ext * 60,
                )

        # Persist the new state
        await self.async_set_state()

        _LOGGER.debug(
            "[_handle_delayed_state_change] (%s) === Return ===", self.entity_id
        )
        return True

    # -----------------------------------------------------------------
    # Template sensor creation
    # -----------------------------------------------------------------

    def make_template_sensor(
        self, attributeName: str, supplementalAttributeArray: list
    ) -> None:
        """Create a template sensor for a specific attribute."""
        _LOGGER.debug("[make_template_sensor] === Start === %s", attributeName)

        if isinstance(attributeName, str):
            if attributeName in self._attr_extra_state_attributes:
                templateSuffix = attributeName
                templateState = self._attr_extra_state_attributes[attributeName]
            else:
                return
        else:
            for templateSuffix in attributeName:
                templateState = attributeName[templateSuffix]

        templateAttributes = {}
        for supplementalAttribute in supplementalAttributeArray:
            if isinstance(supplementalAttribute, str):
                if supplementalAttribute in self._attr_extra_state_attributes:
                    templateAttributes[supplementalAttribute] = (
                        self._attr_extra_state_attributes[supplementalAttribute]
                    )
            else:
                for k in supplementalAttribute:
                    templateAttributes[k] = supplementalAttribute[k]

        templateAttributes[ATTR_PERSON_NAME] = self._person_name

        target = get_target_entity(self._pli, self.entity_id)
        create_and_register_template_sensor(
            self._pli.hass, target, attributeName, templateState, templateAttributes
        )

        _LOGGER.debug("[make_template_sensor] === Return === %s", attributeName)

    def make_template_sensors(self) -> None:
        """Create all requested template sensors."""
        create_sensors_list = (
            self._pli.configuration[CONF_CREATE_SENSORS]
            or self._pli.hass.data[DOMAIN][DATA_CONFIGURATION][CONF_CREATE_SENSORS]
        )

        _LOGGER.debug(
            "[make_template_sensors] === Start === configuration = %s",
            create_sensors_list,
        )

        for attributeName in create_sensors_list:
            if (
                attributeName == ATTR_ALTITUDE
                and ATTR_ALTITUDE in self._attr_extra_state_attributes
                and self._attr_extra_state_attributes[ATTR_ALTITUDE] != 0
                and ATTR_VERTICAL_ACCURACY in self._attr_extra_state_attributes
                and self._attr_extra_state_attributes[ATTR_VERTICAL_ACCURACY] != 0
            ):
                self.make_template_sensor(
                    ATTR_ALTITUDE,
                    [
                        ATTR_VERTICAL_ACCURACY,
                        ATTR_ICON,
                        {ATTR_UNIT_OF_MEASUREMENT: "m"},
                    ],
                )

            elif attributeName == ATTR_BREAD_CRUMBS:
                self.make_template_sensor(ATTR_BREAD_CRUMBS, [ATTR_ICON])

            elif attributeName == ATTR_DIRECTION:
                self.make_template_sensor(ATTR_DIRECTION, [ATTR_ICON])

            elif attributeName == ATTR_DRIVING_KM:
                self.make_template_sensor(
                    ATTR_DRIVING_KM,
                    [
                        ATTR_DRIVING_MINUTES,
                        ATTR_METERS_FROM_HOME,
                        ATTR_MILES_FROM_HOME,
                        {ATTR_UNIT_OF_MEASUREMENT: "km"},
                        ATTR_ICON,
                    ],
                )

            elif attributeName == ATTR_DRIVING_MILES:
                self.make_template_sensor(
                    ATTR_DRIVING_MILES,
                    [
                        ATTR_DRIVING_MINUTES,
                        ATTR_METERS_FROM_HOME,
                        ATTR_MILES_FROM_HOME,
                        {ATTR_UNIT_OF_MEASUREMENT: "mi"},
                        ATTR_ICON,
                    ],
                )

            elif attributeName == ATTR_DRIVING_MINUTES:
                self.make_template_sensor(
                    ATTR_DRIVING_MINUTES,
                    [
                        ATTR_DRIVING_MILES,
                        ATTR_METERS_FROM_HOME,
                        ATTR_MILES_FROM_HOME,
                        {ATTR_UNIT_OF_MEASUREMENT: "min"},
                        ATTR_ICON,
                    ],
                )

            elif attributeName == ATTR_LATITUDE:
                self.make_template_sensor(ATTR_LATITUDE, [ATTR_GPS_ACCURACY, ATTR_ICON])

            elif attributeName == ATTR_LONGITUDE:
                self.make_template_sensor(
                    ATTR_LONGITUDE, [ATTR_GPS_ACCURACY, ATTR_ICON]
                )

            elif attributeName == ATTR_METERS_FROM_HOME:
                self.make_template_sensor(
                    ATTR_METERS_FROM_HOME,
                    [
                        ATTR_MILES_FROM_HOME,
                        ATTR_DRIVING_MILES,
                        ATTR_DRIVING_MINUTES,
                        {ATTR_UNIT_OF_MEASUREMENT: "m"},
                        ATTR_ICON,
                    ],
                )

            elif attributeName == ATTR_MILES_FROM_HOME:
                self.make_template_sensor(
                    ATTR_MILES_FROM_HOME,
                    [
                        ATTR_METERS_FROM_HOME,
                        ATTR_DRIVING_MILES,
                        ATTR_DRIVING_MINUTES,
                        {ATTR_UNIT_OF_MEASUREMENT: "mi"},
                        ATTR_ICON,
                    ],
                )

            else:
                self.make_template_sensor(attributeName, [ATTR_ICON])

        _LOGGER.debug("[make_template_sensors] === Return ===")


# =====================================================================
# Template Sensor - Represents the state of a target's attributes
# =====================================================================


class PersonLocationTemplateSensor(SensorEntity):
    """Template sensor (altitude, speed, etc.) tied to a person."""

    def __init__(
        self, parent: PersonLocationTargetSensor, suffix: str, value: str, attrs: dict
    ) -> None:
        """Initialize the template sensor."""
        base_id = parent._entity_id
        self._entity_id = f"{base_id}_{suffix}"
        self._parent = parent
        self._suffix = suffix

        self._attr_unique_id = f"{base_id}_{suffix}_template"
        self._attr_has_entity_name = True
        self._attr_name = suffix.replace("_", " ").title()
        self._state = value
        self._attr_extra_state_attributes = {}
        self._attr_extra_state_attributes.update(attrs)

        person_name = getattr(self._parent, "_person_name", None)
        if not person_name:
            person_name = base_id.split(".")[1].split("_")[0].title()
            _LOGGER.debug(
                "[PersonLocationTemplateSensor] Using fallback person name: %s",
                person_name,
            )
        self._attr_extra_state_attributes[ATTR_PERSON_NAME] = person_name

    @property
    def device_info(self) -> DeviceInfo:
        """Group template sensor under the person's target."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._parent._person_name.lower()}_location")},
            name=f"{self._parent._person_name} Location",
            manufacturer=INTEGRATION_NAME,
            model="Person Tracker",
        )

    @property
    def native_value(self) -> str:
        """Return the state of the sensor (to be translated)."""
        return self._state


# =====================================================================
# Setup Entry
# =====================================================================


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: callable
) -> None:
    """Set up person_location sensors from a config entry."""
    _LOGGER.debug("[async_setup_entry] entry: %s", entry.entry_id)

    # Expose sensor platform's async_add_entities so __init__.py can register controller
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["sensor_async_add_entities"] = async_add_entities

    hass.data[DOMAIN]["async_add_entities"] = (
        async_add_entities  # TODO used in reverse_geocode somehow
    )
    hass.data[DOMAIN]["entities"] = {}

    entry_devices = entry.data.get("devices", {})
    pli = hass.data[DOMAIN].get(DATA_INTEGRATION)

    if pli:
        if not pli.hass:
            _LOGGER.warning("sensor.py async_setup_entry called with pli.hass empty")
            return
        else:
            _LOGGER.debug("sensor.py async_setup_entry called with pli.hass available")

        entities = hass.data[DOMAIN].setdefault("entities", {})
        new_entities = []
        seen = set()

        if pli.configuration.get(CONF_FOLLOW_PERSON_INTEGRATION):
            entity_ids = await hass.async_add_executor_job(
                hass.states.entity_ids, "person"
            )
            for trigger_entity_id in entity_ids:
                trigger = PersonLocationTrigger(trigger_entity_id, pli)
                await trigger.async_init()

                entity_id = trigger.target_name
                person_name = trigger.person_name

                if entity_id in seen or entity_id in entities:
                    continue

                sensor = PersonLocationTargetSensor(entity_id, pli, person_name)
                entities[entity_id] = sensor
                new_entities.append(sensor)
                seen.add(entity_id)

        for device_id, person_name in entry_devices.items():
            entity_id = f"sensor.{person_name.lower()}_location"

            if entity_id in seen or entity_id in entities:
                continue

            sensor = PersonLocationTargetSensor(entity_id, pli, person_name)
            entities[entity_id] = sensor
            new_entities.append(sensor)
            seen.add(entity_id)

        if new_entities:
            async_add_entities(new_entities)

    else:
        _LOGGER.debug("[async_setup_entry] pli not available")


# =====================================================================
# Template Sensor Factory
# =====================================================================


def create_and_register_template_sensor(
    hass: HomeAssistant,
    parent: PersonLocationTargetSensor,
    suffix: str,
    value: str,
    attrs: dict,
) -> PersonLocationTemplateSensor:
    """Create or update a PersonLocationTemplateSensor safely."""
    entities = hass.data[DOMAIN]["entities"]
    entity_id = f"{parent._entity_id}_{suffix}"

    if entity_id in entities:
        sensor = entities[entity_id]
        sensor._state = value
        sensor._attr_extra_state_attributes.update(attrs)

        if sensor.hass:
            hass.loop.call_soon_threadsafe(lambda: sensor.async_write_ha_state())

        return sensor

    sensor = PersonLocationTemplateSensor(parent, suffix, value, attrs)
    async_add_entities = hass.data[DOMAIN]["async_add_entities"]

    hass.loop.call_soon_threadsafe(lambda: async_add_entities([sensor]))
    entities[entity_id] = sensor
    return sensor


# =====================================================================
# Helper
# =====================================================================


def get_target_entity(
    pli: PersonLocationIntegration, entity_id: str
) -> PersonLocationTargetSensor | None:
    """Get the target entity for a given entity_id."""
    return pli.hass.data.get(DOMAIN, {}).get("entities", {}).get(entity_id)

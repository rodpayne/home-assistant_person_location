"""Sensor platform for person_location integration."""

from datetime import datetime, timedelta, timezone
from functools import partial
import logging
from homeassistant.components.sensor import SensorEntity
# from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import (
    async_track_point_in_time,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.mobile_app.const import ATTR_VERTICAL_ACCURACY
from homeassistant.const import (
    STATE_UNKNOWN,
    ATTR_GPS_ACCURACY,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
)
from .const import DOMAIN, INTEGRATION_NAME
from .const import (
    ATTR_ALTITUDE,
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_DRIVING_MILES,
    ATTR_DRIVING_MINUTES,
    ATTR_GEOCODED,
    ATTR_METERS_FROM_HOME,
    ATTR_MILES_FROM_HOME,
    ATTR_ZONE,
    CONF_CREATE_SENSORS,
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
    DATA_CONFIGURATION,
    IC3_STATIONARY_ZONE_PREFIX,
    TARGET_LOCK,
    ZONE_DOMAIN,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

def get_target_entity(pli, entity_id):
    return pli.hass.data.get(DOMAIN, {}).get("entities", {}).get(entity_id)

class PersonLocationTargetSensor(SensorEntity, RestoreEntity):
    """Main target sensor created by this integration."""

    def __init__(self, entity_id, pli, person_name, friendly_name=""):
        self._entity_id = entity_id
        self._pli = pli
        self._person_name = person_name
        self._attr_unique_id = f"{entity_id}_target"
        self._attr_name = friendly_name or f"{person_name} Location"
        self._attr_native_value = STATE_UNKNOWN
        self._attr_extra_state_attributes = {}
        self.this_entity_info = {
            "geocode_count": 0,
            "locality": "?",
            "trigger_count": 0,
        }
        if not self._pli._target_sensors_restored:
            self._pli._target_sensors_restored = []
        self._attr_extra_state_attributes["version"] = f"{DOMAIN} {VERSION}"
        self._attr_last_updated = datetime.now(timezone.utc)
        self._attr_last_changed = self._attr_last_updated
        self._previous_state = self._attr_native_value
    
    # old code has target.last_updated
    @property
    def last_updated(self):
        return self._attr_last_updated
    
    # old code has target.last_changed
    @property
    def last_changed(self):
        return self._attr_last_changed

    @property
    def personName(self):
        return self._person_name
    
    @property
    def device_info(self) -> DeviceInfo:
        """Group target sensor under the person's device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._person_name.lower()}_location")},
            name=f"{self._person_name} Location",
            manufacturer=INTEGRATION_NAME,
            model="Person Tracker",
            #via_device=(DOMAIN, "person_locations_device"),  # link to umbrella
        )

    def handle_delayed_state_change(self,
        now, *, entity_id=None, from_state=None, to_state=None, minutes=3
    ):
        """Handle the delayed state change."""

        _LOGGER.debug(
            "[handle_delayed_state_change]"
            + " (%s) === Start === from_state = %s; to_state = %s"
            % (entity_id, from_state, to_state)
        )

        with TARGET_LOCK:
            """Lock while updating the target(entity_id)."""
            _LOGGER.debug("[handle_delayed_state_change]" + " TARGET_LOCK obtained")
            # target = PERSON_LOCATION_TARGET(entity_id, pli)
            target = get_target_entity(self._pli, entity_id)
            if not target:
                _LOGGER.warning("[handle_delayed_state_change] no target sensor found for %s", entity_id)
                return False

            elapsed_timespan = datetime.now(timezone.utc) - target.last_changed
            elapsed_minutes = (
                elapsed_timespan.total_seconds() + 1
            ) / 60  # fudge factor of one second

            if target._attr_native_value != from_state:
                _LOGGER.debug(
                    "[handle_delayed_state_change]"
                    + " Skip update: state %s is no longer %s"
                    % (target._attr_native_value, from_state)
                )
            elif elapsed_minutes < minutes:
                _LOGGER.debug(
                    "[handle_delayed_state_change]"
                    + " Skip update: state change minutes ago %s less than %s"
                    % (elapsed_minutes, minutes)
                )
            else:
                target._attr_native_value = to_state

                if to_state == "Home":
                    target._attr_extra_state_attributes[ATTR_BREAD_CRUMBS] = to_state
                    target._attr_extra_state_attributes[ATTR_COMPASS_BEARING] = 0
                    target._attr_extra_state_attributes[ATTR_DIRECTION] = "home"
                elif to_state == "Away":
                    if self._pli.configuration.get(CONF_SHOW_ZONE_WHEN_AWAY,False):
                        #reportedZone = target._attr_extra_state_attributes[ATTR_ZONE]
                        reportedZone = target._attr_extra_state_attributes.get(ATTR_ZONE)
                        zoneStateObject = self._pli.hass.states.get(
                            ZONE_DOMAIN + "." + reportedZone
                        )
                        if (
                            zoneStateObject is None
                                or 
                            reportedZone.startswith(IC3_STATIONARY_ZONE_PREFIX)
                        ):
                            _LOGGER.debug(
                                f"Skipping use of zone {reportedZone} for Away state"
                            )
                            #pass
                        else:
                            zoneAttributesObject = zoneStateObject.attributes.copy()
                            if "friendly_name" in zoneAttributesObject:
                                target._attr_native_value = zoneAttributesObject["friendly_name"]
                    if self._pli.configuration[CONF_HOURS_EXTENDED_AWAY] != 0:
                        self.change_state_later(
                            target.entity_id,
                            target._attr_native_value,
                            "Extended Away",
                            (self._pli.configuration[CONF_HOURS_EXTENDED_AWAY] * 60),
                        )
                        #pass
                elif to_state == "Extended Away":
                    pass

                target.set_state()
        _LOGGER.debug(
            "[handle_delayed_state_change]" + " (%s) === Return ===" % (entity_id)
        )

    def change_state_later(self,entity_id, from_state, to_state, minutes=3):
        """Set timer to handle the delayed state change."""

        _LOGGER.debug("[change_state_later]" + " (%s) === Start ===" % (entity_id))
        point_in_time = datetime.now() + timedelta(minutes=minutes)
        remove = async_track_point_in_time(
            self._pli.hass,
            partial(
                self.handle_delayed_state_change,
                entity_id=entity_id,
                from_state=from_state,
                to_state=to_state,
                minutes=minutes,
            ),
            point_in_time=point_in_time,
        )
        if remove:
            _LOGGER.debug(
                "[change_state_later]"
                + " (%s) not-so-binary, handle_delayed_state_change(, %s, %s, %d) has been scheduled"
                % (entity_id, from_state, to_state, minutes)
            )
        _LOGGER.debug("[change_state_later]" + " (%s) === Return ===" % (entity_id))

    async def async_added_to_hass(self):
        """Restore state after reboot."""

        old_state = await self.async_get_last_state()
        if old_state is not None:
            self._attr_native_value = old_state.state
            self._attr_extra_state_attributes.update(old_state.attributes)
            self._attr_last_updated = old_state.last_updated
            self._attr_last_changed = old_state.last_changed
            self._previous_state = self._attr_native_value
            _LOGGER.debug("Restored target sensor %s with state %s, last_updated %s",
                self._entity_id,
                old_state.state,
                old_state.last_updated
            )
            if self._entity_id not in self._pli._target_sensors_restored:
                self._pli._target_sensors_restored.append(self._entity_id)

            if old_state.state in ["Home", ""]:
                pass
            elif old_state.state == "Just Left":
                _LOGGER.debug("Presence detection not-so-binary, change state later: Away")
                self.change_state_later(
                    self._entity_id,
                    old_state.state,
                    "Away",
                    self._pli.configuration[CONF_MINUTES_JUST_LEFT],
                )
            elif old_state.state == "Just Arrived":
                _LOGGER.debug("Presence detection not-so-binary, change state later: Home")
                self.change_state_later(
                    self._entity_id,
                    old_state.state,
                    "Home",
                    self._pli.configuration[CONF_MINUTES_JUST_ARRIVED],
                )
            else:       # Otherwise, treat as "Away"
                _LOGGER.debug("Presence detection not-so-binary, change state later: Extended Away")
                self.change_state_later(
                    self._entity_id,
                    old_state.state,
                    "Extended Away",
                    self._pli.configuration[CONF_HOURS_EXTENDED_AWAY] * 60,
                )
            
    def set_state(self):
        """Called by services to push updates."""

        self._attr_last_updated = datetime.now(timezone.utc)
        if self._previous_state != self._attr_native_value:
            self._attr_last_changed = self._attr_last_updated
            self._previous_state = self._attr_native_value

        # Schedule state write safely
        self._pli.hass.add_job(self.async_write_ha_state)

    async def async_set_state(self):
        """Called by async services to push updates."""

        self._attr_last_updated = datetime.now(timezone.utc)
        if self._previous_state != self._attr_native_value:
            self._attr_last_changed = self._attr_last_updated
            self._previous_state = self._attr_native_value

        await self.async_write_ha_state()

    def make_template_sensor(self, attributeName, supplementalAttributeArray):
        """Make an additional sensor that will be used instead of making a template sensor."""

        _LOGGER.debug("[make_template_sensor] === Start === %s", attributeName)

        if type(attributeName) is str:
            if attributeName in self._attr_extra_state_attributes:
                templateSuffix = attributeName
                templateState = self._attr_extra_state_attributes[attributeName]
            else:
                return
        elif type(attributeName) is dict:
            for templateSuffix in attributeName:
                templateState = attributeName[templateSuffix]

        templateAttributes = {}
        for supplementalAttribute in supplementalAttributeArray:
            if type(supplementalAttribute) is str:
                if supplementalAttribute in self._attr_extra_state_attributes:
                    templateAttributes[supplementalAttribute] = self._attr_extra_state_attributes[
                        supplementalAttribute
                    ]
            elif type(supplementalAttribute) is dict:
                for supplementalAttributeKey in supplementalAttribute:
                    templateAttributes[supplementalAttributeKey] = (
                        supplementalAttribute[supplementalAttributeKey]
                    )
            else:
                _LOGGER.debug(
                    "supplementalAttribute %s %s",
                    supplementalAttribute,
                    type(supplementalAttribute),
                )
        target = self._pli.hass.data[DOMAIN]["entities"].get(self._entity_id)
        create_and_register_template_sensor(self._pli.hass, target, attributeName, templateState, templateAttributes)

        _LOGGER.debug("[make_template_sensor] === Return === %s", attributeName)

    def make_template_sensors(self):
        """Make the additional sensors if they are requested."""

        create_sensors_list = (
            self._pli.configuration[CONF_CREATE_SENSORS] 
                or 
            self._pli.hass.data[DOMAIN][DATA_CONFIGURATION][CONF_CREATE_SENSORS]
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

            elif attributeName == ATTR_GEOCODED:
                pass

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
                        ATTR_ICON,
                        {ATTR_UNIT_OF_MEASUREMENT: "m"},
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

# class PersonLocationTemplateSensor(SensorEntity, RestoreEntity):
class PersonLocationTemplateSensor(SensorEntity):
    """Template sensor (altitude, speed, etc.) tied to a person."""

    # todo: We may not need template sensor entities to be restored after reboot 
    # because they can be cleanly recreated when the target sensors are restored.
    # todo: Test without RestoreEntity and async_added_to_hass.

    def __init__(self, parent: PersonLocationTargetSensor, suffix: str, value, attrs):
        base_id = getattr(parent, "_entity_id", None) or getattr(parent, "entity_id")
        self._entity_id = f"{base_id}_{suffix}"
        self._parent = parent
        self._suffix = suffix
        self._attr_unique_id = f"{base_id}_{suffix}_template"
        self._attr_name = f"{getattr(parent, 'personName', base_id)} {suffix}"
        self._attr_native_value = value
        self._attr_extra_state_attributes = attrs
        self._pending_update = True
        self._unsub = None  # for cleanup of listeners if needed

    @property
    def device_info(self) -> DeviceInfo:
        """Group template sensors under the same device as the parent target sensor."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._parent._person_name.lower()}_location")},
            name=f"{self._parent._person_name} Location",
            manufacturer=INTEGRATION_NAME,
            model="Person Tracker",
        )
    '''
    async def async_added_to_hass(self):
        """Restore state after reboot and schedule initial update safely."""
        old_state = await self.async_get_last_state()
        if old_state:
            self._attr_native_value = old_state.state
            self._attr_extra_state_attributes.update(old_state.attributes)
            _LOGGER.debug(
                "Restored template sensor %s with %s",
                self._attr_unique_id,
                old_state.state,
            )

        if self._pending_update:
            self._pending_update = False
            if self.hass:
                # Schedule state write safely via event loop
                self.hass.loop.call_soon_threadsafe(
                    lambda: self.async_write_ha_state()
                )
    '''
    async def async_will_remove_from_hass(self):
        """Clean up any listeners or tasks when entity is removed."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        _LOGGER.debug("Template sensor %s removed cleanly", self._attr_unique_id)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up person_location sensors from a config entry."""

    _LOGGER.debug("[async_setup_entry] entry: %s", entry.entry_id)
    _LOGGER.debug("[async_setup_entry] entry.data: %s", entry.data)

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.data[DOMAIN]["async_add_entities"] = async_add_entities
    hass.data[DOMAIN]["entities"] = {}

    # Create initial target sensors here, based on the person_name of devices
    entry_devices = entry.data.get("devices", {})
    _LOGGER.debug("[async_setup_entry] entry.data.devices: %s", entry_devices)

    pli = hass.data[DOMAIN].get("integration")
    if pli:
        entities = hass.data[DOMAIN].setdefault("entities", {})
        new_entities = []
        seen_entity_ids = set()

        for device_id, person_name in entry_devices.items():
            entity_id = f"sensor.{person_name.lower()}_location"

            if entity_id in seen_entity_ids or entity_id in entities:
                _LOGGER.debug("[async_setup_entry] Skipping duplicate entity: %s", entity_id)
                continue

            sensor = PersonLocationTargetSensor(entity_id, pli, person_name)
            entities[entity_id] = sensor
            new_entities.append(sensor)
            seen_entity_ids.add(entity_id)

            _LOGGER.debug("[async_setup_entry] Created and registered entity: %s", entity_id)

        if new_entities:
            async_add_entities(new_entities)
            _LOGGER.debug("[async_setup_entry] Registering entities: %s", [e.entity_id for e in new_entities])
    else:
        _LOGGER.debug("[async_setup_entry] pli is not yet available, hass.data[DOMAIN]: %s", hass.data[DOMAIN])


def create_and_register_template_sensor(hass, parent, suffix, value, attrs):
    """Create or update a PersonLocationTemplateSensor safely."""

    entities = hass.data[DOMAIN]["entities"]
    entity_id = f"sensor.{parent.personName.lower()}_location_{suffix.lower()}"

    if entity_id in entities:
        # Update existing sensor
        sensor = entities[entity_id]
        sensor._attr_native_value = value
        sensor._attr_extra_state_attributes.update(attrs)

        # Schedule safe state update only if hass is still attached
        if sensor.hass:
            hass.loop.call_soon_threadsafe(
                lambda: sensor.async_write_ha_state()
            )
        else:
            _LOGGER.warning(
                "[create_and_register_template_sensor] Sensor %s has no hass; skipping state write",
                entity_id,
            )

        _LOGGER.debug(
            "[create_and_register_template_sensor] Updated existing template sensor %s",
            entity_id,
        )
        return sensor

    # Create new sensor
    sensor = PersonLocationTemplateSensor(
        parent=parent,
        suffix=suffix,
        value=value,
        attrs=attrs,
    )

    async_add_entities = hass.data[DOMAIN]["async_add_entities"]

    # Schedule entity addition safely
    hass.loop.call_soon_threadsafe(lambda: async_add_entities([sensor]))

    entities[entity_id] = sensor

    _LOGGER.debug(
        "[create_and_register_template_sensor] Created new template sensor %s",
        entity_id,
    )
    return sensor
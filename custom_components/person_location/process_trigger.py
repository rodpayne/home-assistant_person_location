"""The person_location integration process_trigger service."""

import logging
import string
from datetime import datetime, timedelta, timezone
from functools import partial

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.const import (
    ATTR_SOURCE_TYPE,
)
from homeassistant.components.mobile_app.const import (
    ATTR_VERTICAL_ACCURACY,
)
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    STATE_NOT_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers.event import (
    track_point_in_time,
)

from .const import (
    ATTR_ALTITUDE,
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_ICON,
    ATTR_LAST_LOCATED,
    ATTR_LOCATION_TIME,
    ATTR_PERSON_NAME,
    ATTR_REPORTED_STATE,
    ATTR_SOURCE,
    ATTR_SPEED,
    ATTR_ZONE,
    CONF_FRIENDLY_NAME_TEMPLATE,
    CONF_HOURS_EXTENDED_AWAY,
    CONF_MINUTES_JUST_ARRIVED,
    CONF_MINUTES_JUST_LEFT,
    CONF_SHOW_ZONE_WHEN_AWAY,
    DEFAULT_FRIENDLY_NAME_TEMPLATE,
    DOMAIN,
    IC3_STATIONARY_ZONE_PREFIX,
    # PERSON_LOCATION_TARGET,
    PERSON_LOCATION_TRIGGER,
    TARGET_LOCK,
    ZONE_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

def get_target_entity(pli, entity_id):
    return pli.hass.data.get(DOMAIN, {}).get("entities", {}).get(entity_id)

def setup_process_trigger(pli):
    """Initialize process_trigger service."""

    def handle_delayed_state_change(
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
            # Look up the existing entity from hass.data
            #entities = pli.hass.data[DOMAIN]["entities"]
            #target = entities.get(entity_id)
            target = get_target_entity(pli, entity_id)
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
                    if pli.configuration[CONF_SHOW_ZONE_WHEN_AWAY]:
                        reportedZone = target._attr_extra_state_attributes[ATTR_ZONE]
                        zoneStateObject = pli.hass.states.get(
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
                            pass
                        else:
                            zoneAttributesObject = zoneStateObject.attributes.copy()
                            if "friendly_name" in zoneAttributesObject:
                                target._attr_native_value = zoneAttributesObject["friendly_name"]
                    if pli.configuration[CONF_HOURS_EXTENDED_AWAY] != 0:
                        change_state_later(
                            target.entity_id,
                            target._attr_native_value,
                            "Extended Away",
                            (pli.configuration[CONF_HOURS_EXTENDED_AWAY] * 60),
                        )
                        pass
                elif to_state == "Extended Away":
                    pass

                target.set_state()
        _LOGGER.debug(
            "[handle_delayed_state_change]" + " (%s) === Return ===" % (entity_id)
        )

    def change_state_later(entity_id, from_state, to_state, minutes=3):
        """Set timer to handle the delayed state change."""

        _LOGGER.debug("[change_state_later]" + " (%s) === Start ===" % (entity_id))
        point_in_time = datetime.now() + timedelta(minutes=minutes)
        remove = track_point_in_time(
            pli.hass,
            partial(
                handle_delayed_state_change,
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
                + " (%s) handle_delayed_state_change(, %s, %s, %d) has been scheduled"
                % (entity_id, from_state, to_state, minutes)
            )
        _LOGGER.debug("[change_state_later]" + " (%s) === Return ===" % (entity_id))

    def utc2local_naive(utc_dt):
        local = utc_dt.replace(tzinfo=timezone.utc).astimezone(tz=None)
        if str(local)[-6] == "-" or str(local)[-6] == "+":
            local = str(local)[:-6]  # remove offset to make it offset-naive
            local = datetime.strptime(local, "%Y-%m-%d %H:%M:%S.%f")
        return local

    def handle_process_trigger(call):
        """
        Handle changes of triggered device trackers and sensors.

        Input:
            - Parameters for the call:
                entity_id
                from_state
                to_state
        Output (if update is accepted):
            - Updated "sensor.<personName>_location" with <personName>'s location and status:
                Attributes:
                - selected attributes from the triggered device tracker
                - state: "Just Arrived", "Home", "Just Left", "Away", or "Extended Away"
                If CONF_SHOW_ZONE_WHEN_AWAY, then the <Zone> is reported instead of "Away".
                - person_name: <personName>
                - source: entity_id of the device tracker that triggered the automation
                - reported_state: the state reported by device tracker = "Home", "Away", or <zone>
                - bread_crumbs: the series of locations that have been seen
                - icon: the icon that corresponds with the current zone
            - Call rest_command service to update HomeSeer: 'homeseer_<personName>_<state>'
        """

        entity_id = call.data.get(CONF_ENTITY_ID, "NONE")
        triggerFrom = call.data.get("from_state", "NONE")
        triggerTo = call.data.get("to_state", "NONE")

        # Validate the input entity:

        if entity_id == "NONE":
            {
                _LOGGER.warning(
                    "[handle_process_trigger] %s is required in call of %s.process_trigger service."
                    % (CONF_ENTITY_ID, DOMAIN)
                )
            }
            return False

        ha_just_started = pli.attributes["startup"]
        if ha_just_started:
            _LOGGER.debug("HA just started flag is on")

        trigger = PERSON_LOCATION_TRIGGER(entity_id, pli)

        _LOGGER.debug(
            "(%s) === Start === from_state = %s; to_state = %s",
            trigger.entity_id,
            triggerFrom,
            triggerTo,
        )

        if trigger.entity_id == trigger.targetName:
            _LOGGER.debug(
                "(%s) Decision: skip self update: target = (%s)",
                trigger.entity_id,
                trigger.targetName,
            )
        elif ATTR_GPS_ACCURACY in trigger.attributes and (
            (trigger.attributes[ATTR_GPS_ACCURACY] == 0)
            or (trigger.attributes[ATTR_GPS_ACCURACY] >= 100)
        ):
            _LOGGER.debug(
                "(%s) Decision: skip update: gps_accuracy = %s",
                trigger.entity_id,
                trigger.attributes[ATTR_GPS_ACCURACY],
            )
        else:
            if ATTR_LAST_LOCATED in trigger.attributes:
                last_located = trigger.attributes[ATTR_LAST_LOCATED]
                new_location_time = datetime.strptime(last_located, "%Y-%m-%d %H:%M:%S")
            else:
                new_location_time = utc2local_naive(
                    trigger.last_updated
                )  # HA last_updated is UTC

            if ATTR_SOURCE_TYPE in trigger.attributes:
                triggerSourceType = trigger.attributes[ATTR_SOURCE_TYPE]
            else:
                triggerSourceType = "other"
                # person entities do not indicate the source type, dig deeper:
                if (
                    "source" in trigger.attributes
                    and "." in trigger.attributes["source"]
                ):
                    triggerSourceObject = pli.hass.states.get(
                        trigger.attributes["source"]
                    )
                    if triggerSourceObject is not None:
                        if ATTR_SOURCE_TYPE in triggerSourceObject.attributes:
                            triggerSourceType = triggerSourceObject.attributes[
                                ATTR_SOURCE_TYPE
                            ]

            # ---------------------------------------------------------
            # Get the current state of the target person location
            # sensor and decide if it should be updated with values
            # from the triggered device tracker:
            saveThisUpdate = False
            # ---------------------------------------------------------

            with TARGET_LOCK:
                """Lock while updating the target(trigger.targetName)."""
                _LOGGER.debug(
                    "(%s) TARGET_LOCK obtained",
                    trigger.targetName,
                )
                # target = PERSON_LOCATION_TARGET(trigger.targetName, pli)
                # Look up the existing entity from hass.data
                #entities = pli.hass.data[DOMAIN]["entities"]
                #target = entities.get(trigger.targetName)
                target = get_target_entity(pli, trigger.targetName)
                if not target:
                    _LOGGER.warning("No target sensor found for %s", trigger.targetName)
                    return False

                target.this_entity_info["trigger_count"] += 1

                if triggerTo in ["NotSet", STATE_UNAVAILABLE, STATE_UNKNOWN]:
                    _LOGGER.debug(
                        "(%s) Decision: skip update: triggerTo = %s",
                        trigger.entity_id,
                        triggerTo,
                    )
                    if ATTR_SOURCE in target._attr_extra_state_attributes and target._attr_extra_state_attributes[ATTR_SOURCE] == trigger.entity_id:
                        _LOGGER.debug(
                            "(%s) Removing from target's source",
                            trigger.entity_id,
                        )
                        target._attr_extra_state_attributes.pop(ATTR_SOURCE)
                        target.set_state()
                    return True

                if ATTR_LOCATION_TIME in target._attr_extra_state_attributes:
                    old_location_time = datetime.strptime(
                        str(target._attr_extra_state_attributes[ATTR_LOCATION_TIME]),
                        "%Y-%m-%d %H:%M:%S.%f",
                    )
                else:
                    old_location_time = utc2local_naive(
                        target.last_updated
                    )  # HA last_updated is UTC

                if new_location_time < old_location_time:
                    _LOGGER.debug(
                        "(%s) Decision: skip stale update: %s < %s",
                        trigger.entity_id,
                        new_location_time,
                        old_location_time,
                    )
                # elif target.firstTime:
                #     saveThisUpdate = True
                #     _LOGGER.debug(
                #         "(%s) Decision: target %s does not yet exist (normal at startup)",
                #         trigger.entity_id,
                #         target.entity_id,
                #     )
                #     oldTargetState = "none"
                else:
                    oldTargetState = target._attr_native_value.lower()
                    if oldTargetState == STATE_UNKNOWN:
                        saveThisUpdate = True
                        _LOGGER.debug(
                            "(%s) Decision: accepting the first update of %s",
                            trigger.entity_id,
                            target.entity_id,
                        )
                    elif triggerSourceType == SourceType.GPS:  # gps device?
                        if triggerTo != triggerFrom:  # did it change zones?
                            saveThisUpdate = True  # gps changing zones is assumed to be new, correct info
                            _LOGGER.debug(
                                "(%s) Decision: trigger has changed zones",
                                trigger.entity_id,
                            )
                        else:
                            if (
                                ATTR_SOURCE not in target._attr_extra_state_attributes
                                or target._attr_extra_state_attributes[ATTR_SOURCE] == trigger.entity_id
                                or "reported_state" not in target._attr_extra_state_attributes
                            ):  # same entity as we are following, if any?
                                saveThisUpdate = True
                                _LOGGER.debug(
                                    "(%s) Decision: continue following trigger",
                                    trigger.entity_id,
                                )
                            elif (
                                ATTR_LATITUDE in trigger.attributes
                                and ATTR_LONGITUDE in trigger.attributes
                                and ATTR_LATITUDE not in target._attr_extra_state_attributes
                                and ATTR_LONGITUDE not in target._attr_extra_state_attributes
                            ):
                                saveThisUpdate = True
                                _LOGGER.debug(
                                    "(%s) Decision: use source that has coordinates",
                                    trigger.entity_id,
                                )
                            elif (
                                trigger.state == target._attr_extra_state_attributes[ATTR_REPORTED_STATE]
                            ):  # same status as the one we are following?
                                #if ATTR_VERTICAL_ACCURACY in trigger.attributes:
                                #    if (
                                #        ATTR_VERTICAL_ACCURACY not in target._attr_extra_state_attributes
                                #    ) or (
                                #        trigger.attributes[ATTR_VERTICAL_ACCURACY] > 0
                                #        and target._attr_extra_state_attributes[ATTR_VERTICAL_ACCURACY]
                                #        == 0
                                #    ):  # better choice based on accuracy?
                                #        saveThisUpdate = True
                                #        _LOGGER.debug(
                                #            "(%s) Decision: vertical_accuracy is better than %s",
                                #            trigger.entity_id,
                                #            target._attr_extra_state_attributes[ATTR_SOURCE],
                                #        )
                                if (
                                    ATTR_GPS_ACCURACY in trigger.attributes
                                    and (ATTR_GPS_ACCURACY not in target._attr_extra_state_attributes
                                    or trigger.attributes[ATTR_GPS_ACCURACY]
                                    < target._attr_extra_state_attributes[ATTR_GPS_ACCURACY])
                                ):  # better choice based on accuracy?
                                    saveThisUpdate = True
                                    _LOGGER.debug(
                                        "(%s) Decision: gps_accuracy is better than %s",
                                        trigger.entity_id,
                                        target._attr_extra_state_attributes[ATTR_SOURCE],
                                    )
                            elif (
                                ha_just_started
                                and ATTR_LATITUDE in trigger.attributes
                                and ATTR_LONGITUDE in trigger.attributes
                            ):
                                saveThisUpdate = True
                                _LOGGER.debug(
                                    "(%s) Decision: accept gps source that has coordinates during startup",
                                    trigger.entity_id,
                                )
                    else:  # source = router or ping
                        if triggerTo != triggerFrom:  # did tracker change state?
                            if ((trigger.stateHomeAway == "Home") != (oldTargetState == "home")):  # reporting Home
                                    saveThisUpdate = True
                                    _LOGGER.debug(
                                        "(%s) Decision: non-GPS trigger has changed state",
                                        trigger.entity_id,
                                    )

                # -----------------------------------------------------

                if not saveThisUpdate:
                    _LOGGER.debug(
                        "(%s) Decision: ignore update",
                        trigger.entity_id,
                    )
                else:
                    _LOGGER.debug(
                        "(%s Saving This Update) -state: %s -attributes: %s",
                        trigger.entity_id,
                        trigger.state,
                        trigger.attributes,
                    )

                    # Carry over selected attributes from trigger to target:

                    if ATTR_SOURCE_TYPE in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_SOURCE_TYPE] = trigger.attributes[
                            ATTR_SOURCE_TYPE
                        ]
                    else:
                        if ATTR_SOURCE_TYPE in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_SOURCE_TYPE)

                    if (
                        ATTR_LATITUDE in trigger.attributes
                        and ATTR_LONGITUDE in trigger.attributes
                    ):
                        target._attr_extra_state_attributes[ATTR_LATITUDE] = trigger.attributes[
                            ATTR_LATITUDE
                        ]
                        target._attr_extra_state_attributes[ATTR_LONGITUDE] = trigger.attributes[
                            ATTR_LONGITUDE
                        ]
                    else:
                        if ATTR_LATITUDE in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_LATITUDE)
                        if ATTR_LONGITUDE in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_LONGITUDE)

                    if ATTR_GPS_ACCURACY in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_GPS_ACCURACY] = trigger.attributes[
                            ATTR_GPS_ACCURACY
                        ]
                    else:
                        if ATTR_GPS_ACCURACY in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_GPS_ACCURACY)

                    if ATTR_ALTITUDE in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_ALTITUDE] = round(
                            trigger.attributes[ATTR_ALTITUDE]
                        )
                    else:
                        if ATTR_ALTITUDE in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_ALTITUDE)

                    if ATTR_VERTICAL_ACCURACY in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_VERTICAL_ACCURACY] = trigger.attributes[
                            ATTR_VERTICAL_ACCURACY
                        ]
                    else:
                        if ATTR_VERTICAL_ACCURACY in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_VERTICAL_ACCURACY)

                    if ATTR_ENTITY_PICTURE in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_ENTITY_PICTURE] = trigger.attributes[
                            ATTR_ENTITY_PICTURE
                        ]
                    else:
                        if ATTR_ENTITY_PICTURE in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_ENTITY_PICTURE)

                    if ATTR_SPEED in trigger.attributes:
                        target._attr_extra_state_attributes[ATTR_SPEED] = trigger.attributes[
                            ATTR_SPEED
                        ]
                        _LOGGER.debug(
                            "(%s) speed = %s",
                            trigger.entity_id,
                            trigger.attributes[ATTR_SPEED],
                        )
                    else:
                        if ATTR_SPEED in target._attr_extra_state_attributes:
                            target._attr_extra_state_attributes.pop(ATTR_SPEED)

                    target._attr_extra_state_attributes[ATTR_SOURCE] = trigger.entity_id
                    target._attr_extra_state_attributes[ATTR_REPORTED_STATE] = trigger.state
                    target._attr_extra_state_attributes[ATTR_PERSON_NAME] = string.capwords(
                        trigger.personName
                    )

                    target._attr_extra_state_attributes[ATTR_LOCATION_TIME] = new_location_time.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )
                    _LOGGER.debug(
                        "(%s) new_location_time = %s",
                        target.entity_id,
                        new_location_time,
                    )

                    # Determine the zone and the icon to be used:

                    if ATTR_ZONE in trigger.attributes:
                        reportedZone = trigger.attributes[ATTR_ZONE]
                    else:
                        reportedZone = (
                            trigger.state.lower().replace(" ", "_").replace("'", "_")
                        )
                    zoneStateObject = pli.hass.states.get(
                        ZONE_DOMAIN + "." + reportedZone
                    )
                    icon = "mdi:help-circle"
                    if (
                        zoneStateObject is not None
                            and 
                        not reportedZone.startswith(IC3_STATIONARY_ZONE_PREFIX)
                    ):
                        zoneAttributesObject = zoneStateObject.attributes.copy()
                        if ATTR_ICON in zoneAttributesObject:
                            icon = zoneAttributesObject[ATTR_ICON]

                    target._attr_extra_state_attributes[ATTR_ICON] = icon
                    target._attr_extra_state_attributes[ATTR_ZONE] = reportedZone

                    _LOGGER.debug(
                        "(%s) zone = %s; icon = %s",
                        trigger.entity_id,
                        reportedZone,
                        target._attr_extra_state_attributes[ATTR_ICON],
                    )

                    if reportedZone == "home":
                        target._attr_extra_state_attributes[ATTR_LATITUDE] = pli.attributes[
                            "home_latitude"
                        ]
                        target._attr_extra_state_attributes[ATTR_LONGITUDE] = pli.attributes[
                            "home_longitude"
                        ]

                    # Set up something like https://philhawthorne.com/making-home-assistants-presence-detection-not-so-binary/
                    # https://github.com/rodpayne/home-assistant_person_location?tab=readme-ov-file#make-presence-detection-not-so-binary
                    # If Home Assistant just started, just go with Home or Away as the initial state.

                    _LOGGER.debug(f"Presence detection not-so-binary: stateHomeAway = {trigger.stateHomeAway}, oldTargetState = {oldTargetState}")
                    if trigger.stateHomeAway == "Home":
                        # State is changing to Home.
                        if (
                            oldTargetState in ["just left", "none"]
                            or ha_just_started
                            or (pli.configuration[CONF_MINUTES_JUST_ARRIVED] == 0)
                        ):
                            # Initial setting at startup goes straight to Home.
                            # Just Left also goes straight back to Home.
                            # Anything else goes straight to Home if Just Arrived is not an option.
                            newTargetState = "Home"

                            target._attr_extra_state_attributes[ATTR_BREAD_CRUMBS] = newTargetState
                            target._attr_extra_state_attributes[ATTR_COMPASS_BEARING] = 0
                            target._attr_extra_state_attributes[ATTR_DIRECTION] = "home"

                        elif oldTargetState == "home":
                            newTargetState = "Home"
                        elif oldTargetState == "just arrived":
                            newTargetState = "Just Arrived"
                        else:
                            newTargetState = "Just Arrived"
                            change_state_later(
                                target.entity_id,
                                newTargetState,
                                "Home",
                                pli.configuration[CONF_MINUTES_JUST_ARRIVED],
                            )
                    else:
                        # State is changing to not Home.
                        if oldTargetState != "away" and (
                            oldTargetState == "none"
                            or ha_just_started
                            or (pli.configuration[CONF_MINUTES_JUST_LEFT] == 0)
                        ):
                            # initial setting at startup goes straight to Away
                            newTargetState = "Away"
                            if pli.configuration[CONF_HOURS_EXTENDED_AWAY] != 0:
                                change_state_later(
                                    target.entity_id,
                                    "Away",
                                    "Extended Away",
                                    (pli.configuration[CONF_HOURS_EXTENDED_AWAY] * 60),
                                )
                        elif oldTargetState in ["just left", "just arrived"]:
                            newTargetState = "Just Left"
                        elif oldTargetState == "extended away":
                            newTargetState = "Extended Away"
                        elif oldTargetState == "home":
                            newTargetState = "Just Left"
                            change_state_later(
                                target.entity_id,
                                newTargetState,
                                "Away",
                                pli.configuration[CONF_MINUTES_JUST_LEFT],
                            )
                        else:
                            # oldTargetState is either "away" or a Zone
                            newTargetState = "Away"
                    if (
                        newTargetState == "Away"
                        and pli.configuration[CONF_SHOW_ZONE_WHEN_AWAY]
                    ):
                        # Get the state from the zone friendly_name:
                        if (
                            zoneStateObject is None
                                or 
                            reportedZone.startswith(IC3_STATIONARY_ZONE_PREFIX)
                        ):
                            # Skip stray zone names:
                            pass
                        else:
                            zoneAttributesObject = zoneStateObject.attributes.copy()
                            if "friendly_name" in zoneAttributesObject:
                                newTargetState = zoneAttributesObject["friendly_name"]

                    target._attr_native_value = newTargetState

                    _LOGGER.debug(f"Presence detection not-so-binary: newTargetState = {newTargetState}")

                    if ha_just_started:
                        target._attr_extra_state_attributes[ATTR_BREAD_CRUMBS] = newTargetState

                    # target._attr_extra_state_attributes["version"] = f"{DOMAIN} {VERSION}"

                    target.set_state()

                    # Call service to "reverse geocode" the location.
                    # For devices at Home, this will be forced to run
                    # just at startup or on arrival.

                    force_update = (newTargetState in ["Home", "Just Arrived"]) and (
                        oldTargetState in ["away", "extended away", "just left"]
                    )
                    if pli.attributes["startup"]:
                        force_update = True

                    service_data = {
                        "entity_id": target.entity_id,
                        "friendly_name_template": pli.configuration.get(
                            CONF_FRIENDLY_NAME_TEMPLATE,
                            DEFAULT_FRIENDLY_NAME_TEMPLATE,    
                        ),
                        "force_update": force_update,
                    }
                    pli.hass.services.call(
                        DOMAIN, "reverse_geocode", service_data, False
                    )

                _LOGGER.debug(
                    "(%s) TARGET_LOCK release...",
                    trigger.entity_id,
                )
        _LOGGER.debug(
            "(%s) === Return ===",
            trigger.entity_id,
        )

    pli.hass.services.async_register(DOMAIN, "process_trigger", handle_process_trigger)
    return True

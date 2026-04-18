"""process_trigger.py - The person_location integration process_trigger service (async)."""

# pyright: reportMissingImports=false
from __future__ import annotations

# from curses import raw
from typing import TYPE_CHECKING

from custom_components.person_location.helpers.entity import resolve_zone_entity_id

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import ServiceCall

    from . import PersonLocationIntegration

# import asyncio
import logging
import string

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.const import ATTR_SOURCE_TYPE
from homeassistant.components.mobile_app.const import (
    ATTR_VERTICAL_ACCURACY,
)
from homeassistant.components.zone import DOMAIN as ZONE_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_GPS_ACCURACY,
    ATTR_ICON,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_ENTITY_ID,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)

# from homeassistant.util import dt as dt_util
from .const import (
    ATTR_ALTITUDE,
    ATTR_AWAY_TIMESTAMP,
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_LAST_LOCATED,
    ATTR_LOCATION_TIMESTAMP,
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
    INFO_TRIGGER_COUNT,
    STATE_EXTENDED_AWAY,
    STATE_JUST_ARRIVED,
    STATE_JUST_LEFT,
    TARGET_ASYNCIO_LOCK,
)
from .helpers.api import get_home_coordinates
from .helpers.timestamp import parse_ts
from .sensor import get_target_entity
from .trigger import PersonLocationTrigger

_LOGGER = logging.getLogger(__name__)


async def async_setup_process_trigger(pli: PersonLocationIntegration) -> bool:
    """Register the async process_trigger service."""
    # def _utc2local(utc_dt: datetime) -> datetime:
    #    """Convert UTC datetime to local timezone (aware)."""
    #    if utc_dt.tzinfo is None:
    #        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    #    return dt_util.as_local(utc_dt)

    # -------------------------------------------------------------------------
    # Delayed state-change handler (async-safe)
    # -------------------------------------------------------------------------
    async def _handle_delayed_state_change(
        _now: datetime,
        *,
        entity_id: str,
        from_state: str,
        to_state: str,
        minutes: int = 3,
    ) -> bool:
        """Handle delayed transitions (Just Arrived → Home, etc.)."""
        async with TARGET_ASYNCIO_LOCK:
            target = get_target_entity(pli, entity_id)
            if not target:
                return False

            current_state = (target._state or "").lower()
            if current_state != from_state.lower():
                return True

            target._state = to_state

            # Atomic async write
            await target.async_set_state()

        return True

    # -------------------------------------------------------------------------
    # Main process_trigger handler
    # -------------------------------------------------------------------------
    async def handle_process_trigger(call: ServiceCall) -> bool:
        entity_id = call.data.get(CONF_ENTITY_ID)
        trigger_from = call.data.get("from_state")
        trigger_to = call.data.get("to_state")

        # ---------------------------------------------------------------------
        # Initial validation and trigger metadata loading
        # ---------------------------------------------------------------------
        if not entity_id:
            _LOGGER.warning("Missing %s in process_trigger call", CONF_ENTITY_ID)
            return False

        ha_just_started = pli._attr_extra_state_attributes.get("startup", False)

        # Load trigger metadata
        trigger = await PersonLocationTrigger(entity_id, pli).async_init()

        # Skip self-updates
        if trigger.entity_id == trigger.target_name:
            _LOGGER.debug(
                "(%s) Decision: skip self update: target = (%s)",
                trigger.entity_id,
                trigger.target_name,
            )
            return True

        # Skip bad GPS accuracy
        if ATTR_GPS_ACCURACY in trigger.attributes:
            acc = trigger.attributes[ATTR_GPS_ACCURACY]
            if acc == 0 or acc >= 100:
                _LOGGER.debug(
                    "(%s) Decision: skip due to bad GPS accuracy: %s",
                    trigger.entity_id,
                    acc,
                )
                return True

        # Determine new location timestamp
        if ATTR_LAST_LOCATED in trigger.attributes:
            new_location_time = parse_ts(trigger.attributes[ATTR_LAST_LOCATED])
        else:
            new_location_time = parse_ts(trigger.last_updated)

        # Determine source type
        if ATTR_SOURCE_TYPE in trigger.attributes:
            trigger_source_type = trigger.attributes[ATTR_SOURCE_TYPE]
        else:
            trigger_source_type = "other"
            if "source" in trigger.attributes:
                src = trigger.attributes["source"]
                if "." in src:
                    src_obj = pli.hass.states.get(src)
                    if src_obj and ATTR_SOURCE_TYPE in src_obj.attributes:
                        trigger_source_type = src_obj.attributes[ATTR_SOURCE_TYPE]

        # ---------------------------------------------------------------------
        # Update target sensor
        # ---------------------------------------------------------------------
        async with TARGET_ASYNCIO_LOCK:
            target = get_target_entity(pli, trigger.target_name)
            if not target:
                _LOGGER.warning("No target sensor found for %s", trigger.target_name)
                return False

            target.this_entity_info[INFO_TRIGGER_COUNT] += 1

            # Skip unavailable/unknown
            if trigger_to in ["NotSet", STATE_UNAVAILABLE, STATE_UNKNOWN]:
                _LOGGER.debug(
                    "(%s) Decision: skip update: trigger_to = %s",
                    trigger.entity_id,
                    trigger_to,
                )
                if (
                    target._attr_extra_state_attributes.get(ATTR_SOURCE)
                    == trigger.entity_id
                ):
                    _LOGGER.debug(
                        "(%s) Removing from target's source",
                        trigger.entity_id,
                    )
                    target._attr_extra_state_attributes.pop(ATTR_SOURCE, None)
                    await target.async_set_state()
                return True

            # Determine old location timestamp
            raw = target._attr_extra_state_attributes.get(ATTR_LOCATION_TIMESTAMP)
            old_location_time = parse_ts(raw or target.last_updated)

            # Skip stale updates
            if new_location_time < old_location_time:
                _LOGGER.debug(
                    "(%s) Decision: skip stale update: %s < %s",
                    trigger.entity_id,
                    new_location_time,
                    old_location_time,
                )
                return True

            # -----------------------------------------------------------------
            # Decide whether to accept this update
            # -----------------------------------------------------------------
            save_update = False
            old_state = (target._state or "").lower()

            if old_state == STATE_UNKNOWN:
                save_update = True
                _LOGGER.debug(
                    "(%s) Decision: accepting the first update of %s",
                    trigger.entity_id,
                    target.entity_id,
                )
            elif trigger_source_type == SourceType.GPS:
                if trigger_to != trigger_from:
                    save_update = True
                    _LOGGER.debug(
                        "(%s) Decision: GPS trigger has changed state %s → %s",
                        trigger.entity_id,
                        trigger_from,
                        trigger_to,
                    )
                else:
                    if (
                        ATTR_SOURCE not in target._attr_extra_state_attributes
                        or target._attr_extra_state_attributes[ATTR_SOURCE]
                        == trigger.entity_id
                        or ATTR_REPORTED_STATE
                        not in target._attr_extra_state_attributes
                    ):
                        save_update = True
                        _LOGGER.debug(
                            "(%s) Decision: continue following this GPS trigger",
                            trigger.entity_id,
                        )
                    elif (
                        ATTR_LATITUDE in trigger.attributes
                        and ATTR_LONGITUDE in trigger.attributes
                        and ATTR_LATITUDE not in target._attr_extra_state_attributes
                        and ATTR_LONGITUDE not in target._attr_extra_state_attributes
                    ):
                        save_update = True
                        _LOGGER.debug(
                            "(%s) Decision: switch to source that has coordinates",
                            trigger.entity_id,
                        )
                    elif trigger.state == target._attr_extra_state_attributes.get(
                        ATTR_REPORTED_STATE
                    ):
                        # Same status as the one we are following - compare accuracy
                        if ATTR_GPS_ACCURACY in trigger.attributes:
                            old_acc = target._attr_extra_state_attributes.get(
                                ATTR_GPS_ACCURACY, 9999
                            )
                            if trigger.attributes[ATTR_GPS_ACCURACY] < old_acc:
                                save_update = True
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
                        save_update = True
                        _LOGGER.debug(
                            "(%s) Decision: at startup, accept any GPS trigger with coordinates",
                            trigger.entity_id,
                        )
            else:
                # Router/ping
                if trigger_to != trigger_from:
                    if (trigger.state_home_or_not == STATE_HOME) != (
                        trigger.derive_trigger_home_or_not(old_state) == STATE_HOME
                    ):
                        save_update = True
                        _LOGGER.debug(
                            "(%s) Decision: non-GPS trigger has changed state %s → %s",
                            trigger.entity_id,
                            trigger_from,
                            trigger_to,
                        )

            if not save_update:
                _LOGGER.debug(
                    "(%s) Decision: ignore this update",
                    trigger.entity_id,
                )
                return True

            _LOGGER.debug(
                "(%s Saving This Update) -state: %s -attributes: %s",
                trigger.entity_id,
                trigger.state,
                trigger.attributes,
            )

            # -----------------------------------------------------------------
            # Carry overrelevant attributes from trigger to target
            # -----------------------------------------------------------------
            attrs = target._attr_extra_state_attributes

            # Source type
            if ATTR_SOURCE_TYPE in trigger.attributes:
                attrs[ATTR_SOURCE_TYPE] = trigger.attributes[ATTR_SOURCE_TYPE]
            else:
                attrs.pop(ATTR_SOURCE_TYPE, None)

            # Coordinates
            if (
                ATTR_LATITUDE in trigger.attributes
                and ATTR_LONGITUDE in trigger.attributes
            ):
                attrs[ATTR_LATITUDE] = trigger.attributes[ATTR_LATITUDE]
                attrs[ATTR_LONGITUDE] = trigger.attributes[ATTR_LONGITUDE]
            else:
                attrs.pop(ATTR_LATITUDE, None)
                attrs.pop(ATTR_LONGITUDE, None)

            # Accuracy
            if ATTR_GPS_ACCURACY in trigger.attributes:
                attrs[ATTR_GPS_ACCURACY] = trigger.attributes[ATTR_GPS_ACCURACY]
            else:
                attrs.pop(ATTR_GPS_ACCURACY, None)

            # Altitude
            if ATTR_ALTITUDE in trigger.attributes:
                try:
                    attrs[ATTR_ALTITUDE] = round(trigger.attributes[ATTR_ALTITUDE])
                except Exception:
                    attrs[ATTR_ALTITUDE] = trigger.attributes[ATTR_ALTITUDE]
            else:
                attrs.pop(ATTR_ALTITUDE, None)

            # Vertical accuracy
            if ATTR_VERTICAL_ACCURACY in trigger.attributes:
                target._attr_extra_state_attributes[ATTR_VERTICAL_ACCURACY] = (
                    trigger.attributes[ATTR_VERTICAL_ACCURACY]
                )
            else:
                if ATTR_VERTICAL_ACCURACY in target._attr_extra_state_attributes:
                    target._attr_extra_state_attributes.pop(ATTR_VERTICAL_ACCURACY)

            # Entity picture
            if ATTR_ENTITY_PICTURE in trigger.attributes:
                attrs[ATTR_ENTITY_PICTURE] = trigger.attributes[ATTR_ENTITY_PICTURE]
            else:
                attrs.pop(ATTR_ENTITY_PICTURE, None)

            # Speed
            if ATTR_SPEED in trigger.attributes:
                attrs[ATTR_SPEED] = trigger.attributes[ATTR_SPEED]
            else:
                attrs.pop(ATTR_SPEED, None)

            # Basic metadata
            attrs[ATTR_SOURCE] = trigger.entity_id
            attrs[ATTR_REPORTED_STATE] = trigger.state
            attrs[ATTR_PERSON_NAME] = string.capwords(trigger.person_name)
            attrs[ATTR_LOCATION_TIMESTAMP] = new_location_time.isoformat()

            # -----------------------------------------------------------------
            # Zone + icon
            # -----------------------------------------------------------------
            if ATTR_ZONE in trigger.attributes:
                new_zone = trigger.attributes[ATTR_ZONE].replace("zone.", "")
                new_zone_obj = pli.hass.states.get(f"{ZONE_DOMAIN}.{new_zone}")
            else:
                zone_entity_id = resolve_zone_entity_id(pli.hass, trigger.state)
                if zone_entity_id:
                    new_zone_obj = pli.hass.states.get(zone_entity_id)
                    if new_zone_obj:
                        new_zone = zone_entity_id.split(".", 1)[1]
                    else:
                        new_zone = None
                else:
                    new_zone = None
                    new_zone_obj = None

            icon = "mdi:help-circle"
            if new_zone_obj and not new_zone.startswith(IC3_STATIONARY_ZONE_PREFIX):
                new_zone_attrs = new_zone_obj.attributes
                icon = new_zone_attrs.get(ATTR_ICON, icon)
            attrs[ATTR_ICON] = icon
            _LOGGER.debug(
                "(%s) Determined new zone: %s, icon: %s",
                trigger.entity_id,
                new_zone,
                icon,
            )
            if new_zone:
                attrs[ATTR_ZONE] = new_zone
                if new_zone == STATE_HOME:
                    attrs.pop(ATTR_ZONE, None)
                    # Lock down to Home coordinates when the zone is Home
                    attrs[ATTR_LATITUDE], attrs[ATTR_LONGITUDE] = get_home_coordinates(
                        pli.hass
                    )
            else:
                attrs.pop(ATTR_ZONE, None)

            # -----------------------------------------------------------------
            # Set up something like https://philhawthorne.com/making-home-assistants-presence-detection-not-so-binary/
            # https://github.com/rodpayne/home-assistant_person_location?tab=readme-ov-file#make-presence-detection-not-so-binary
            # -----------------------------------------------------------------
            old_state = old_state.lower()
            new_state = None

            if trigger.state_home_or_not == STATE_HOME:  # Trigger is Home logic
                if (
                    old_state in [STATE_JUST_LEFT, "none"]
                    or ha_just_started
                    or pli.configuration[CONF_MINUTES_JUST_ARRIVED] == 0
                ):
                    # Initial setting at startup goes straight to Home.
                    # Just Left also goes straight back to Home.
                    # Anything else goes straight to Home if Just Arrived is not an option.

                    new_state = STATE_HOME
                    attrs[ATTR_BREAD_CRUMBS] = "Home"
                    attrs[ATTR_DIRECTION] = "home"
                    attrs[ATTR_COMPASS_BEARING] = 0
                    attrs.pop(ATTR_AWAY_TIMESTAMP, None)
                elif old_state == STATE_HOME:
                    # Already home - stay there
                    new_state = STATE_HOME
                elif old_state == STATE_JUST_ARRIVED:
                    # Already just arrived - stay there until time passes
                    new_state = STATE_JUST_ARRIVED
                else:
                    # Transitioning from away to home - use Just Arrived
                    new_state = STATE_JUST_ARRIVED
                    target.schedule_state_change(
                        from_state=STATE_JUST_ARRIVED,
                        to_state=STATE_HOME,
                        minutes=pli.configuration[CONF_MINUTES_JUST_ARRIVED],
                    )
            else:
                # trigger.state_home_or_not != STATE_HOME: Trigger is Away logic
                if old_state != STATE_NOT_HOME and (
                    old_state == "none"
                    or ha_just_started
                    or pli.configuration[CONF_MINUTES_JUST_LEFT] == 0
                ):
                    # Initial setting at startup goes straight to Away.
                    new_state = STATE_NOT_HOME
                    if pli.configuration[CONF_HOURS_EXTENDED_AWAY] != 0:
                        target.schedule_state_change(
                            from_state=STATE_NOT_HOME,
                            to_state=STATE_EXTENDED_AWAY,
                            minutes=pli.configuration[CONF_HOURS_EXTENDED_AWAY] * 60,
                        )
                elif old_state == STATE_NOT_HOME:
                    # Away stays Away until time passes
                    new_state = STATE_NOT_HOME
                elif old_state == STATE_JUST_LEFT:
                    # Just Left stays Just Left until time passes
                    new_state = STATE_JUST_LEFT
                elif old_state == STATE_EXTENDED_AWAY:
                    # Already extended away - stay there until home
                    new_state = STATE_EXTENDED_AWAY
                elif old_state in [STATE_HOME, STATE_JUST_ARRIVED]:
                    attrs[ATTR_AWAY_TIMESTAMP] = attrs[ATTR_LOCATION_TIMESTAMP]
                    if pli.configuration[CONF_MINUTES_JUST_LEFT] == 0:
                        new_state = STATE_NOT_HOME
                        if pli.configuration[CONF_HOURS_EXTENDED_AWAY] != 0:
                            target.schedule_state_change(
                                from_state=STATE_NOT_HOME,
                                to_state=STATE_EXTENDED_AWAY,
                                minutes=pli.configuration[CONF_HOURS_EXTENDED_AWAY]
                                * 60,
                            )
                    else:
                        new_state = STATE_JUST_LEFT
                        target.schedule_state_change(
                            from_state=STATE_JUST_LEFT,
                            to_state=STATE_NOT_HOME,
                            minutes=pli.configuration[CONF_MINUTES_JUST_LEFT],
                        )
                else:
                    new_state = STATE_NOT_HOME

            # Zone override when away
            if (
                new_state == STATE_NOT_HOME
                and pli.configuration[CONF_SHOW_ZONE_WHEN_AWAY]
                and new_zone_obj
                and not new_zone.startswith(IC3_STATIONARY_ZONE_PREFIX)
            ):
                friendly = new_zone_obj.attributes.get("friendly_name")
                if friendly:
                    new_state = friendly

            # Set the new state
            target._state = new_state

            if ATTR_BREAD_CRUMBS not in attrs:
                attrs[ATTR_BREAD_CRUMBS] = new_state

            # -----------------------------------------------------------------
            # Commit state atomically
            # -----------------------------------------------------------------
            await target.async_set_state()

        # ---------------------------------------------------------------------
        # Trigger reverse_geocode
        # ---------------------------------------------------------------------
        force_update = new_state in [STATE_HOME, STATE_JUST_ARRIVED] and old_state in [
            STATE_NOT_HOME,
            STATE_EXTENDED_AWAY,
            STATE_JUST_LEFT,
        ]
        if pli._attr_extra_state_attributes.get("startup"):
            force_update = True

        await pli.hass.services.async_call(
            DOMAIN,
            "reverse_geocode",
            {
                "entity_id": target.entity_id,
                "friendly_name_template": pli.configuration.get(
                    CONF_FRIENDLY_NAME_TEMPLATE,
                    DEFAULT_FRIENDLY_NAME_TEMPLATE,
                ),
                "force_update": force_update,
            },
            blocking=False,
        )

        return True

    # Register service
    pli.hass.services.async_register(DOMAIN, "process_trigger", handle_process_trigger)
    return True

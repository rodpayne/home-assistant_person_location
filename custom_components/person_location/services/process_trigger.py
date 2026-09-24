"""process_trigger.py - The person_location integration process_trigger service (async)."""

from __future__ import annotations

# pyright: reportMissingImports=false
from functools import partial
import logging
import string
from typing import TYPE_CHECKING, Any

from custom_components.person_location.helpers.entity import resolve_zone_entity_id

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
from homeassistant.exceptions import (
    ServiceValidationError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import ServiceCall, State

    from .. import PersonLocationIntegration

from ..const import (
    ATTR_ALTITUDE,
    ATTR_AWAY_TIMESTAMP,
    ATTR_BREAD_CRUMBS,
    ATTR_COMPASS_BEARING,
    ATTR_DIRECTION,
    ATTR_IN_ZONES,
    ATTR_LAST_LOCATED,
    ATTR_LOCATION_TIMESTAMP,
    ATTR_PERSON_NAME,
    ATTR_REPORTED_STATE,
    ATTR_SOURCE,
    ATTR_SPEED,
    ATTR_TRACKING_TYPE,
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
)
from ..helpers.api import get_home_coordinates
from ..helpers.timestamp import parse_ts
from ..helpers.trigger import PersonLocationTrigger
from ..sensor import PersonLocationTargetSensor, get_target_entity

_LOGGER = logging.getLogger(__name__)


def _get_trigger_location_time(trigger: PersonLocationTrigger) -> datetime:
    """Return the most recent location timestamp from the trigger."""
    raw = trigger.attributes.get(ATTR_LAST_LOCATED)
    return parse_ts(raw if raw is not None else trigger.last_updated)


def _get_trigger_source_type(
    pli: PersonLocationIntegration, trigger: PersonLocationTrigger
) -> str:
    """Return the device tracker source type reported by the trigger."""
    source_type = trigger.attributes.get(ATTR_SOURCE_TYPE)
    if source_type is not None:
        return source_type

    source = trigger.attributes.get("source")
    if not source or "." not in source:
        return "other"

    source_state = pli.hass.states.get(source)
    return (
        source_state.attributes.get(ATTR_SOURCE_TYPE, "other")
        if source_state
        else "other"
    )


def _should_save_update(
    trigger: PersonLocationTrigger,
    target: PersonLocationTargetSensor,
    trigger_from: str | None,
    trigger_to: str | None,
    trigger_source_type: str,
    old_state: str,
    ha_just_started: bool,
) -> bool:
    """Determine whether this trigger contains better information."""
    if old_state == STATE_UNKNOWN:
        _LOGGER.debug(
            "(%s) Decision: accepting the first update of %s",
            trigger.entity_id,
            target.entity_id,
        )
        return True

    if trigger_source_type != SourceType.GPS:
        if trigger_to != trigger_from and (
            trigger.state_home_or_not != trigger.derive_trigger_home_or_not(old_state)
        ):
            _LOGGER.debug(
                "(%s) Decision: non-GPS trigger has changed state %s → %s",
                trigger.entity_id,
                trigger_from,
                trigger_to,
            )
            return True
        return False

    if trigger_to != trigger_from:
        _LOGGER.debug(
            "(%s) Decision: GPS trigger has changed state %s → %s",
            trigger.entity_id,
            trigger_from,
            trigger_to,
        )
        return True

    attrs = target._attr_extra_state_attributes
    if (
        ATTR_SOURCE not in attrs
        or attrs[ATTR_SOURCE] == trigger.entity_id
        or ATTR_REPORTED_STATE not in attrs
    ):
        _LOGGER.debug(
            "(%s) Decision: continue following this GPS trigger",
            trigger.entity_id,
        )
        return True

    if (
        ATTR_LATITUDE in trigger.attributes
        and ATTR_LONGITUDE in trigger.attributes
        and ATTR_LATITUDE not in attrs
        and ATTR_LONGITUDE not in attrs
    ):
        _LOGGER.debug(
            "(%s) Decision: switch to source that has coordinates",
            trigger.entity_id,
        )
        return True

    if trigger.state == attrs.get(ATTR_REPORTED_STATE):
        if ATTR_GPS_ACCURACY in trigger.attributes:
            old_accuracy = attrs.get(ATTR_GPS_ACCURACY, 9999)
            if trigger.attributes[ATTR_GPS_ACCURACY] < old_accuracy:
                _LOGGER.debug(
                    "(%s) Decision: gps_accuracy is better than %s",
                    trigger.entity_id,
                    attrs[ATTR_SOURCE],
                )
                return True
        return False

    if (
        ha_just_started
        and ATTR_LATITUDE in trigger.attributes
        and ATTR_LONGITUDE in trigger.attributes
    ):
        _LOGGER.debug(
            "(%s) Decision: at startup, accept any GPS trigger with coordinates",
            trigger.entity_id,
        )
        return True

    return False


def _copy_trigger_attribute(
    attrs: dict[str, Any], trigger: PersonLocationTrigger, name: str
) -> None:
    """Copy a trigger attribute or remove it from the target."""
    if name in trigger.attributes:
        attrs[name] = trigger.attributes[name]
    else:
        attrs.pop(name, None)


def _copy_trigger_attributes(
    attrs: dict[str, Any], trigger: PersonLocationTrigger
) -> None:
    """Copy location attributes from the trigger to the target."""
    _copy_trigger_attribute(attrs, trigger, ATTR_SOURCE_TYPE)
    _copy_trigger_attribute(attrs, trigger, ATTR_LATITUDE)
    _copy_trigger_attribute(attrs, trigger, ATTR_LONGITUDE)
    _copy_trigger_attribute(attrs, trigger, ATTR_GPS_ACCURACY)

    if ATTR_ALTITUDE in trigger.attributes:
        try:
            attrs[ATTR_ALTITUDE] = round(trigger.attributes[ATTR_ALTITUDE])
        except Exception:
            attrs[ATTR_ALTITUDE] = trigger.attributes[ATTR_ALTITUDE]
    else:
        attrs.pop(ATTR_ALTITUDE, None)

    _copy_trigger_attribute(attrs, trigger, ATTR_VERTICAL_ACCURACY)
    _copy_trigger_attribute(attrs, trigger, ATTR_ENTITY_PICTURE)
    _copy_trigger_attribute(attrs, trigger, ATTR_SPEED)
    _copy_trigger_attribute(attrs, trigger, ATTR_IN_ZONES)
    _copy_trigger_attribute(attrs, trigger, ATTR_TRACKING_TYPE)


def _get_zone(
    pli: PersonLocationIntegration, trigger: PersonLocationTrigger
) -> tuple[str | None, State | None, str]:
    """Return the zone name, zone state, and icon for the trigger."""
    if ATTR_ZONE in trigger.attributes:
        zone_name = trigger.attributes[ATTR_ZONE].replace("zone.", "")
        zone_entity = f"{ZONE_DOMAIN}.{zone_name}"
    else:
        zone_entity = resolve_zone_entity_id(pli.hass, trigger.state)
        zone_name = zone_entity.split(".", 1)[1] if zone_entity else None

    zone_state = pli.hass.states.get(zone_entity) if zone_entity else None

    icon = "mdi:help-circle"
    if (
        zone_state
        and zone_name
        and not zone_name.startswith(IC3_STATIONARY_ZONE_PREFIX)
    ):
        icon = zone_state.attributes.get(ATTR_ICON, icon)

    return zone_name, zone_state, icon


def _update_zone_attributes(
    pli: PersonLocationIntegration,
    trigger: PersonLocationTrigger,
    attrs: dict[str, Any],
) -> tuple[str | None, State | None]:
    """Update the target's zone and icon attributes."""
    zone_name, zone_state, icon = _get_zone(pli, trigger)

    attrs[ATTR_ICON] = icon
    _LOGGER.debug(
        "(%s) Determined new zone: %s, icon: %s",
        trigger.entity_id,
        zone_name,
        icon,
    )

    if not zone_name:
        attrs.pop(ATTR_ZONE, None)
        return zone_name, zone_state

    if zone_name == STATE_HOME:
        attrs.pop(ATTR_ZONE, None)
        attrs[ATTR_LATITUDE], attrs[ATTR_LONGITUDE] = get_home_coordinates(pli.hass)
    else:
        attrs[ATTR_ZONE] = zone_name

    return zone_name, zone_state


def _schedule_extended_away(
    pli: PersonLocationIntegration, target: PersonLocationTargetSensor
) -> None:
    """Schedule the extended-away transition when configured."""
    hours = pli.configuration[CONF_HOURS_EXTENDED_AWAY]
    if hours:
        target.schedule_state_change(
            from_state=STATE_NOT_HOME,
            to_state=STATE_EXTENDED_AWAY,
            minutes=hours * 60,
        )


def _set_presence_state(
    pli: PersonLocationIntegration,
    target: PersonLocationTargetSensor,
    attrs: dict[str, Any],
    trigger: PersonLocationTrigger,
    old_state: str,
    ha_just_started: bool,
) -> str:
    """Determine and schedule the target's presence state."""
    old_state = old_state.lower()

    if trigger.state_home_or_not == STATE_HOME:
        if (
            old_state in [STATE_JUST_LEFT, "none"]
            or ha_just_started
            or pli.configuration[CONF_MINUTES_JUST_ARRIVED] == 0
        ):
            new_state = STATE_HOME
            attrs[ATTR_BREAD_CRUMBS] = "Home"
            attrs[ATTR_DIRECTION] = "home"
            attrs[ATTR_COMPASS_BEARING] = 0
            attrs.pop(ATTR_AWAY_TIMESTAMP, None)
        elif old_state == STATE_HOME:
            new_state = STATE_HOME
        elif old_state == STATE_JUST_ARRIVED:
            new_state = STATE_JUST_ARRIVED
        else:
            new_state = STATE_JUST_ARRIVED
            target.schedule_state_change(
                from_state=STATE_JUST_ARRIVED,
                to_state=STATE_HOME,
                minutes=pli.configuration[CONF_MINUTES_JUST_ARRIVED],
            )
    elif old_state != STATE_NOT_HOME and (
        old_state == "none"
        or ha_just_started
        or pli.configuration[CONF_MINUTES_JUST_LEFT] == 0
    ):
        new_state = STATE_NOT_HOME
        _schedule_extended_away(pli, target)
    elif old_state == STATE_NOT_HOME:
        new_state = STATE_NOT_HOME
    elif old_state == STATE_JUST_LEFT:
        new_state = STATE_JUST_LEFT
    elif old_state == STATE_EXTENDED_AWAY:
        new_state = STATE_EXTENDED_AWAY
    elif old_state in [STATE_HOME, STATE_JUST_ARRIVED]:
        attrs[ATTR_AWAY_TIMESTAMP] = attrs[ATTR_LOCATION_TIMESTAMP]
        if pli.configuration[CONF_MINUTES_JUST_LEFT] == 0:
            new_state = STATE_NOT_HOME
            _schedule_extended_away(pli, target)
        else:
            new_state = STATE_JUST_LEFT
            target.schedule_state_change(
                from_state=STATE_JUST_LEFT,
                to_state=STATE_NOT_HOME,
                minutes=pli.configuration[CONF_MINUTES_JUST_LEFT],
            )
    else:
        new_state = STATE_NOT_HOME

    return new_state


def _apply_zone_override(
    pli: PersonLocationIntegration,
    new_state: str,
    zone_name: str | None,
    zone_state: State | None,
) -> str:
    """Show the zone name as the state when configured."""
    if (
        new_state == STATE_NOT_HOME
        and pli.configuration[CONF_SHOW_ZONE_WHEN_AWAY]
        and zone_state
        and zone_name
        and not zone_name.startswith(IC3_STATIONARY_ZONE_PREFIX)
    ):
        friendly_name = zone_state.attributes.get("friendly_name")
        if friendly_name:
            return friendly_name

    return new_state


async def _handle_process_trigger(
    pli: PersonLocationIntegration, call: ServiceCall
) -> bool:
    """Process a location trigger and update its target sensor."""
    entity_id = call.data.get(CONF_ENTITY_ID, "NONE")
    trigger_from = call.data.get("from_state")
    trigger_to = call.data.get("to_state")

    if entity_id == "NONE":
        raise ServiceValidationError(
            f"{CONF_ENTITY_ID} is required in call of {DOMAIN}.process_trigger service."
        )

    ha_just_started = pli._attr_extra_state_attributes.get("startup", False)
    trigger = await PersonLocationTrigger(entity_id, pli).async_init()

    if trigger.entity_id == trigger.target_name:
        _LOGGER.debug(
            "(%s) Decision: skip self update: target = (%s)",
            trigger.entity_id,
            trigger.target_name,
        )
        return True

    if ATTR_GPS_ACCURACY in trigger.attributes:
        accuracy = trigger.attributes[ATTR_GPS_ACCURACY]
        if accuracy == 0 or accuracy >= 100:
            _LOGGER.debug(
                "(%s) Decision: skip due to bad GPS accuracy: %s",
                trigger.entity_id,
                accuracy,
            )
            return True

    new_location_time = _get_trigger_location_time(trigger)
    trigger_source_type = _get_trigger_source_type(pli, trigger)

    async with pli.target_lock(trigger.target_name):
        target = get_target_entity(pli, trigger.target_name)
        if not target:
            _LOGGER.warning("No target sensor found for %s", trigger.target_name)
            return False

        target.this_entity_info[INFO_TRIGGER_COUNT] += 1

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

        attrs = target._attr_extra_state_attributes
        old_location_time = parse_ts(
            attrs.get(ATTR_LOCATION_TIMESTAMP) or target.last_updated
        )
        if new_location_time < old_location_time:
            _LOGGER.debug(
                "(%s) Decision: skip stale update: %s < %s",
                trigger.entity_id,
                new_location_time,
                old_location_time,
            )
            return True

        old_state = (target._state or "").lower()
        if not _should_save_update(
            trigger,
            target,
            trigger_from,
            trigger_to,
            trigger_source_type,
            old_state,
            ha_just_started,
        ):
            _LOGGER.debug(
                "(%s) Decision: ignore this update",
                trigger.entity_id,
            )
            return True

        _LOGGER.debug(
            "(%s) Saving This Update -state: %s -attributes: %s",
            trigger.entity_id,
            trigger.state,
            trigger.attributes,
        )

        _copy_trigger_attributes(attrs, trigger)
        attrs[ATTR_SOURCE] = trigger.entity_id
        attrs[ATTR_REPORTED_STATE] = trigger.state
        attrs[ATTR_PERSON_NAME] = string.capwords(trigger.person_name)
        attrs[ATTR_LOCATION_TIMESTAMP] = new_location_time.isoformat()

        zone_name, zone_state = _update_zone_attributes(pli, trigger, attrs)
        new_state = _set_presence_state(
            pli, target, attrs, trigger, old_state, ha_just_started
        )
        new_state = _apply_zone_override(pli, new_state, zone_name, zone_state)

        target._state = new_state
        attrs.setdefault(ATTR_BREAD_CRUMBS, new_state)

        await target.async_set_state()

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


async def async_setup_process_trigger(pli: PersonLocationIntegration) -> bool:
    """Register the process_trigger service."""
    pli.hass.services.async_register(
        DOMAIN,
        "process_trigger",
        partial(_handle_process_trigger, pli),
    )
    return True

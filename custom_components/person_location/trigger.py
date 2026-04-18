"""trigger.py - Trigger entity model for the person_location integration."""

# pyright: reportMissingImports=false
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.person_location import PersonLocationIntegration
    from homeassistant.core import HomeAssistant

from homeassistant.const import (
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.util import slugify

from .const import (
    AWAY_LIKE,
    CONF_DEVICES,
    IC3_STATIONARY_STATE_PREFIX,
)
from .helpers.timestamp import parse_ts

_LOGGER = logging.getLogger(__name__)


# =====================================================================
# Trigger Sensor - Normalized metadata for a triggering entity
# =====================================================================


class PersonLocationTrigger:
    """Represents a triggering entity (device_tracker, person, sensor, etc.).

    Provides normalized metadata for process_trigger.
    """

    def __init__(self, entity_id: str, pli: PersonLocationIntegration) -> None:
        """Initialize the trigger with basic identity info."""
        # Identity only — no HA I/O here
        self.entity_id = entity_id
        self.pli = pli
        self.hass: HomeAssistant = pli.hass

        # Populated in async_init()
        self.state = STATE_UNKNOWN
        self.state_home_or_not = STATE_UNKNOWN
        self.last_changed: datetime | None = None
        self.last_updated: datetime | None = None
        self.attributes: dict = {}
        self.friendly_name = ""
        self.person_name = ""
        self.target_name = ""

        self.first_time = True

    async def async_init(self) -> PersonLocationTrigger:
        """Async initializer that loads HA state and derives metadata."""
        if not self.hass:
            _LOGGER.warning("trigger.py async_init called with self.hass empty")
            return
        else:
            _LOGGER.debug("trigger.py async_init called with self.hass available")

        state_obj = self.hass.states.get(self.entity_id)

        if state_obj:
            self.first_time = False
            self.state = self._normalize_state(state_obj.state)
            self.state_home_or_not = self.derive_trigger_home_or_not(state_obj.state)
            self.last_changed = state_obj.last_changed
            self.last_updated = state_obj.last_updated
            self.attributes = dict(state_obj.attributes)
        else:
            # Synthetic defaults for first-time entities
            self.first_time = True
            self.state = STATE_UNKNOWN
            self.state_home_or_not = STATE_UNKNOWN
            self.last_changed = parse_ts("2020-03-14T15:09:26.535897Z")
            self.last_updated = self.last_changed
            self.attributes = {}

        self.friendly_name = self.attributes.get("friendly_name", "")
        self.person_name = self._derive_person_name()
        self.target_name = self._derive_target_name()

        return self

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _normalize_state(self, raw_state: str) -> str:
        """Normalize raw trigger state into Home/Away/unknown/zone."""
        if raw_state.lower() in (STATE_HOME, STATE_ON):
            return STATE_HOME
        if raw_state == STATE_NOT_HOME:
            return STATE_NOT_HOME
        if raw_state.lower() in AWAY_LIKE:
            return STATE_NOT_HOME
        if raw_state.startswith(IC3_STATIONARY_STATE_PREFIX):
            return STATE_NOT_HOME
        if raw_state.lower() in (STATE_UNKNOWN, STATE_UNAVAILABLE, "none"):
            return STATE_UNKNOWN
        return raw_state

    def derive_trigger_home_or_not(self, state: str | None) -> str:
        """Normalize any device_tracker state into one of: 'home', 'away', or 'unknown'."""
        if state is None:
            return STATE_UNKNOWN

        # Normalize formatting
        s = state.strip().lower().replace(" ", "_")

        # Hard canonical states
        if s == STATE_HOME:
            return STATE_HOME

        if s in (STATE_NOT_HOME, STATE_NOT_HOME):
            return STATE_NOT_HOME

        # HA core fallback states
        if s in (STATE_UNKNOWN, STATE_UNAVAILABLE, "none"):
            return STATE_UNKNOWN

        if s in AWAY_LIKE:
            return STATE_NOT_HOME

        # Anything else is Away
        return STATE_NOT_HOME

    def _derive_person_name(self) -> str:
        """Determine the person name from config or attributes."""
        cfg_devices = self.pli.configuration.get(CONF_DEVICES, {})

        # Config override
        if self.entity_id in cfg_devices:
            return cfg_devices[self.entity_id].lower()

        # Attribute-based fallbacks
        for key in ("person_name", "account_name", "owner_fullname"):
            if key in self.attributes:
                val = self.attributes[key]
                if key == "owner_fullname":
                    return val.split()[0]
                return val

        # Person entity fallback
        if (
            "friendly_name" in self.attributes
            and self.entity_id.split(".")[0] == "person"
        ):
            return self.attributes["friendly_name"]

        # Last resort: derive from entity_id
        fallback = self.entity_id.split(".")[1].split("_")[0].title()
        if not self.first_time:
            _LOGGER.debug(
                'Missing person_name/account_name for %s, using "%s"',
                self.entity_id,
                fallback,
            )
        return fallback

    def _derive_target_name(self) -> str:
        """Compute the target sensor/device_tracker entity_id."""
        platform = self.pli.configuration.get("platform", "sensor")
        return f"{platform}.{slugify(self.person_name)}_location"

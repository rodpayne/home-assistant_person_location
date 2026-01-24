"""helpers/entity.py - Helpers for entity lifecycle"""

from collections.abc import Iterable
import logging
import re

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONF_CREATE_SENSORS,
    DATA_CONFIGURATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# ------- Template Entities -------------------------------------------------

# Base ends with "_location"; suffix can contain underscores
_TEMPLATE_RE = re.compile(r"^(?P<base>.+_location)_(?P<suffix>.+)_template$")


def _extract_base_and_suffix(unique_id: str) -> tuple[str, str] | None:
    if not unique_id:
        return None
    m = _TEMPLATE_RE.match(unique_id)
    if not m:
        return None
    _LOGGER.debug(
        "[_extract_base_and_suffix] base: %s, suffix: %s",
        m.group("base"),
        m.group("suffix"),
    )
    return m.group("base"), m.group("suffix")


async def prune_orphan_template_entities(
    hass: HomeAssistant,
) -> list[str]:
    """Remove template sensor entities that are no longer requested."""
    platform_domain = DOMAIN
    entity_domain = "sensor"

    # Get template sensor names that are still valid
    allowed_suffixes = hass.data[DOMAIN][DATA_CONFIGURATION][CONF_CREATE_SENSORS]

    registry = er.async_get(hass)
    allowed = set(allowed_suffixes or [])
    removed: list[str] = []

    for entity in list(registry.entities.values()):
        if entity.domain != entity_domain:
            continue
        if entity.platform != platform_domain:
            continue

        pair = _extract_base_and_suffix(entity.unique_id)
        if not pair:
            continue
        base_id, suffix = pair

        if suffix in allowed:
            continue

        # Check state before removing
        state_obj = hass.states.get(entity.entity_id)
        if state_obj is None or state_obj.state != STATE_UNAVAILABLE:
            # Entity is either active or unknown, so skip removal
            continue

        removed.append(entity.entity_id)
        registry.async_remove(entity.entity_id)

    return removed


# -------------------------------------------------------------------------------

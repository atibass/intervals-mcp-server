"""
Intervals.icu MCP Server

This module implements a Model Context Protocol (MCP) server for connecting
Claude with the Intervals.icu API. It provides tools for retrieving and managing
athlete data, including activities, events, workouts, and wellness metrics.
"""

import logging

from intervals_mcp_server.api.client import (
    httpx_client,
    make_intervals_request,
)
from intervals_mcp_server.config import get_config
from intervals_mcp_server.mcp_instance import mcp
from intervals_mcp_server.server_setup import setup_transport, start_server
from intervals_mcp_server.utils.validation import validate_athlete_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("intervals_icu_mcp_server")

config = get_config()

from intervals_mcp_server.tools.activities import (  # pylint: disable=wrong-import-position  # noqa: E402
    add_activity_message,
    get_activities,
    get_activity_details,
    get_activity_intervals,
    get_activity_messages,
    get_activity_streams as _get_activity_streams,
)
from intervals_mcp_server.tools.events import (  # pylint: disable=wrong-import-position  # noqa: E402
    add_or_update_event,
    delete_event,
    delete_events_by_date_range,
    get_event_by_id,
    get_events,
)
from intervals_mcp_server.tools.gear import get_gear_list  # pylint: disable=wrong-import-position  # noqa: E402
from intervals_mcp_server.tools.wellness import get_wellness_data  # pylint: disable=wrong-import-position  # noqa: E402
from intervals_mcp_server.tools.power_curves import get_athlete_power_curves  # pylint: disable=wrong-import-position  # noqa: E402
from intervals_mcp_server.tools.custom_items import (  # pylint: disable=wrong-import-position  # noqa: E402
    create_custom_item,
    delete_custom_item,
    get_custom_item_by_id,
    get_custom_items,
    update_custom_item,
)

_ALLOWED_STREAM_TYPES = (
    "time",
    "watts",
    "heartrate",
    "cadence",
    "altitude",
    "distance",
    "core_temperature",
    "skin_temperature",
    "velocity_smooth",
)
_ALLOWED_STREAM_TYPE_SET = set(_ALLOWED_STREAM_TYPES)

try:
    mcp.remove_tool("get_activity_streams")
except Exception:
    logger.debug("Upstream get_activity_streams tool was not registered")


@mcp.tool()
async def get_activity_streams(
    activity_id: str,
    api_key: str | None = None,
    stream_types: str | None = None,
) -> str:
    """Get supported activity streams while silently dropping unsupported names."""
    sanitized_stream_types: str | None = None

    if stream_types:
        requested = [item.strip() for item in stream_types.split(",") if item.strip()]
        sanitized = [item for item in requested if item in _ALLOWED_STREAM_TYPE_SET]

        if not sanitized:
            return (
                "Error: no supported stream types requested. Supported types: "
                + ",".join(_ALLOWED_STREAM_TYPES)
            )

        sanitized_stream_types = ",".join(sanitized)

    return await _get_activity_streams(
        activity_id=activity_id,
        api_key=api_key,
        stream_types=sanitized_stream_types,
    )


_WRITE_TOOLS = (
    "add_activity_message",
    "add_or_update_event",
    "add_or_update_note",
    "delete_event",
    "delete_events_by_date_range",
    "create_custom_item",
    "update_custom_item",
    "delete_custom_item",
)

for _tool_name in _WRITE_TOOLS:
    try:
        mcp.remove_tool(_tool_name)
    except Exception:
        logger.debug("Write tool not registered: %s", _tool_name)

_exposed_tools = [tool.name for tool in mcp._tool_manager.list_tools()]  # pylint: disable=protected-access
logger.info("Intervals.icu MCP started in read-only mode")
logger.info("MCP exposed tools: %s", _exposed_tools)

__all__ = [
    "make_intervals_request",
    "httpx_client",
    "add_activity_message",
    "get_activities",
    "get_activity_details",
    "get_activity_intervals",
    "get_activity_messages",
    "get_activity_streams",
    "get_events",
    "get_event_by_id",
    "delete_event",
    "delete_events_by_date_range",
    "add_or_update_event",
    "get_wellness_data",
    "get_athlete_power_curves",
    "get_custom_items",
    "get_custom_item_by_id",
    "create_custom_item",
    "update_custom_item",
    "delete_custom_item",
]


if __name__ == "__main__":
    validate_athlete_id(config.athlete_id)
    selected_transport = setup_transport()
    start_server(mcp, selected_transport)

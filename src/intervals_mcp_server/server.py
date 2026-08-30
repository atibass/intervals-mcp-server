"""
Intervals.icu MCP Server

This module implements a Model Context Protocol (MCP) server for connecting
clients with the Intervals.icu API. The deployed server is intentionally
read-only.
"""

import logging
from typing import Any

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
_DEFAULT_STREAM_TYPES = (
    "time",
    "watts",
    "heartrate",
    "cadence",
    "altitude",
    "distance",
    "velocity_smooth",
)

# Replace the upstream preview-oriented stream tool with a raw structured tool.
try:
    mcp.remove_tool("get_activity_streams")
except Exception:
    logger.debug("Upstream get_activity_streams tool was not registered")


@mcp.tool()
async def get_activity_streams(
    activity_id: str,
    api_key: str | None = None,
    stream_types: str | None = None,
) -> dict[str, Any] | str:
    """Return full raw activity stream arrays from Intervals.icu.

    Unsupported stream names are silently dropped before the upstream request.
    Each returned stream preserves the Intervals.icu response, including its
    complete ``data`` array; values are not sampled, summarized, or truncated by
    this MCP server.

    Supported stream types: time, watts, heartrate, cadence, altitude, distance,
    core_temperature, skin_temperature, velocity_smooth.
    """
    if stream_types:
        requested = [item.strip() for item in stream_types.split(",") if item.strip()]
        sanitized = [item for item in requested if item in _ALLOWED_STREAM_TYPE_SET]

        if not sanitized:
            return (
                "Error: no supported stream types requested. Supported types: "
                + ",".join(_ALLOWED_STREAM_TYPES)
            )
        selected_types = sanitized
    else:
        selected_types = list(_DEFAULT_STREAM_TYPES)

    result = await make_intervals_request(
        url=f"/activity/{activity_id}/streams",
        api_key=api_key,
        params={"types": ",".join(selected_types)},
    )

    if isinstance(result, dict) and "error" in result:
        error_message = result.get("message", "Unknown error")
        return f"Error fetching activity streams: {error_message}"

    if not result:
        return f"No stream data found for activity {activity_id}."

    if not isinstance(result, list):
        return f"Unexpected stream response format for activity {activity_id}."

    # Preserve every stream object exactly as returned by Intervals.icu so the
    # caller receives the complete raw arrays rather than a 5+5 value preview.
    return {
        "activity_id": activity_id,
        "requested_stream_types": selected_types,
        "streams": result,
    }


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

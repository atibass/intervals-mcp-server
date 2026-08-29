"""
Intervals.icu MCP Server

This module implements a Model Context Protocol (MCP) server for connecting
Claude with the Intervals.icu API. It provides tools for retrieving and managing
athlete data, including activities, events, workouts, and wellness metrics.
"""

import logging

# Import API client and configuration
from intervals_mcp_server.api.client import (
    httpx_client,  # Re-export for backward compatibility with tests
    make_intervals_request,
)
from intervals_mcp_server.config import get_config
from intervals_mcp_server.mcp_instance import mcp

# Import types and validation
from intervals_mcp_server.server_setup import setup_transport, start_server
from intervals_mcp_server.utils.validation import validate_athlete_id

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("intervals_icu_mcp_server")

# Get configuration instance
config = get_config()

# Import tool modules to register them (tools register themselves via @mcp.tool() decorators)
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

# Intervals.icu rejects unsupported stream names (for example `temperature`) with
# HTTP 422. Replace the upstream registration with a read-only wrapper that only
# forwards stream types known to be supported by this deployment.
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
    """Get supported activity streams while silently dropping unsupported names.

    Supported stream types are: time, watts, heartrate, cadence, altitude,
    distance, core_temperature, skin_temperature, velocity_smooth.
    Unsupported names such as `temperature` are removed before the API request.
    """
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


# This deployment is intentionally read-only. Some write tools are registered as
# a side-effect of importing their modules, so remove them from FastMCP's public
# registry after registration and before serving any client requests.
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
    except Exception:  # Tool may not exist in every upstream revision.
        logger.debug("Write tool not registered: %s", _tool_name)

logger.info("Intervals.icu MCP started in read-only mode")

# Re-export make_intervals_request and httpx_client for backward compatibility.
# Functions remain importable for upstream compatibility, but write functions are
# not exposed as MCP tools.
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

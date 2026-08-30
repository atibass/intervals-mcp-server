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


def _select_stream_types(stream_types: str | None) -> list[str] | str:
    """Sanitize a comma-separated stream list without forwarding invalid names."""
    if not stream_types:
        return list(_DEFAULT_STREAM_TYPES)

    requested = [item.strip() for item in stream_types.split(",") if item.strip()]
    sanitized = [item for item in requested if item in _ALLOWED_STREAM_TYPE_SET]
    if not sanitized:
        return (
            "Error: no supported stream types requested. Supported types: "
            + ",".join(_ALLOWED_STREAM_TYPES)
        )
    return sanitized


async def _fetch_activity_streams(
    activity_id: str,
    api_key: str | None,
    selected_types: list[str],
) -> list[dict[str, Any]] | str:
    """Fetch supported stream objects from Intervals.icu."""
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

    return [stream for stream in result if isinstance(stream, dict)]


# Replace the upstream preview-oriented stream tool with raw structured tools.
try:
    mcp.remove_tool("get_activity_streams")
except Exception:
    logger.debug("Upstream get_activity_streams tool was not registered")


@mcp.tool()
async def get_activity_stream_types(
    activity_id: str,
    api_key: str | None = None,
) -> dict[str, Any] | str:
    """Discover available supported stream types for an activity without returning raw arrays.

    Returns compact metadata such as stream type, display name, value type,
    total data-point count, and non-null data-point count. Use this first for
    long activities, then call get_activity_streams with offset/limit to page
    through only the raw data needed for analysis.
    """
    selected_types = list(_ALLOWED_STREAM_TYPES)
    streams = await _fetch_activity_streams(activity_id, api_key, selected_types)
    if isinstance(streams, str):
        return streams

    available: list[dict[str, Any]] = []
    returned_types: set[str] = set()

    for stream in streams:
        stream_type = str(stream.get("type", "unknown"))
        returned_types.add(stream_type)
        data = stream.get("data", [])
        data_list = data if isinstance(data, list) else []
        metadata = {key: value for key, value in stream.items() if key != "data"}
        metadata["data_points"] = len(data_list)
        metadata["non_null_points"] = sum(value is not None for value in data_list)
        available.append(metadata)

    return {
        "activity_id": activity_id,
        "available_streams": available,
        "available_stream_types": [item.get("type", "unknown") for item in available],
        "not_returned_stream_types": [
            item for item in selected_types if item not in returned_types
        ],
    }


@mcp.tool()
async def get_activity_streams(
    activity_id: str,
    api_key: str | None = None,
    stream_types: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any] | str:
    """Return raw activity stream arrays from Intervals.icu, optionally paged by index.

    By default (offset=0, limit=None) this returns the complete raw arrays exactly
    as before. For long activities, set offset and limit to return only a chunk of
    each stream. Chunking is performed after retrieval from Intervals.icu and is
    intended to keep MCP responses and model context manageable.

    Unsupported stream names are silently dropped before the upstream request.
    Supported stream types: time, watts, heartrate, cadence, altitude, distance,
    core_temperature, skin_temperature, velocity_smooth.

    Args:
        activity_id: Intervals.icu activity ID.
        api_key: Optional API key; configured API key is used when omitted.
        stream_types: Optional comma-separated supported stream names.
        offset: Zero-based raw array index to start from. Defaults to 0.
        limit: Maximum points per stream to return. None returns all remaining points.
    """
    if offset < 0:
        return "Error: offset must be >= 0."
    if limit is not None and limit <= 0:
        return "Error: limit must be > 0 when provided."

    selected = _select_stream_types(stream_types)
    if isinstance(selected, str):
        return selected

    streams = await _fetch_activity_streams(activity_id, api_key, selected)
    if isinstance(streams, str):
        return streams

    paged_streams: list[dict[str, Any]] = []
    max_total_points = 0
    max_returned_points = 0

    for stream in streams:
        data = stream.get("data", [])
        if not isinstance(data, list):
            paged_streams.append(stream)
            continue

        total_points = len(data)
        max_total_points = max(max_total_points, total_points)
        end_index = total_points if limit is None else min(total_points, offset + limit)
        chunk = data[offset:end_index] if offset < total_points else []
        max_returned_points = max(max_returned_points, len(chunk))

        paged_stream = dict(stream)
        paged_stream["data"] = chunk
        paged_stream["total_data_points"] = total_points
        paged_stream["returned_offset"] = offset
        paged_stream["returned_data_points"] = len(chunk)
        paged_streams.append(paged_stream)

    if limit is None:
        next_offset = None
        has_more = False
    else:
        next_offset_candidate = offset + max_returned_points
        has_more = next_offset_candidate < max_total_points
        next_offset = next_offset_candidate if has_more else None

    return {
        "activity_id": activity_id,
        "requested_stream_types": selected,
        "pagination": {
            "offset": offset,
            "limit": limit,
            "max_total_data_points": max_total_points,
            "max_returned_data_points": max_returned_points,
            "has_more": has_more,
            "next_offset": next_offset,
        },
        "streams": paged_streams,
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
    "get_activity_stream_types",
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

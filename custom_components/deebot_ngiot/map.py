"""Map raster decoding and SVG rendering."""

import base64
import logging
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from deebot_client.events.base import Event

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MapRasterEvent(Event):
    """Decoded map raster."""

    map_id: str
    charge_pos: Any
    total_width: int | None
    total_height: int | None
    direction: int
    width: int
    height: int
    resolution: int
    pixels: bytes
    x_min: float
    y_max: float


@dataclass(frozen=True)
class RobotPositionEvent(Event):
    """Robot position in map coordinates."""

    map_id: str
    x: float
    y: float
    angle: float
    break_before: bool = False


_BACKGROUND = 127
_FALLBACK_COLOR = "#73808a"
_COLORS = {
    0: "#eef2f4",
    1: "#ffffff",
    2: "#5d6872",
    3: "#c5cdd2",
    4: "#8d9aa3",
    5: "#424c54",
    6: "#aeb9c0",
    7: "#dce4e8",
    8: "#303a42",
    9: "#97a5ae",
    10: "#e9ad52",
    255: "#d64d5d",
}


def _decompress_lz4_block(
    data: bytes, expected_size: int, *, allow_short: bool = False
) -> bytes:
    """Decompress a raw LZ4 block without a frame or size header."""
    output = bytearray()
    index = 0

    def read_length(length: int) -> int:
        nonlocal index
        while True:
            extra = data[index]
            index += 1
            length += extra
            if extra != 255:
                return length

    while index < len(data):
        token = data[index]
        index += 1
        literal_length = token >> 4
        if literal_length == 15:
            literal_length = read_length(literal_length)
        if index + literal_length > len(data):
            raise ValueError("Truncated LZ4 literals")
        output.extend(data[index : index + literal_length])
        index += literal_length
        if index >= len(data):
            break
        if index + 2 > len(data):
            raise ValueError("Truncated LZ4 match offset")
        offset = int.from_bytes(data[index : index + 2], "little")
        index += 2
        if offset == 0 or offset > len(output):
            raise ValueError("Invalid LZ4 match offset")
        match_length = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            match_length = read_length(match_length)
        for _ in range(match_length):
            output.append(output[-offset])
    if len(output) > expected_size or (len(output) < expected_size and not allow_short):
        raise ValueError(
            f"Invalid LZ4 output size {len(output)}, expected {expected_size}"
        )
    return bytes(output)


def _decompress_map_trace(data: bytes, expected_size: int) -> bytes:
    """Decompress a trace block whose declared buffer can exceed its contents."""
    return _decompress_lz4_block(data, expected_size, allow_short=True)


def decode_map_raster(map_data: dict[str, Any]) -> MapRasterEvent | None:
    """Decode the LZ4 compressed map raster."""
    encoded = map_data.get("map")
    width = map_data.get("width")
    height = map_data.get("height")
    compressed_length = map_data.get("lz4Len")
    total_width = map_data.get("totalWidth")
    total_height = map_data.get("totalHeight")
    direction = map_data.get("direction", 1)
    resolution = map_data.get("resolution", 1)
    if not isinstance(encoded, str) or not all(
        isinstance(value, int) and value > 0
        for value in (width, height, compressed_length)
    ):
        return None
    try:
        pixels = _decompress_lz4_block(base64.b64decode(encoded), compressed_length)
    except Exception:
        _LOGGER.exception("Unable to decode map raster")
        return None
    if len(pixels) != width * height:
        _LOGGER.error(
            "Map raster has %s pixels, expected %s", len(pixels), width * height
        )
        return None
    return MapRasterEvent(
        map_id=str(map_data.get("mapId", "")),
        charge_pos=map_data.get("chargePos"),
        total_width=(
            total_width
            if isinstance(total_width, int) and total_width > 0
            else None
        ),
        total_height=(
            total_height
            if isinstance(total_height, int) and total_height > 0
            else None
        ),
        direction=direction if isinstance(direction, int) else 1,
        width=width,
        height=height,
        resolution=(
            resolution
            if isinstance(resolution, int) and resolution > 0
            else 1
        ),
        pixels=pixels,
        x_min=float(map_data.get("xMin", 0)),
        y_max=float(map_data.get("yMax", height)),
    )


@dataclass(frozen=True)
class MapTracePointsEvent(Event):
    """Decoded path points from an NGIOT map trace update."""

    map_id: str
    trace_id: str
    start: int
    total_count: int
    points: tuple[RobotPositionEvent, ...]


def decode_map_trace_data(
    trace_data: dict[str, Any], map_id: str
) -> MapTracePointsEvent | None:
    """Decode the packed points in an NGIOT map trace update."""
    encoded = trace_data.get("trace")
    expected_size = trace_data.get("lz4Len")
    start = trace_data.get("start", 0)
    total_count = trace_data.get("totalCount", 0)
    if (
        not isinstance(encoded, str)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or not isinstance(start, int)
        or start < 0
        or not isinstance(total_count, int)
        or total_count < 0
    ):
        return None

    trace_map_id = str(trace_data.get("mapId", map_id))
    try:
        decoded = _decompress_map_trace(
            base64.b64decode(encoded, validate=True), expected_size
        )
        if not decoded or len(decoded) % 5:
            raise ValueError(f"Invalid map trace data length {len(decoded)}")
        # The fifth byte's meaning is not established by the available traces.
        points = tuple(
            RobotPositionEvent(trace_map_id, float(x), float(y), 0)
            for x, y, _ in struct.iter_unpack("<hhB", decoded)
        )
    except (IndexError, ValueError):
        _LOGGER.exception("Unable to decode map trace")
        return None

    return MapTracePointsEvent(
        trace_map_id,
        str(trace_data.get("traceId", "")),
        start,
        total_count,
        points,
    )


def parse_robot_position(position: Any, map_id: str) -> RobotPositionEvent | None:
    """Parse the position object returned by the map endpoint."""
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        angle = position.get("a", position.get("angle", 0))
    elif isinstance(position, (list, tuple)) and len(position) >= 2:
        x, y = position[:2]
        angle = position[2] if len(position) > 2 else 0
    else:
        return None
    if not all(isinstance(value, (int, float)) for value in (x, y, angle)):
        return None
    return RobotPositionEvent(map_id, float(x), float(y), float(angle))


def _position_to_map_coordinates(
    raster: MapRasterEvent, position: RobotPositionEvent
) -> tuple[float, float]:
    """Translate centered robot coordinates to raster cells."""
    x_offset = (raster.total_width or 0) / 2
    map_x = position.x / raster.resolution + x_offset - raster.x_min
    if raster.total_height is None:
        map_y = raster.y_max - position.y / raster.resolution
    else:
        map_y = raster.y_max - (
            raster.total_height / 2 - position.y / raster.resolution
        )
    if raster.direction < 0:
        map_y = raster.height - 1 - map_y
    return map_x, map_y


def room_id_at(raster: MapRasterEvent, position: RobotPositionEvent) -> int | None:
    """Return the raster value under the robot, which identifies the room."""
    if position.map_id != raster.map_id:
        return None
    map_x, map_y = _position_to_map_coordinates(raster, position)
    column = int(map_x)
    row = int(map_y)
    if not (0 <= column < raster.width and 0 <= row < raster.height):
        return None
    value = raster.pixels[row * raster.width + column]
    return None if value == _BACKGROUND else value


def render_svg(
    raster: MapRasterEvent,
    position: RobotPositionEvent | None = None,
    path: Sequence[RobotPositionEvent | None] = (),
) -> bytes:
    """Render the raster, robot position, and cleaning path as SVG."""
    pixel_size = raster.resolution
    rects: list[str] = []
    for y in range(raster.height):
        row_start = y * raster.width
        x = 0
        while x < raster.width:
            value = raster.pixels[row_start + x]
            end = x + 1
            while end < raster.width and raster.pixels[row_start + end] == value:
                end += 1
            if value != _BACKGROUND:
                rects.append(
                    f'<rect x="{x * pixel_size}" y="{y * pixel_size}" '
                    f'width="{(end - x) * pixel_size}" height="{pixel_size}" '
                    f'fill="{_COLORS.get(value, _FALLBACK_COLOR)}"/>'
                )
            x = end
    width = raster.width * pixel_size
    height = raster.height * pixel_size
    path_segments: list[str] = []
    path_points: list[str] = []
    for point in path:
        if point is None or point.map_id != raster.map_id:
            if len(path_points) > 1:
                path_segments.append(" ".join(path_points))
            path_points = []
            continue
        if point.break_before:
            if len(path_points) > 1:
                path_segments.append(" ".join(path_points))
            path_points = []
        map_x, map_y = _position_to_map_coordinates(raster, point)
        path_points.append(f"{map_x * pixel_size},{map_y * pixel_size}")
    if len(path_points) > 1:
        path_segments.append(" ".join(path_points))
    trace = "".join(
        (
            f'<polyline points="{points}" fill="none" stroke="#ffffff" '
            'stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<polyline points="{points}" fill="none" stroke="#1976d2" '
            'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        for points in path_segments
    )
    home_marker = ""
    if home_position := parse_robot_position(raster.charge_pos, raster.map_id):
        map_x, map_y = _position_to_map_coordinates(raster, home_position)
        home_x = map_x * pixel_size
        home_y = map_y * pixel_size
        home_marker = (
            f'<g transform="translate({home_x} {home_y})" '
            'role="img" aria-label="Home position">'
            "<title>Home position</title>"
            '<circle r="15" fill="#ffffff" stroke="#d64d5d" stroke-width="3"/>'
            '<path d="M -8 -1 L 0 -8 L 8 -1 V 7 H 2 V 2 H -2 V 7 H -8 Z" '
            'fill="#d64d5d"/>'
            "</g>"
        )
    marker = ""
    if position and position.map_id == raster.map_id:
        map_x, map_y = _position_to_map_coordinates(raster, position)
        marker_x = map_x * pixel_size
        marker_y = map_y * pixel_size
        marker = (
            f'<g transform="translate({marker_x} {marker_y})" '
            'role="img" aria-label="Robot vacuum position">'
            "<title>Robot vacuum position</title>"
            '<circle r="12" fill="#1976d2" stroke="#ffffff" stroke-width="3"/>'
            "</g>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}"><rect width="100%" height="100%" '
        f'fill="#7f7f7f"/>{"".join(rects)}{trace}{home_marker}{marker}</svg>'
    ).encode()

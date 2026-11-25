"""Tests for NGIOT map trace decoding."""

import base64
import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import patch


def load_map_module():
    """Load map decoding without requiring the Home Assistant runtime."""
    base = types.ModuleType("deebot_client.events.base")
    base.Event = object
    modules = {
        "deebot_client": types.ModuleType("deebot_client"),
        "deebot_client.events": types.ModuleType("deebot_client.events"),
        "deebot_client.events.base": base,
    }
    path = Path(__file__).parents[1] / "custom_components/deebot_ngiot/map.py"
    spec = importlib.util.spec_from_file_location("_test_deebot_ngiot_map", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


map_module = load_map_module()


def literal_trace(data):
    """Encode short literal-only raw LZ4 blocks."""
    if len(data) >= 15:
        return base64.b64encode(bytes([0xF0, len(data) - 15]) + data).decode()
    return base64.b64encode(bytes([len(data) << 4]) + data).decode()


class MapTraceTests(unittest.TestCase):
    def test_multiple_points_and_overestimated_buffer(self):
        raw = b"".join(
            struct.pack("<hhB", x, y, attr)
            for x, y, attr in [(-16, 250, 232), (-1, 247, 0), (4, 237, 0)]
        )
        event = map_module.decode_map_trace_data(
            {
                "mapId": 2,
                "traceId": "clean",
                "start": 1,
                "totalCount": 5,
                "lz4Len": 24,
                "trace": literal_trace(raw),
            },
            "2",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.start, 1)
        self.assertEqual(
            [(p.map_id, p.x, p.y, p.break_before) for p in event.points],
            [("2", -16, 250, False), ("2", -1, 247, False), ("2", 4, 237, False)],
        )

    def test_rejects_truncated_point_without_padding(self):
        with self.assertLogs(map_module.__name__, level="ERROR"):
            event = map_module.decode_map_trace_data(
                {"trace": literal_trace(b"\x01\x00\x02\x00"), "lz4Len": 8},
                "2",
            )
        self.assertIsNone(event)

    def test_raster_decompression_still_requires_exact_size(self):
        with self.assertRaises(ValueError):
            map_module._decompress_lz4_block(b"\x10\x01", 2)

    def test_lz4_back_reference_and_invalid_offset(self):
        self.assertEqual(
            map_module._decompress_lz4_block(b"\x10A\x01\x00", 5), b"AAAAA"
        )
        with self.assertRaises(ValueError):
            map_module._decompress_lz4_block(b"\x00\x00\x00", 4)

    def test_raster_decode_checks_dimensions(self):
        data = {
            "map": literal_trace(bytes([127, 1, 1, 2, 3, 4])),
            "mapId": 2,
            "width": 3,
            "height": 2,
            "lz4Len": 6,
            "resolution": 10,
        }
        raster = map_module.decode_map_raster(data)
        self.assertEqual(raster.map_id, "2")
        self.assertEqual(raster.pixels, bytes([127, 1, 1, 2, 3, 4]))
        self.assertEqual(raster.resolution, 10)
        with self.assertLogs(map_module.__name__, level="ERROR"):
            self.assertIsNone(map_module.decode_map_raster({**data, "width": 4}))
        self.assertIsNone(map_module.decode_map_raster({**data, "width": 0}))

    def test_position_parsing_and_room_lookup_with_y_flip(self):
        raster = map_module.MapRasterEvent(
            map_id="2", charge_pos=None, total_width=6, total_height=4,
            direction=1, width=3, height=2, resolution=10,
            pixels=bytes([127, 1, 1, 2, 3, 4]), x_min=2, y_max=2,
        )
        position = map_module.parse_robot_position({"x": 0, "y": 0, "a": 90}, "2")
        self.assertEqual((position.x, position.y, position.angle), (0, 0, 90))
        self.assertEqual(map_module.room_id_at(raster, position), 1)
        flipped = map_module.MapRasterEvent(**{**raster.__dict__, "direction": -1})
        self.assertEqual(map_module.room_id_at(flipped, position), 3)
        self.assertIsNone(map_module.room_id_at(raster, map_module.RobotPositionEvent("other", 0, 0, 0)))
        self.assertIsNone(map_module.parse_robot_position({"x": "bad", "y": 0}, "2"))

    def test_svg_renders_room_home_robot_and_disconnected_paths(self):
        raster = map_module.MapRasterEvent(
            map_id="2", charge_pos={"x": 0, "y": 0}, total_width=None,
            total_height=None, direction=1, width=3, height=2, resolution=1,
            pixels=bytes([127, 1, 1, 2, 3, 4]), x_min=0, y_max=2,
        )
        first = map_module.RobotPositionEvent("2", 0, 1, 0)
        second = map_module.RobotPositionEvent("2", 1, 1, 0)
        svg = map_module.render_svg(raster, second, [first, second, None, first]).decode()
        self.assertIn('width="3" height="2"', svg)
        self.assertIn('aria-label="Home position"', svg)
        self.assertIn('aria-label="Robot vacuum position"', svg)
        self.assertEqual(svg.count("<polyline"), 2)


if __name__ == "__main__":
    unittest.main()

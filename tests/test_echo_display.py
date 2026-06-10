"""Unit tests for lcm_tools.display.echo_display nested struct formatting."""

from __future__ import annotations

from lcm_tools.display.echo_display import _extract_fields, _format_value


# ---------------------------------------------------------------------------
# Mock LCM struct classes (mimic lcm-gen generated output using __slots__)
# ---------------------------------------------------------------------------


class Point:
    __slots__ = ["x", "y", "z"]

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class Pose:
    __slots__ = ["position", "orientation"]

    def __init__(self, pos: Point, ori: Point) -> None:
        self.position = pos
        self.orientation = ori


class SensorReading:
    __slots__ = ["name", "values"]

    def __init__(self, name: str, values: list[float]) -> None:
        self.name = name
        self.values = values


class ComplexMsg:
    __slots__ = ["timestamp", "pose", "readings", "labels"]

    def __init__(self) -> None:
        self.timestamp = 1000
        self.pose = Pose(Point(1.0, 2.0, 3.0), Point(0.0, 0.1, 0.0))
        self.readings = [
            SensorReading("temp", [25.0, 26.0]),
            SensorReading("hum", [60.0, 65.0]),
        ]
        self.labels = ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _extract_fields tests
# ---------------------------------------------------------------------------


class TestExtractFields:
    def test_slots_based_class(self) -> None:
        p = Point(1.0, 2.0, 3.0)
        fields = _extract_fields(p)
        assert fields == [("x", 1.0), ("y", 2.0), ("z", 3.0)]

    def test_nested_class(self) -> None:
        pose = Pose(Point(1.0, 2.0, 3.0), Point(4.0, 5.0, 6.0))
        fields = _extract_fields(pose)
        assert len(fields) == 2
        assert fields[0][0] == "position"
        assert isinstance(fields[0][1], Point)
        assert fields[1][0] == "orientation"

    def test_dict_based_object(self) -> None:
        """Objects without __slots__ fall back to dir()."""

        class DynamicObj:
            def __init__(self) -> None:
                self.a = 1
                self.b = "hello"

        obj = DynamicObj()
        fields = _extract_fields(obj)
        names = [f[0] for f in fields]
        assert "a" in names
        assert "b" in names


# ---------------------------------------------------------------------------
# _format_value tests
# ---------------------------------------------------------------------------


class TestFormatValue:
    def test_primitive_int(self) -> None:
        assert _format_value(42, indent=1) == "42"

    def test_primitive_float(self) -> None:
        assert _format_value(3.14, indent=1) == "3.14"

    def test_primitive_string(self) -> None:
        assert _format_value("hello", indent=1) == "'hello'"

    def test_primitive_list(self) -> None:
        assert _format_value([1, 2, 3], indent=1) == "[1, 2, 3]"

    def test_empty_list(self) -> None:
        assert _format_value([], indent=1) == "[]"

    def test_empty_dict(self) -> None:
        assert _format_value({}, indent=1) == "{}"

    def test_nested_struct(self) -> None:
        p = Point(1.0, 2.0, 3.0)
        result = _format_value(p, indent=1)
        assert "x: 1.0" in result
        assert "y: 2.0" in result
        assert "z: 3.0" in result
        # Should have indentation
        lines = result.split("\n")
        assert len(lines) == 4  # newline + 3 fields

    def test_deeply_nested_struct(self) -> None:
        pose = Pose(Point(1.0, 2.0, 3.0), Point(4.0, 5.0, 6.0))
        result = _format_value(pose, indent=1)
        assert "position:" in result
        assert "orientation:" in result
        assert "x: 1.0" in result
        assert "x: 4.0" in result

    def test_list_of_structs(self) -> None:
        readings = [
            SensorReading("temp", [25.0]),
            SensorReading("hum", [60.0]),
        ]
        result = _format_value(readings, indent=1)
        assert "[0]:" in result
        assert "[1]:" in result
        assert "name: 'temp'" in result
        assert "name: 'hum'" in result

    def test_complex_message_full(self) -> None:
        msg = ComplexMsg()
        fields = _extract_fields(msg)
        lines = []
        for k, v in fields:
            formatted = _format_value(v, indent=1)
            lines.append(f"  {k}: {formatted}")
        output = "\n".join(lines)

        # Verify all top-level fields are present
        assert "timestamp: 1000" in output
        assert "pose:" in output
        assert "readings:" in output
        assert "labels: ['a', 'b', 'c']" in output

        # Verify nested struct expansion
        assert "position:" in output
        assert "orientation:" in output
        assert "x: 1.0" in output

        # Verify list of structs expansion
        assert "[0]:" in output
        assert "name: 'temp'" in output
        assert "name: 'hum'" in output

"""Unit tests for lcm_tools.core.lcm_type_builder."""

from __future__ import annotations

import struct as _struct

import pytest

from lcm_tools.core.lcm_type_builder import TypeRegistry


# ---------------------------------------------------------------------------
# Helper: create a registry from inline .lcm text
# ---------------------------------------------------------------------------


def _registry_from_text(*texts: str) -> TypeRegistry:
    """Create a TypeRegistry from one or more .lcm file contents."""
    import tempfile
    import os

    reg = TypeRegistry()
    tmpdir = tempfile.mkdtemp()
    for i, text in enumerate(texts):
        path = os.path.join(tmpdir, f"type_{i}.lcm")
        with open(path, "w") as f:
            f.write(text)
        reg.register_file(path)
    return reg


# ---------------------------------------------------------------------------
# TypeRegistry tests
# ---------------------------------------------------------------------------


class TestTypeRegistry:
    def test_register_single_file(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                int32_t x;
            }
        """)
        assert "test.msg_t" in reg.all_types

    def test_find_by_name_short(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        cls = reg.find_by_name("msg_t")
        assert cls is not None
        assert cls.__name__ == "msg_t"

    def test_find_by_name_full(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        cls = reg.find_by_name("test.msg_t")
        assert cls is not None

    def test_find_by_fingerprint(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        cls = reg.find_by_name("msg_t")
        fp_bytes = cls._get_packed_fingerprint()
        fp_int = int.from_bytes(fp_bytes, "big")
        found = reg.find_by_fingerprint(fp_int)
        assert found is cls

    def test_register_dir(self) -> None:
        reg = TypeRegistry()
        reg.register_dir("lcm_ref/examples/types/")
        types = reg.all_types
        assert "exlcm.example_t" in types
        assert "exlcm.node_t" in types
        assert "exlcm.example_list_t" in types


# ---------------------------------------------------------------------------
# Dynamic class tests
# ---------------------------------------------------------------------------


class TestDynamicClasses:
    def test_init_defaults(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                int32_t x;
                double y;
                string name;
                boolean flag;
            }
        """)
        cls = reg.find_by_name("msg_t")
        obj = cls()
        assert obj.x == 0
        assert obj.y == 0.0
        assert obj.name == ""
        assert obj.flag is False

    def test_fixed_array_init(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                double pos[3];
            }
        """)
        cls = reg.find_by_name("msg_t")
        obj = cls()
        assert obj.pos == [0.0, 0.0, 0.0]

    def test_encode_decode_roundtrip_simple(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                int64_t timestamp;
                double value;
                int32_t count;
                string name;
                boolean enabled;
            }
        """)
        cls = reg.find_by_name("msg_t")
        msg = cls()
        msg.timestamp = 123456789
        msg.value = 3.14
        msg.count = 42
        msg.name = "hello"
        msg.enabled = True

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.timestamp == 123456789
        assert abs(decoded.value - 3.14) < 1e-10
        assert decoded.count == 42
        assert decoded.name == "hello"
        assert decoded.enabled is True

    def test_encode_decode_fixed_array(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                double position[3];
                float orientation[4];
            }
        """)
        cls = reg.find_by_name("msg_t")
        msg = cls()
        msg.position = [1.0, 2.0, 3.0]
        msg.orientation = [0.1, 0.2, 0.3, 0.4]

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.position == [1.0, 2.0, 3.0]
        for i in range(4):
            assert abs(decoded.orientation[i] - [0.1, 0.2, 0.3, 0.4][i]) < 1e-6

    def test_encode_decode_variable_array(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                int32_t n;
                int16_t data[n];
            }
        """)
        cls = reg.find_by_name("msg_t")
        msg = cls()
        msg.n = 5
        msg.data = [10, 20, 30, 40, 50]

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.n == 5
        assert decoded.data == [10, 20, 30, 40, 50]

    def test_encode_decode_nested_struct(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct point_t {
                double x;
                double y;
            }
            struct pose_t {
                point_t position;
                point_t velocity;
            }
        """)
        cls = reg.find_by_name("pose_t")
        point_cls = reg.find_by_name("point_t")

        msg = cls()
        msg.position = point_cls()
        msg.position.x = 1.0
        msg.position.y = 2.0
        msg.velocity = point_cls()
        msg.velocity.x = 0.1
        msg.velocity.y = 0.2

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.position.x == 1.0
        assert decoded.position.y == 2.0
        assert decoded.velocity.x == 0.1
        assert decoded.velocity.y == 0.2

    def test_encode_decode_struct_array(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct item_t {
                int32_t id;
                string name;
            }
            struct list_t {
                int32_t n;
                item_t items[n];
            }
        """)
        list_cls = reg.find_by_name("list_t")
        item_cls = reg.find_by_name("item_t")

        msg = list_cls()
        msg.n = 2
        i1 = item_cls()
        i1.id = 1
        i1.name = "alpha"
        i2 = item_cls()
        i2.id = 2
        i2.name = "beta"
        msg.items = [i1, i2]

        data = msg.encode()
        decoded = list_cls.decode(data)

        assert decoded.n == 2
        assert decoded.items[0].id == 1
        assert decoded.items[0].name == "alpha"
        assert decoded.items[1].id == 2
        assert decoded.items[1].name == "beta"

    def test_recursive_type(self) -> None:
        """Test recursive type (node_t with children array)."""
        reg = TypeRegistry()
        reg.register_file("lcm_ref/examples/types/node_t.lcm")

        node_cls = reg.find_by_name("node_t")
        n = node_cls()
        n.num_children = 2

        c1 = node_cls()
        c1.num_children = 0
        c1.children = []
        c2 = node_cls()
        c2.num_children = 0
        c2.children = []
        n.children = [c1, c2]

        data = n.encode()
        decoded = node_cls.decode(data)

        assert decoded.num_children == 2
        assert len(decoded.children) == 2
        assert decoded.children[0].num_children == 0
        assert decoded.children[1].num_children == 0

    def test_byte_array(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                int32_t len;
                byte data[len];
            }
        """)
        cls = reg.find_by_name("msg_t")
        msg = cls()
        msg.len = 4
        msg.data = b"\x01\x02\x03\x04"

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.len == 4
        assert decoded.data == b"\x01\x02\x03\x04"

    def test_constants_on_class(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t {
                const int32_t MAX_SIZE = 100;
                const double PI = 3.14;
                int32_t x;
            }
        """)
        cls = reg.find_by_name("msg_t")
        assert cls.MAX_SIZE == 100
        assert cls.PI == 3.14

    def test_get_hash(self) -> None:
        reg = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        cls = reg.find_by_name("msg_t")
        obj = cls()
        h = obj.get_hash()
        assert isinstance(h, int)
        assert h != 0


# ---------------------------------------------------------------------------
# Fingerprint matching
# ---------------------------------------------------------------------------


class TestFingerprintMatching:
    def test_fingerprint_consistency(self) -> None:
        """Same .lcm definition should produce same fingerprint."""
        reg1 = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; int64_t y; }
        """)
        reg2 = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; int64_t y; }
        """)
        fp1 = reg1.find_by_name("msg_t")._get_packed_fingerprint()
        fp2 = reg2.find_by_name("msg_t")._get_packed_fingerprint()
        assert fp1 == fp2

    def test_fingerprint_differs_on_change(self) -> None:
        """Different member types should produce different fingerprints."""
        reg1 = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        reg2 = _registry_from_text("""
            package test;
            struct msg_t { int64_t x; }
        """)
        fp1 = reg1.find_by_name("msg_t")._get_packed_fingerprint()
        fp2 = reg2.find_by_name("msg_t")._get_packed_fingerprint()
        assert fp1 != fp2

    def test_auto_match_by_fingerprint(self) -> None:
        """TypeRegistry should find class by fingerprint from encoded data."""
        reg = _registry_from_text("""
            package test;
            struct msg_t { int32_t x; }
        """)
        cls = reg.find_by_name("msg_t")
        msg = cls()
        msg.x = 42
        data = msg.encode()

        # Extract fingerprint from encoded data (first 8 bytes)
        fp = _struct.unpack(">Q", data[:8])[0]
        found_cls = reg.find_by_fingerprint(fp)
        assert found_cls is cls


# ---------------------------------------------------------------------------
# Example file integration tests
# ---------------------------------------------------------------------------


class TestExampleFiles:
    def test_example_t_roundtrip(self) -> None:
        reg = TypeRegistry()
        reg.register_dir("lcm_ref/examples/types/")

        cls = reg.find_by_name("example_t")
        msg = cls()
        msg.timestamp = 9999
        msg.position = [1.0, 2.0, 3.0]
        msg.orientation = [0.1, 0.2, 0.3, 0.4]
        msg.num_ranges = 3
        msg.ranges = [100, 200, 300]
        msg.name = "test_msg"
        msg.enabled = True

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.timestamp == 9999
        assert decoded.position == [1.0, 2.0, 3.0]
        assert decoded.num_ranges == 3
        assert decoded.ranges == [100, 200, 300]
        assert decoded.name == "test_msg"
        assert decoded.enabled is True

    def test_example_list_t_roundtrip(self) -> None:
        reg = TypeRegistry()
        reg.register_dir("lcm_ref/examples/types/")

        list_cls = reg.find_by_name("example_list_t")
        ex_cls = reg.find_by_name("example_t")

        msg = list_cls()
        msg.n = 1
        item = ex_cls()
        item.timestamp = 42
        item.position = [0.0, 0.0, 0.0]
        item.orientation = [1.0, 0.0, 0.0, 0.0]
        item.num_ranges = 0
        item.ranges = []
        item.name = "item0"
        item.enabled = False
        msg.examples = [item]

        data = msg.encode()
        decoded = list_cls.decode(data)

        assert decoded.n == 1
        assert len(decoded.examples) == 1
        assert decoded.examples[0].timestamp == 42
        assert decoded.examples[0].name == "item0"

    def test_muldim_array_t(self) -> None:
        reg = TypeRegistry()
        reg.register_dir("lcm_ref/examples/types/")

        cls = reg.find_by_name("muldim_array_t")
        msg = cls()
        msg.size_a = 2
        msg.size_b = 2
        msg.size_c = 1
        # data[size_a][size_b][size_c] = data[2][2][1]
        msg.data = [[[1], [2]], [[3], [4]]]
        # strarray[2][size_c] = strarray[2][1]
        msg.strarray = [["a"], ["b"]]

        data = msg.encode()
        decoded = cls.decode(data)

        assert decoded.size_a == 2
        assert decoded.size_b == 2
        assert decoded.size_c == 1
        assert decoded.data == [[[1], [2]], [[3], [4]]]
        assert decoded.strarray == [["a"], ["b"]]

    def test_cross_file_reference(self) -> None:
        """Test that types from different files can reference each other."""
        reg = TypeRegistry()
        reg.register_file("lcm_ref/examples/types/example_t.lcm")
        reg.register_file("lcm_ref/examples/types/example_list_t.lcm")

        list_cls = reg.find_by_name("example_list_t")
        assert list_cls is not None
        # example_t should also be registered
        ex_cls = reg.find_by_name("example_t")
        assert ex_cls is not None

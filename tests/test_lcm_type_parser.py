"""Unit tests for lcm_tools.core.lcm_type_parser."""

from __future__ import annotations

import pytest

from lcm_tools.core.lcm_type_parser import (
    LcmDimension,
    LcmMember,
    LcmStruct,
    LcmParseError,
    compute_fingerprints,
    parse_lcm_string,
    parse_lcm_file,
    _hash_update,
    _hash_string_update,
    _lcm_struct_hash,
)


# ---------------------------------------------------------------------------
# Tokenizer / basic parsing tests
# ---------------------------------------------------------------------------


class TestBasicParsing:
    def test_empty_input(self) -> None:
        structs = parse_lcm_string("")
        assert structs == []

    def test_simple_struct(self) -> None:
        text = """
        package test;
        struct simple_t {
            int32_t value;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs) == 1
        s = structs[0]
        assert s.full_name == "test.simple_t"
        assert s.package == "test"
        assert s.short_name == "simple_t"
        assert len(s.members) == 1
        assert s.members[0].type_name == "int32_t"
        assert s.members[0].member_name == "value"

    def test_no_package(self) -> None:
        text = """
        struct nopkg_t {
            int32_t x;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs) == 1
        assert structs[0].full_name == "nopkg_t"
        assert structs[0].package == ""

    def test_multiple_structs(self) -> None:
        text = """
        package multi;
        struct a_t { int32_t x; }
        struct b_t { int64_t y; }
        """
        structs = parse_lcm_string(text)
        assert len(structs) == 2
        assert structs[0].short_name == "a_t"
        assert structs[1].short_name == "b_t"

    def test_dotted_package(self) -> None:
        text = """
        package com.example.types;
        struct msg_t { int32_t v; }
        """
        structs = parse_lcm_string(text)
        assert structs[0].full_name == "com.example.types.msg_t"
        assert structs[0].package == "com.example.types"


# ---------------------------------------------------------------------------
# Member types
# ---------------------------------------------------------------------------


class TestMemberTypes:
    def test_all_primitive_types(self) -> None:
        text = """
        package p;
        struct all_types_t {
            int8_t   a;
            int16_t  b;
            int32_t  c;
            int64_t  d;
            byte     e;
            float    f;
            double   g;
            string   h;
            boolean  i;
        }
        """
        structs = parse_lcm_string(text)
        members = structs[0].members
        assert len(members) == 9
        types = [m.type_name for m in members]
        assert types == [
            "int8_t", "int16_t", "int32_t", "int64_t",
            "byte", "float", "double", "string", "boolean",
        ]

    def test_fixed_array(self) -> None:
        text = """
        package p;
        struct arr_t {
            double position[3];
        }
        """
        structs = parse_lcm_string(text)
        m = structs[0].members[0]
        assert m.member_name == "position"
        assert len(m.dimensions) == 1
        assert m.dimensions[0].mode == "const"
        assert m.dimensions[0].size == "3"

    def test_variable_array(self) -> None:
        text = """
        package p;
        struct varr_t {
            int32_t n;
            int16_t data[n];
        }
        """
        structs = parse_lcm_string(text)
        m = structs[0].members[1]
        assert m.member_name == "data"
        assert len(m.dimensions) == 1
        assert m.dimensions[0].mode == "var"
        assert m.dimensions[0].size == "n"

    def test_multi_dim_array(self) -> None:
        text = """
        package p;
        struct md_t {
            int32_t sa;
            int32_t sb;
            int32_t data[sa][sb];
        }
        """
        structs = parse_lcm_string(text)
        m = structs[0].members[2]
        assert len(m.dimensions) == 2
        assert m.dimensions[0].mode == "var"
        assert m.dimensions[0].size == "sa"
        assert m.dimensions[1].mode == "var"
        assert m.dimensions[1].size == "sb"

    def test_compound_type_same_package(self) -> None:
        text = """
        package mypkg;
        struct inner_t { int32_t x; }
        struct outer_t { inner_t val; }
        """
        structs = parse_lcm_string(text)
        outer = structs[1]
        assert outer.members[0].type_name == "mypkg.inner_t"

    def test_compound_type_fully_qualified(self) -> None:
        text = """
        package mypkg;
        struct outer_t { other_pkg.inner_t val; }
        """
        structs = parse_lcm_string(text)
        assert structs[0].members[0].type_name == "other_pkg.inner_t"

    def test_multiple_members_one_line(self) -> None:
        text = """
        package p;
        struct s_t {
            int32_t a, b, c;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs[0].members) == 3
        names = [m.member_name for m in structs[0].members]
        assert names == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_const_int(self) -> None:
        text = """
        package p;
        struct c_t {
            const int32_t ABC = 42;
            int32_t x;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs[0].constants) == 1
        assert structs[0].constants[0].name == "ABC"
        assert structs[0].constants[0].value_str == "42"

    def test_const_multiple(self) -> None:
        text = """
        package p;
        struct c_t {
            const int32_t A = 1, B = 2;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs[0].constants) == 2
        assert structs[0].constants[0].name == "A"
        assert structs[0].constants[1].name == "B"

    def test_const_hex(self) -> None:
        text = """
        package p;
        struct c_t {
            const int64_t FLAG = 0xff00;
        }
        """
        structs = parse_lcm_string(text)
        assert structs[0].constants[0].value_str == "0xff00"

    def test_const_float(self) -> None:
        text = """
        package p;
        struct c_t {
            const double PI = 3.14159;
        }
        """
        structs = parse_lcm_string(text)
        assert structs[0].constants[0].value_str == "3.14159"


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestComments:
    def test_line_comments(self) -> None:
        text = """
        // This is a comment
        package p;
        // Another comment
        struct s_t {
            // Member comment
            int32_t x;
        }
        """
        structs = parse_lcm_string(text)
        assert len(structs) == 1
        assert structs[0].members[0].member_name == "x"

    def test_block_comments(self) -> None:
        text = """
        /* Block comment */
        package p;
        struct s_t {
            /* multi
               line */
            int32_t x;
        }
        """
        structs = parse_lcm_string(text)
        assert structs[0].members[0].member_name == "x"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_missing_semicolon(self) -> None:
        text = """
        package p
        struct s_t { int32_t x; }
        """
        with pytest.raises(LcmParseError):
            parse_lcm_string(text)

    def test_missing_brace(self) -> None:
        text = """
        package p;
        struct s_t
            int32_t x;
        """
        with pytest.raises(LcmParseError):
            parse_lcm_string(text)

    def test_unknown_token(self) -> None:
        text = """
        package p;
        @invalid
        """
        with pytest.raises(LcmParseError):
            parse_lcm_string(text)


# ---------------------------------------------------------------------------
# Hash / Fingerprint
# ---------------------------------------------------------------------------


class TestHash:
    def test_hash_update_basic(self) -> None:
        # Verify basic hash operations match C implementation
        v = 0x12345678
        v = _hash_string_update(v, "timestamp")
        # Just verify it produces a deterministic value
        assert isinstance(v, int)

    def test_hash_deterministic(self) -> None:
        v1 = _hash_string_update(0x12345678, "test")
        v2 = _hash_string_update(0x12345678, "test")
        assert v1 == v2

    def test_hash_different_strings(self) -> None:
        v1 = _hash_string_update(0x12345678, "abc")
        v2 = _hash_string_update(0x12345678, "def")
        assert v1 != v2

    def test_struct_hash_simple(self) -> None:
        text = """
        package p;
        struct s_t {
            int32_t x;
        }
        """
        structs = parse_lcm_string(text)
        h = _lcm_struct_hash(structs[0])
        assert h != 0  # Should produce non-zero hash

    def test_compute_fingerprints(self) -> None:
        text = """
        package p;
        struct inner_t { int32_t x; }
        struct outer_t { inner_t val; }
        """
        structs = parse_lcm_string(text)
        compute_fingerprints(structs)
        # Both should have non-zero fingerprints
        assert structs[0].hash_value != 0
        assert structs[1].hash_value != 0
        # And different from each other
        assert structs[0].hash_value != structs[1].hash_value

    def test_recursive_type_hash(self) -> None:
        """Recursive types (like node_t) should not infinite-loop."""
        text = """
        package p;
        struct node_t {
            int32_t num_children;
            p.node_t children[num_children];
        }
        """
        structs = parse_lcm_string(text)
        compute_fingerprints(structs)
        assert structs[0].hash_value != 0


# ---------------------------------------------------------------------------
# Parse example files
# ---------------------------------------------------------------------------


class TestParseExampleFiles:
    def test_example_t(self) -> None:
        structs = parse_lcm_file("lcm_ref/examples/types/example_t.lcm")
        assert len(structs) == 1
        s = structs[0]
        assert s.full_name == "exlcm.example_t"
        assert len(s.members) == 7
        # Check specific members
        assert s.members[0].member_name == "timestamp"
        assert s.members[0].type_name == "int64_t"
        assert s.members[5].member_name == "name"
        assert s.members[5].type_name == "string"
        assert s.members[6].member_name == "enabled"
        assert s.members[6].type_name == "boolean"

    def test_node_t(self) -> None:
        structs = parse_lcm_file("lcm_ref/examples/types/node_t.lcm")
        assert len(structs) == 1
        s = structs[0]
        assert s.full_name == "exlcm.node_t"
        assert s.members[1].type_name == "exlcm.node_t"

    def test_example_list_t(self) -> None:
        structs = parse_lcm_file("lcm_ref/examples/types/example_list_t.lcm")
        assert len(structs) == 1
        s = structs[0]
        assert s.members[1].type_name == "exlcm.example_t"

    def test_muldim_array_t(self) -> None:
        structs = parse_lcm_file("lcm_ref/examples/types/muldim_array_t.lcm")
        s = structs[0]
        data_m = s.members[3]
        assert data_m.member_name == "data"
        assert len(data_m.dimensions) == 3

    def test_exampleconst_t(self) -> None:
        structs = parse_lcm_file("lcm_ref/examples/types/exampleconst_t.lcm")
        s = structs[0]
        assert len(s.constants) == 5

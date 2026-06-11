"""Pure-Python parser for LCM type definition files (.lcm).

Parses .lcm files into an AST (LcmStruct, LcmMember, etc.) and computes
the LCM fingerprint (hash) compatible with lcm-gen's algorithm.

Reference: lcm_ref/lcmgen/lcmgen.c
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# AST data structures
# ---------------------------------------------------------------------------

# Primitive types recognised by LCM
PRIMITIVE_TYPES = frozenset({
    "int8_t", "int16_t", "int32_t", "int64_t",
    "byte", "float", "double", "string", "boolean",
})

# Types that can be used as array dimension sizes
ARRAY_DIM_TYPES = frozenset({"int8_t", "int16_t", "int32_t", "int64_t"})


@dataclass
class LcmDimension:
    """One dimension of an array member."""
    mode: str   # "const" (literal number) or "var" (runtime variable)
    size: str   # numeric string or member variable name


@dataclass
class LcmConstant:
    """A constant declared inside a struct."""
    type_name: str   # e.g. "int32_t", "double"
    name: str
    value_str: str   # raw value string from the .lcm file


@dataclass
class LcmMember:
    """A single member (field) of a struct."""
    type_name: str                     # fully-qualified, e.g. "exlcm.point_t"
    member_name: str
    dimensions: List[LcmDimension] = field(default_factory=list)


@dataclass
class LcmStruct:
    """A complete struct definition parsed from a .lcm file."""
    full_name: str              # e.g. "exlcm.example_t"
    package: str                # e.g. "exlcm"
    short_name: str             # e.g. "example_t"
    members: List[LcmMember] = field(default_factory=list)
    constants: List[LcmConstant] = field(default_factory=list)
    hash_value: int = 0         # LCM fingerprint (computed after parsing)
    base_hash: int = 0          # Non-recursive hash (before compute_fingerprints)
    source_file: str = ""       # path to the .lcm file


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Token types
_TOK_EOF = "EOF"
_TOK_IDENT = "IDENT"
_TOK_NUMBER = "NUMBER"
_TOK_PUNCT = "PUNCT"
_TOK_COMMENT = "COMMENT"

# Regex patterns (order matters)
_TOKEN_PATTERNS = [
    ("SKIP",      re.compile(r"[ \t\r]+")),
    ("NEWLINE",   re.compile(r"\n")),
    ("LINE_COMMENT", re.compile(r"//[^\n]*")),
    ("BLOCK_COMMENT", re.compile(r"/\*[\s\S]*?\*/")),
    ("HEX_NUMBER", re.compile(r"0[xX][0-9a-fA-F]+")),
    ("FLOAT_NUMBER", re.compile(r"\d+\.\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?|\d+[eE][+-]?\d+")),
    ("INT_NUMBER", re.compile(r"\d+")),
    ("IDENT",     re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")),
    ("PUNCT",     re.compile(r"[{}[\]();,.=]")),
]


@dataclass
class _Token:
    type: str
    value: str
    line: int
    col: int


class _Tokenizer:
    """Simple tokenizer for .lcm files."""

    def __init__(self, text: str, path: str = "<string>") -> None:
        self._text = text
        self._path = path
        self._pos = 0
        self._line = 1
        self._col = 1
        self._tokens: List[_Token] = []
        self._idx = 0
        self._tokenize()

    def _tokenize(self) -> None:
        text = self._text
        pos = 0
        line = 1
        col = 1

        while pos < len(text):
            matched = False
            for name, pattern in _TOKEN_PATTERNS:
                m = pattern.match(text, pos)
                if m:
                    value = m.group(0)
                    if name in ("LINE_COMMENT", "BLOCK_COMMENT"):
                        # Count newlines inside block comments
                        for ch in value:
                            if ch == "\n":
                                line += 1
                                col = 1
                            else:
                                col += 1
                    elif name == "NEWLINE":
                        line += 1
                        col = 1
                    elif name == "SKIP":
                        col += len(value)
                    elif name == "HEX_NUMBER":
                        self._tokens.append(_Token(_TOK_NUMBER, value, line, col))
                        col += len(value)
                    elif name == "INT_NUMBER" or name == "FLOAT_NUMBER":
                        self._tokens.append(_Token(_TOK_NUMBER, value, line, col))
                        col += len(value)
                    elif name == "IDENT":
                        self._tokens.append(_Token(_TOK_IDENT, value, line, col))
                        col += len(value)
                    elif name == "PUNCT":
                        self._tokens.append(_Token(_TOK_PUNCT, value, line, col))
                        col += len(value)
                    pos = m.end()
                    matched = True
                    break
            if not matched:
                raise LcmParseError(
                    f"{self._path}:{line}:{col}: unexpected character {text[pos]!r}"
                )

        self._tokens.append(_Token(_TOK_EOF, "", line, col))

    def peek(self) -> _Token:
        return self._tokens[self._idx]

    def next(self) -> _Token:
        tok = self._tokens[self._idx]
        if tok.type != _TOK_EOF:
            self._idx += 1
        return tok

    def expect(self, type_: str, value: Optional[str] = None) -> _Token:
        tok = self.next()
        if tok.type != type_:
            raise LcmParseError(
                f"{self._path}:{tok.line}:{tok.col}: expected {type_} "
                f"{'(' + value + ')' if value else ''}, got {tok.type} {tok.value!r}"
            )
        if value is not None and tok.value != value:
            raise LcmParseError(
                f"{self._path}:{tok.line}:{tok.col}: expected {value!r}, "
                f"got {tok.value!r}"
            )
        return tok


class LcmParseError(Exception):
    """Raised when .lcm file parsing fails."""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _is_primitive(type_name: str) -> bool:
    return type_name in PRIMITIVE_TYPES


def _parse_typename(tok: _Tokenizer, current_package: str) -> str:
    """Read a type name (possibly dotted), return fully-qualified name."""
    name_tok = tok.expect(_TOK_IDENT)
    name = name_tok.value

    # Handle dotted names like "exlcm.node_t"
    while tok.peek().type == _TOK_PUNCT and tok.peek().value == ".":
        tok.next()  # consume "."
        part = tok.expect(_TOK_IDENT)
        name = f"{name}.{part.value}"

    # If no package in the name and it's not a primitive, prepend current package
    if not _is_primitive(name) and "." not in name and current_package:
        name = f"{current_package}.{name}"

    return name


def _parse_dimensions(tok: _Tokenizer) -> List[LcmDimension]:
    """Parse zero or more array dimension brackets: [3], [n], etc."""
    dims: List[LcmDimension] = []
    while tok.peek().type == _TOK_PUNCT and tok.peek().value == "[":
        tok.next()  # consume "["
        size_tok = tok.next()
        if size_tok.type == _TOK_NUMBER:
            dims.append(LcmDimension(mode="const", size=size_tok.value))
        elif size_tok.type == _TOK_IDENT:
            dims.append(LcmDimension(mode="var", size=size_tok.value))
        else:
            raise LcmParseError(
                f"{size_tok.line}:{size_tok.col}: expected array size, "
                f"got {size_tok.value!r}"
            )
        tok.expect(_TOK_PUNCT, "]")
    return dims


def _parse_const(tok: _Tokenizer, struct: LcmStruct) -> None:
    """Parse: const <type> NAME = VALUE [, NAME = VALUE]* ;"""
    type_tok = tok.expect(_TOK_IDENT)
    const_type = type_tok.value

    while True:
        name_tok = tok.expect(_TOK_IDENT)
        tok.expect(_TOK_PUNCT, "=")
        val_tok = tok.next()
        # Value can be a number or a negative number (handled as "- number")
        if val_tok.type == _TOK_PUNCT and val_tok.value == "-":
            num_tok = tok.next()
            value_str = f"-{num_tok.value}"
        else:
            value_str = val_tok.value

        struct.constants.append(LcmConstant(
            type_name=const_type,
            name=name_tok.value,
            value_str=value_str,
        ))

        # Check for comma (another constant) or semicolon (end)
        nxt = tok.peek()
        if nxt.type == _TOK_PUNCT and nxt.value == ",":
            tok.next()
            continue
        break

    tok.expect(_TOK_PUNCT, ";")


def _parse_struct(tok: _Tokenizer, current_package: str, source_file: str) -> LcmStruct:
    """Parse: struct <name> { ... }"""
    name_tok = tok.expect(_TOK_IDENT)
    short_name = name_tok.value

    if current_package:
        full_name = f"{current_package}.{short_name}"
    else:
        full_name = short_name

    struct = LcmStruct(
        full_name=full_name,
        package=current_package,
        short_name=short_name,
        source_file=source_file,
    )

    tok.expect(_TOK_PUNCT, "{")

    while True:
        nxt = tok.peek()
        if nxt.type == _TOK_EOF:
            raise LcmParseError(f"{source_file}: unexpected EOF inside struct {short_name}")
        if nxt.type == _TOK_PUNCT and nxt.value == "}":
            tok.next()
            break

        # Check for "const" keyword
        if nxt.type == _TOK_IDENT and nxt.value == "const":
            tok.next()  # consume "const"
            _parse_const(tok, struct)
            continue

        # Otherwise it's a member declaration
        type_name = _parse_typename(tok, current_package)

        # One or more members can be declared on the same line
        while True:
            member_name_tok = tok.expect(_TOK_IDENT)
            dims = _parse_dimensions(tok)

            struct.members.append(LcmMember(
                type_name=type_name,
                member_name=member_name_tok.value,
                dimensions=dims,
            ))

            nxt2 = tok.peek()
            if nxt2.type == _TOK_PUNCT and nxt2.value == ",":
                tok.next()  # consume "," and read next member
                continue
            break

        tok.expect(_TOK_PUNCT, ";")

    return struct


def parse_lcm_string(text: str, source_file: str = "<string>") -> List[LcmStruct]:
    """Parse .lcm file content and return a list of LcmStruct definitions.

    Args:
        text: Content of the .lcm file.
        source_file: File path (for error messages).

    Returns:
        List of parsed struct definitions (hash not yet computed).
    """
    tok = _Tokenizer(text, source_file)
    structs: List[LcmStruct] = []
    current_package = ""

    while tok.peek().type != _TOK_EOF:
        nxt = tok.peek()

        if nxt.type == _TOK_IDENT and nxt.value == "package":
            tok.next()
            pkg_tok = tok.expect(_TOK_IDENT)
            # Package names can be dotted
            pkg = pkg_tok.value
            while tok.peek().type == _TOK_PUNCT and tok.peek().value == ".":
                tok.next()
                part = tok.expect(_TOK_IDENT)
                pkg = f"{pkg}.{part.value}"
            tok.expect(_TOK_PUNCT, ";")
            current_package = pkg
            continue

        if nxt.type == _TOK_IDENT and nxt.value == "struct":
            tok.next()  # consume "struct"
            s = _parse_struct(tok, current_package, source_file)
            structs.append(s)
            continue

        raise LcmParseError(
            f"{source_file}:{nxt.line}:{nxt.col}: unexpected token {nxt.value!r}, "
            f"expected 'package' or 'struct'"
        )

    return structs


def parse_lcm_file(path: str | Path) -> List[LcmStruct]:
    """Parse a .lcm file from disk.

    Args:
        path: Path to the .lcm file.

    Returns:
        List of parsed struct definitions.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_lcm_string(text, source_file=str(p))


# ---------------------------------------------------------------------------
# Hash / Fingerprint computation (must match lcmgen.c exactly)
# ---------------------------------------------------------------------------

_MASK64 = 0xFFFFFFFFFFFFFFFF


def _hash_update(v: int, c: int) -> int:
    """Replicate lcmgen.c hash_update()."""
    # Sign-extend c to 64-bit if needed (C char is signed on most platforms)
    if c > 127:
        c -= 256
    v = (((v << 8) ^ (v >> 55)) + c) & _MASK64
    return v


def _hash_string_update(v: int, s: str) -> int:
    """Replicate lcmgen.c hash_string_update()."""
    v = _hash_update(v, len(s))
    for ch in s:
        v = _hash_update(v, ord(ch))
    return v


def _lcm_struct_hash(struct: LcmStruct) -> int:
    """Compute the LCM fingerprint for a single struct (non-recursive part).

    This replicates lcm_struct_hash() from lcmgen.c:
    - Hashes member names, primitive type names, and dimension info.
    - Does NOT include the struct's own name in the hash.
    - For compound (non-primitive) member types, does NOT hash the type name
      here; that's handled in the recursive fingerprint.
    """
    v = 0x12345678

    for member in struct.members:
        # Hash member name
        v = _hash_string_update(v, member.member_name)

        # Hash primitive type name (but NOT compound type names)
        if _is_primitive(member.type_name):
            v = _hash_string_update(v, member.type_name)

        # Hash dimensionality
        ndim = len(member.dimensions)
        v = _hash_update(v, ndim)
        for dim in member.dimensions:
            mode_val = 0 if dim.mode == "const" else 1  # LCM_CONST=0, LCM_VAR=1
            v = _hash_update(v, mode_val)
            v = _hash_string_update(v, dim.size)

    return v


def compute_fingerprints(structs: List[LcmStruct]) -> None:
    """Compute fingerprints for all structs, resolving cross-references.

    The final fingerprint (_get_hash_recursive) includes:
    1. The struct's own hash (from member names, primitive types, dimensions)
    2. Recursively, the hash of any compound member types

    This function sets struct.hash_value for each struct in the list.

    Args:
        structs: List of all known struct definitions (may span multiple files).
    """
    # Build lookup table: full_name -> LcmStruct
    by_name: dict[str, LcmStruct] = {}
    for s in structs:
        by_name[s.full_name] = s
        # Also index by short name for same-package references
        if s.short_name not in by_name:
            by_name[s.short_name] = s

    # First compute base hashes (non-recursive part)
    for s in structs:
        base = _lcm_struct_hash(s)
        s.base_hash = base
        s.hash_value = base

    # Now compute recursive fingerprints
    # Cache: full_name -> final recursive hash
    cache: dict[str, int] = {}

    def _recursive_hash(struct: LcmStruct, parents: list[str]) -> int:
        if struct.full_name in parents:
            return 0  # Break recursion cycle

        if struct.full_name in cache:
            return cache[struct.full_name]

        new_parents = parents + [struct.full_name]
        tmphash = struct.hash_value

        for member in struct.members:
            if not _is_primitive(member.type_name):
                # Resolve the compound type
                ref_struct = _resolve_type(member.type_name, struct.package, by_name)
                if ref_struct is not None:
                    tmphash = (tmphash + _recursive_hash(ref_struct, new_parents)) & _MASK64

        # Rotate left by 1
        tmphash = (((tmphash << 1) & _MASK64) + (tmphash >> 63)) & _MASK64

        cache[struct.full_name] = tmphash
        return tmphash

    for s in structs:
        s.hash_value = _recursive_hash(s, [])


def _resolve_type(
    type_name: str,
    current_package: str,
    by_name: dict[str, LcmStruct],
) -> Optional[LcmStruct]:
    """Resolve a type name to its LcmStruct definition."""
    # Try exact match first
    if type_name in by_name:
        return by_name[type_name]

    # Try with current package prefix
    if current_package and "." not in type_name:
        qualified = f"{current_package}.{type_name}"
        if qualified in by_name:
            return by_name[qualified]

    return None

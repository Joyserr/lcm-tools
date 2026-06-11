"""Runtime LCM type class builder.

Dynamically generates Python decode classes from parsed LcmStruct definitions.
Generated classes have the same interface as lcm-gen output (decode, _decode_one,
_get_packed_fingerprint, __slots__, etc.) and can be used directly with the
echo display module.

Also provides TypeRegistry for fingerprint-based auto-matching.
"""

from __future__ import annotations

import struct as _struct
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from lcm_tools.core.lcm_type_parser import (
    LcmStruct,
    compute_fingerprints,
    parse_lcm_file,
)

# ---------------------------------------------------------------------------
# struct format char mapping (big-endian)
# ---------------------------------------------------------------------------

_PRIM_FORMAT = {
    "byte": "B",
    "boolean": "b",
    "int8_t": "b",
    "int16_t": ">h",
    "int32_t": ">i",
    "int64_t": ">q",
    "float": ">f",
    "double": ">d",
}

_PRIM_SIZE = {
    "byte": 1,
    "boolean": 1,
    "int8_t": 1,
    "int16_t": 2,
    "int32_t": 4,
    "int64_t": 8,
    "float": 4,
    "double": 8,
}

# For batch unpacking of arrays (big-endian, no '>')
_PRIM_FMT_CHAR = {
    "byte": "B",
    "boolean": "b",
    "int8_t": "b",
    "int16_t": "h",
    "int32_t": "i",
    "int64_t": "q",
    "float": "f",
    "double": "d",
}


def _is_primitive(type_name: str) -> bool:
    return type_name in _PRIM_FORMAT


# ---------------------------------------------------------------------------
# Dynamic class builder
# ---------------------------------------------------------------------------

def _build_init(members: list) -> callable:
    """Build __init__ method that initialises all fields to defaults."""

    def __init__(self: Any) -> None:
        for m in members:
            type_name = m["type"]
            dims = m["dims"]

            if not dims:
                # Scalar
                if type_name == "byte":
                    setattr(self, m["name"], 0)
                elif type_name == "boolean":
                    setattr(self, m["name"], False)
                elif type_name in ("int8_t", "int16_t", "int32_t", "int64_t"):
                    setattr(self, m["name"], 0)
                elif type_name in ("float", "double"):
                    setattr(self, m["name"], 0.0)
                elif type_name == "string":
                    setattr(self, m["name"], "")
                else:
                    # Compound type - will be resolved at decode time
                    setattr(self, m["name"], None)
            else:
                # Array
                if dims[-1][0] == "var":
                    # Variable-length last dim -> empty list
                    setattr(self, m["name"], [])
                else:
                    # Fixed-size: build nested lists
                    val = _build_fixed_init(type_name, dims, 0)
                    setattr(self, m["name"], val)

    return __init__


def _build_fixed_init(type_name: str, dims: list, dim_idx: int) -> Any:
    """Recursively build fixed-size array initialiser."""
    if dim_idx == len(dims):
        # Leaf value
        if type_name == "byte":
            return 0
        elif type_name == "boolean":
            return False
        elif type_name in ("int8_t", "int16_t", "int32_t", "int64_t"):
            return 0
        elif type_name in ("float", "double"):
            return 0.0
        elif type_name == "string":
            return ""
        else:
            return None
    if dim_idx == len(dims) - 1 and type_name == "byte":
        return b""
    size = int(dims[dim_idx][1])
    return [_build_fixed_init(type_name, dims, dim_idx + 1) for _ in range(size)]


def _build_decode_one(member_specs: list, type_registry: "TypeRegistry") -> callable:
    """Build _decode_one(buf) static method (raw function, not staticmethod)."""

    def _decode_one(buf: BytesIO) -> Any:
        # 'cls' is the class itself, accessed via the closure
        self = _decode_one._owner_cls()
        for ms in member_specs:
            _decode_member(self, buf, ms, type_registry)
        return self

    return _decode_one


def _decode_member(
    self: Any, buf: BytesIO, ms: dict, registry: "TypeRegistry"
) -> None:
    """Decode a single member into self."""
    name = ms["name"]
    type_name = ms["type"]
    dims = ms["dims"]

    if not dims:
        # Scalar
        setattr(self, name, _decode_scalar(buf, type_name, registry))
    else:
        # Array
        val = _decode_array(self, buf, type_name, dims, 0, registry)
        setattr(self, name, val)


def _decode_scalar(buf: BytesIO, type_name: str, registry: "TypeRegistry") -> Any:
    """Decode a single scalar or compound value from buf."""
    if type_name == "string":
        str_len = _struct.unpack(">I", buf.read(4))[0]
        raw = buf.read(str_len)
        return raw[:-1].decode("utf-8", "replace") if raw else ""
    elif type_name == "boolean":
        return bool(_struct.unpack("b", buf.read(1))[0])
    elif type_name in _PRIM_FORMAT:
        fmt = _PRIM_FORMAT[type_name]
        return _struct.unpack(fmt, buf.read(_PRIM_SIZE[type_name]))[0]
    else:
        # Compound type
        ref_cls = registry._classes_by_name.get(type_name)
        if ref_cls is None:
            raise ValueError(f"Unknown LCM type: {type_name}")
        return ref_cls._decode_one(buf)


def _resolve_dim_size(self: Any, dim: tuple) -> int:
    """Resolve a dimension size: const -> int, var -> self.<field>."""
    mode, size_str = dim
    if mode == "const":
        return int(size_str)
    else:
        return getattr(self, size_str)


def _decode_array(
    self: Any,
    buf: BytesIO,
    type_name: str,
    dims: list,
    dim_idx: int,
    registry: "TypeRegistry",
) -> Any:
    """Recursively decode a (possibly multi-dimensional) array."""
    if dim_idx == len(dims):
        # Should not be called here; scalar path handles this
        return _decode_scalar(buf, type_name, registry)

    dim_mode, dim_size_str = dims[dim_idx]
    size = _resolve_dim_size(self, dims[dim_idx])
    is_last_dim = (dim_idx == len(dims) - 1)

    if is_last_dim:
        # Last dimension: decode elements directly
        if _is_primitive(type_name) and type_name != "string":
            # Batch decode primitives
            return _decode_prim_array(buf, type_name, size)
        else:
            # Decode elements one by one (strings or compound)
            result = []
            for _ in range(size):
                result.append(_decode_scalar(buf, type_name, registry))
            return result
    else:
        # Not last dimension: recurse
        result = []
        for _ in range(size):
            result.append(
                _decode_array(self, buf, type_name, dims, dim_idx + 1, registry)
            )
        return result


def _decode_prim_array(buf: BytesIO, type_name: str, count: int) -> Any:
    """Decode an array of primitive values using batch struct.unpack."""
    if type_name == "byte":
        return buf.read(count)

    fmt_char = _PRIM_FMT_CHAR[type_name]
    elem_size = _PRIM_SIZE[type_name]
    raw = buf.read(count * elem_size)

    if type_name == "boolean":
        return [bool(x) for x in _struct.unpack(f">{count}{fmt_char}", raw)]
    else:
        return list(_struct.unpack(f">{count}{fmt_char}", raw))


def build_lcm_class(
    lcm_struct: LcmStruct, registry: "TypeRegistry"
) -> type:
    """Dynamically build a Python class for an LCM struct.

    The generated class has the same interface as lcm-gen output:
    - __slots__ with all member names
    - __init__() initialising all fields
    - decode(data) static method
    - _decode_one(buf) static method
    - _get_packed_fingerprint() static method
    - _get_hash_recursive(parents) static method

    Args:
        lcm_struct: Parsed struct definition (with hash_value computed).
        registry: TypeRegistry for resolving compound type references.

    Returns:
        A dynamically created Python class.
    """
    short_name = lcm_struct.short_name
    member_names = [m.member_name for m in lcm_struct.members]

    # Prepare member specs for decode
    member_specs = []
    for m in lcm_struct.members:
        dims = [(d.mode, d.size) for d in m.dimensions]
        member_specs.append({
            "name": m.member_name,
            "type": m.type_name,
            "dims": dims,
        })

    # Build __init__
    init_fn = _build_init(member_specs)

    # Build _decode_one
    decode_one_fn = _build_decode_one(member_specs, registry)

    # Build _get_hash_recursive (raw function)
    hash_recursive_fn = _build_hash_recursive(lcm_struct, registry)

    # Build _get_packed_fingerprint (raw function)
    packed_fp_cache: list = [None]

    def _get_packed_fingerprint() -> bytes:
        if _get_packed_fingerprint._cache[0] is None:  # type: ignore[attr-defined]
            _get_packed_fingerprint._cache[0] = _struct.pack(  # type: ignore[attr-defined]
                ">Q", _get_packed_fingerprint._hash_fn([])  # type: ignore[attr-defined]
            )
        return _get_packed_fingerprint._cache[0]  # type: ignore[attr-defined]

    _get_packed_fingerprint._cache = packed_fp_cache  # type: ignore[attr-defined]
    _get_packed_fingerprint._hash_fn = hash_recursive_fn  # type: ignore[attr-defined]

    # Build decode (raw function)
    def decode(data: bytes) -> Any:
        if hasattr(data, "read"):
            buf = data
        else:
            buf = BytesIO(data)
        if buf.read(8) != decode._owner_cls._get_packed_fingerprint():  # type: ignore[attr-defined]
            raise ValueError("Decode error: fingerprint mismatch")
        return decode._owner_cls._decode_one(buf)  # type: ignore[attr-defined]

    # Build _encode_one (minimal, for encode support)
    def _encode_one(self: Any, buf: BytesIO) -> None:
        for ms in member_specs:
            _encode_member(self, buf, ms, registry)

    def encode(self: Any) -> bytes:
        buf = BytesIO()
        buf.write(self.__class__._get_packed_fingerprint())
        self._encode_one(buf)
        return buf.getvalue()

    def get_hash(self: Any) -> int:
        return _struct.unpack(">Q", self.__class__._get_packed_fingerprint())[0]

    # Create the class
    cls_dict: Dict[str, Any] = {
        "__slots__": member_names,
        "__init__": init_fn,
        "_decode_one": staticmethod(decode_one_fn),
        "_get_hash_recursive": staticmethod(hash_recursive_fn),
        "_get_packed_fingerprint": staticmethod(_get_packed_fingerprint),
        "decode": staticmethod(decode),
        "_encode_one": _encode_one,
        "encode": encode,
        "get_hash": get_hash,
        "__typenames__": [m.type_name for m in lcm_struct.members],
        "__dimensions__": [
            [
                int(d.size) if d.mode == "const" else d.size
                for d in m.dimensions
            ] if m.dimensions else None
            for m in lcm_struct.members
        ],
    }

    # Add constants
    for c in lcm_struct.constants:
        if c.type_name in ("int8_t", "int16_t", "int32_t"):
            cls_dict[c.name] = int(c.value_str, 0)
        elif c.type_name == "int64_t":
            cls_dict[c.name] = int(c.value_str, 0)
        elif c.type_name in ("float", "double"):
            cls_dict[c.name] = float(c.value_str)

    cls = type(short_name, (object,), cls_dict)

    # Store back-references so functions can find the owning class
    decode_one_fn._owner_cls = cls  # type: ignore[attr-defined]
    decode._owner_cls = cls  # type: ignore[attr-defined]

    return cls


def _build_hash_recursive(lcm_struct: LcmStruct, registry: "TypeRegistry") -> callable:
    """Build _get_hash_recursive(parents) matching lcm-gen output (raw function)."""
    base_hash = lcm_struct.base_hash
    short_name = lcm_struct.short_name

    # Pre-compute which members are compound types
    compound_members = []
    for m in lcm_struct.members:
        if not _is_primitive(m.type_name):
            compound_members.append(m.type_name)

    def _get_hash_recursive(parents: list) -> int:
        if _get_hash_recursive._short_name in parents:  # type: ignore[attr-defined]
            return 0
        newparents = parents + [_get_hash_recursive._short_name]  # type: ignore[attr-defined]
        tmphash = _get_hash_recursive._base_hash  # type: ignore[attr-defined]
        for type_name in _get_hash_recursive._compound_members:  # type: ignore[attr-defined]
            ref_cls = _get_hash_recursive._registry._classes_by_name.get(type_name)  # type: ignore[attr-defined]
            if ref_cls is not None:
                tmphash = (tmphash + ref_cls._get_hash_recursive(newparents)) & 0xFFFFFFFFFFFFFFFF
        tmphash = (((tmphash << 1) & 0xFFFFFFFFFFFFFFFF) + (tmphash >> 63)) & 0xFFFFFFFFFFFFFFFF
        return tmphash

    _get_hash_recursive._base_hash = base_hash  # type: ignore[attr-defined]
    _get_hash_recursive._short_name = short_name  # type: ignore[attr-defined]
    _get_hash_recursive._compound_members = compound_members  # type: ignore[attr-defined]
    _get_hash_recursive._registry = registry  # type: ignore[attr-defined]

    return _get_hash_recursive


def _encode_member(self: Any, buf: BytesIO, ms: dict, registry: "TypeRegistry") -> None:
    """Encode a single member."""
    name = ms["name"]
    type_name = ms["type"]
    dims = ms["dims"]
    value = getattr(self, name)

    if not dims:
        _encode_scalar(buf, value, type_name, registry)
    else:
        _encode_array(buf, value, type_name, dims, 0, self, registry)


def _encode_scalar(buf: BytesIO, value: Any, type_name: str, registry: "TypeRegistry") -> None:
    if type_name == "string":
        encoded = value.encode("utf-8")
        buf.write(_struct.pack(">I", len(encoded) + 1))
        buf.write(encoded)
        buf.write(b"\x00")
    elif type_name == "boolean":
        buf.write(_struct.pack("b", int(value)))
    elif type_name in _PRIM_FORMAT:
        buf.write(_struct.pack(_PRIM_FORMAT[type_name], value))
    else:
        # Compound
        value._encode_one(buf)


def _encode_array(
    buf: BytesIO, value: Any, type_name: str,
    dims: list, dim_idx: int, self: Any, registry: "TypeRegistry"
) -> None:
    if dim_idx == len(dims):
        _encode_scalar(buf, value, type_name, registry)
        return

    is_last = (dim_idx == len(dims) - 1)
    if is_last and _is_primitive(type_name) and type_name != "string":
        _encode_prim_array(buf, value, type_name)
    else:
        for item in value:
            if is_last:
                _encode_scalar(buf, item, type_name, registry)
            else:
                _encode_array(buf, item, type_name, dims, dim_idx + 1, self, registry)


def _encode_prim_array(buf: BytesIO, value: Any, type_name: str) -> None:
    if type_name == "byte":
        buf.write(bytearray(value))
        return
    fmt_char = _PRIM_FMT_CHAR[type_name]
    count = len(value)
    if type_name == "boolean":
        buf.write(_struct.pack(f">{count}{fmt_char}", *[int(v) for v in value]))
    else:
        buf.write(_struct.pack(f">{count}{fmt_char}", *value))


# ---------------------------------------------------------------------------
# Type Registry
# ---------------------------------------------------------------------------

class TypeRegistry:
    """Registry of dynamically generated LCM decode classes.

    Parses .lcm files and maintains a mapping from fingerprints and
    type names to generated classes. Supports auto-matching by fingerprint.
    """

    def __init__(self) -> None:
        self._structs: List[LcmStruct] = []
        self._classes: Dict[str, type] = {}           # full_name -> class
        self._classes_by_name: Dict[str, type] = {}    # various name forms -> class
        self._by_fingerprint: Dict[int, type] = {}     # fingerprint int -> class
        self._built = False

    def register_file(self, lcm_path: str | Path) -> None:
        """Parse a .lcm file and register all structs found.

        Also auto-discovers sibling .lcm files in the same directory to
        resolve cross-file type references (nested struct dependencies).

        Args:
            lcm_path: Path to a .lcm file.
        """
        p = Path(lcm_path)
        structs = parse_lcm_file(p)
        self._structs.extend(structs)
        self._built = False

        # Auto-discover sibling .lcm files for cross-file type references
        parent = p.parent
        if parent.is_dir():
            registered_files = {s.source_file for s in self._structs}
            for sibling in sorted(parent.glob("*.lcm")):
                sibling_str = str(sibling)
                if sibling_str not in registered_files and sibling != p:
                    try:
                        sib_structs = parse_lcm_file(sibling)
                        self._structs.extend(sib_structs)
                    except Exception:
                        pass  # Skip unparseable files silently

    def register_dir(self, dir_path: str | Path) -> None:
        """Recursively register all .lcm files in a directory.

        Args:
            dir_path: Path to a directory containing .lcm files.
        """
        p = Path(dir_path)
        if p.is_file() and p.suffix == ".lcm":
            self.register_file(p)
            return
        for lcm_file in sorted(p.rglob("*.lcm")):
            self.register_file(lcm_file)

    def register_paths(self, paths: List[str | Path]) -> None:
        """Register multiple files/directories."""
        for p in paths:
            path = Path(p)
            if path.is_dir():
                self.register_dir(path)
            elif path.is_file():
                self.register_file(path)
            else:
                raise FileNotFoundError(f"LCM path not found: {p}")

    def _build_all(self) -> None:
        """Compute fingerprints and build all classes."""
        if self._built:
            return

        # Compute fingerprints across ALL registered structs
        compute_fingerprints(self._structs)

        # Build classes
        for s in self._structs:
            cls = build_lcm_class(s, self)
            self._classes[s.full_name] = cls

            # Register under multiple name forms for lookup
            self._classes_by_name[s.full_name] = cls
            if s.short_name not in self._classes_by_name:
                self._classes_by_name[s.short_name] = cls

            # Register by fingerprint
            self._by_fingerprint[s.hash_value] = cls

        self._built = True

    def find_by_fingerprint(self, fp: int) -> Optional[type]:
        """Find a decode class by LCM fingerprint.

        Args:
            fp: The 64-bit fingerprint integer.

        Returns:
            The decode class, or None if not found.
        """
        self._build_all()
        return self._by_fingerprint.get(fp)

    def find_by_name(self, name: str) -> Optional[type]:
        """Find a decode class by type name.

        Accepts short name ("example_t"), fully-qualified ("exlcm.example_t"),
        or module.Class format ("exlcm.example_t").

        Args:
            name: Type name to search for.

        Returns:
            The decode class, or None if not found.
        """
        self._build_all()
        return self._classes_by_name.get(name)

    @property
    def all_types(self) -> Dict[str, type]:
        """Return all registered classes keyed by full name."""
        self._build_all()
        return dict(self._classes)

    @property
    def all_fingerprints(self) -> Dict[int, str]:
        """Return mapping from fingerprint to type full name."""
        self._build_all()
        return {fp: cls.__name__ for fp, cls in self._by_fingerprint.items()}

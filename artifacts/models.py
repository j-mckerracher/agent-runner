"""Prompt 18 — Versioned ArtifactRef contract: a typed artifact identity.

`ArtifactRef` is a small, deterministic, stdlib-only data contract that
honestly identifies one workflow artifact — a local `path` and/or a
URI-addressed resource — and carries producer/consumer/schema/validation/
checksum/metadata information about it. It is the reusable leaf contract that
later prompts (concrete artifact schemas, loaders, validators, centralized
paths, stage produce/consume wiring) build on.

Architectural rule: this package imports **only the standard library** — never
`core`, `workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, or any
vendor SDK. It therefore carries zero optional-dependency or circular-import
risk, mirroring `runners/models.py` and `telemetry/events.py`.

Design rules (see `docs/artifact-ref.md` for the full rationale):

- `artifact_ref_schema_version` is checked against
  `SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS`. A ref carrying an unsupported
  version is rejected rather than parsed as if it matched the current
  contract. This is the *contract* version — distinct from the per-artifact
  `artifact_schema_version` that names the shape of the artifact's payload.
- Required identity: `artifact_type` (nonempty) plus at least one of `path` /
  `uri`. Everything else is optional; `None` means "missing/unknown" and is
  omitted from `to_dict()`.
- Construction is pure: no filesystem access, subprocess, network, env
  mutation, or logging. Paths are coerced `str -> Path` without touching the
  filesystem; checksums are validated but never computed.
- `__post_init__` validates each field's *outer runtime type* before coercing
  it, then aggregates every problem into one `ArtifactRefValidationError` —
  bad input never leaks a bare `TypeError`/`ValueError`.
- `metadata` JSON-ability is verified lazily in `to_dict()` (construction
  cheap, serialization is the gate), matching the runners/telemetry contracts.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any

ARTIFACT_REF_SCHEMA_VERSION = "1"
SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS = frozenset({"1"})


class ArtifactValidationStatus(str, Enum):
    """Outcome of validating the referenced artifact against its schema.

    `NOT_VALIDATED` is an explicit "we checked nothing yet" state; it is
    distinct from the field being absent (`None`), which means "unknown /
    not recorded". Garbage is never silently coerced to `NOT_VALIDATED`.
    """

    NOT_VALIDATED = "not_validated"
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"


class ArtifactRefValidationError(ValueError):
    """Raised when an `ArtifactRef` fails validation.

    Carries every problem found (not just the first) so callers see the full
    set of offending fields in one pass, mirroring
    `runners.models.AgentContractError` and
    `telemetry.events.TraceValidationError`.
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid artifact ref")


class ArtifactMetadataSerializationError(ValueError):
    """Raised when `metadata` holds a value `json` cannot encode.

    Kept an independent copy of the `MetadataSerializationError` idea (rather
    than imported from `runners`/`telemetry`) so this leaf package imports
    nothing outside the standard library.
    """


_HEX64 = re.compile(r"[0-9a-f]{64}")


class _Immutable:
    """Base for genuinely immutable, slotted canonical metadata nodes.

    Subclasses declare their own ``__slots__`` and set fields via
    ``object.__setattr__`` in ``__init__``. Because there is no ``__dict__`` and
    both ``__setattr__``/``__delattr__`` raise, an instance cannot be mutated —
    even when reached directly through an internal-looking attribute.
    """

    __slots__ = ()

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")


class _MappingNode(_Immutable):
    """Immutable canonical mapping: an ordered tuple of (key, value) entries.

    Keys are either ``str`` or an immutable `_UnsupportedKey` marker (a
    caller-owned non-string key is never retained). Values are canonical nodes.
    Equality is order-insensitive, matching plain-`dict` semantics.
    """

    __slots__ = ("entries",)

    def __init__(self, entries: tuple):
        object.__setattr__(self, "entries", entries)

    def __eq__(self, other: Any) -> Any:
        if not isinstance(other, _MappingNode):
            return NotImplemented
        return dict(self.entries) == dict(other.entries)

    __hash__ = None

    def __repr__(self) -> str:
        return f"_MappingNode({self.entries!r})"


class _SequenceNode(_Immutable):
    """Immutable canonical sequence: an ordered tuple of canonical items."""

    __slots__ = ("items",)

    def __init__(self, items: tuple):
        object.__setattr__(self, "items", items)

    def __eq__(self, other: Any) -> Any:
        if not isinstance(other, _SequenceNode):
            return NotImplemented
        return self.items == other.items

    __hash__ = None

    def __repr__(self) -> str:
        return f"_SequenceNode({self.items!r})"


class _UnsupportedValue(_Immutable):
    """Immutable marker for a caller leaf JSON cannot encode.

    Records only the original type name — the caller object is never retained.
    Equality is by identity, so two distinct unsupported values (even of the
    same type) never silently compare equal.
    """

    __slots__ = ("original_type",)

    def __init__(self, original_type: str):
        object.__setattr__(self, "original_type", original_type)

    def __repr__(self) -> str:
        return f"_UnsupportedValue({self.original_type!r})"


class _UnsupportedKey(_Immutable):
    """Immutable marker for a caller-owned non-string mapping key.

    The caller's key object is never retained or exposed; iteration yields this
    marker instead. Equality is by identity so distinct invalid keys never
    silently compare equal.
    """

    __slots__ = ("original_type",)

    def __init__(self, original_type: str):
        object.__setattr__(self, "original_type", original_type)

    def __repr__(self) -> str:
        return f"_UnsupportedKey({self.original_type!r})"


class _CycleMarker(_Immutable):
    """Immutable singleton standing in for a detected reference cycle.

    Using a marker rather than an actual cyclic Python container keeps the
    canonical graph a finite tree, so equality and hashing can never leak a
    ``RecursionError``. Serialization still rejects it lazily.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "_CYCLE"


_CYCLE = _CycleMarker()


def _canonical_scalar(value: Any) -> Any:
    """Return an exact built-in for an accepted JSON scalar, detaching subclass
    instances (e.g. a mutable `int`/`str`/`float` subclass) so no caller-owned
    object is retained. The underlying built-in payload is extracted **without**
    calling overridable conversion hooks (`__int__`/`__float__`/`__str__`), so a
    subclass cannot change the stored value or wire output. Order matters:
    `bool` is an `int` subclass and must be checked first. Returns
    `NotImplemented` for anything not a JSON scalar.
    """
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        # `int.__index__` returns the exact underlying integer payload without
        # calling any overridden `__int__`, `__index__`, or `__repr__` on the
        # subclass, and works for arbitrarily large values.
        return int.__index__(value)
    if isinstance(value, float):
        # `float.hex`/`float.fromhex` round-trips the exact value, preserving
        # `-0.0`, and cannot be intercepted by an overridden `__float__`.
        return float.fromhex(float.hex(value))
    if isinstance(value, str):
        # Full-slice via the built-in `str` method bypasses any overridden
        # `__str__`/`__getitem__` on the subclass.
        return str.__getitem__(value, slice(None))
    return NotImplemented


def _canonical_key(key: Any) -> Any:
    """Return a canonical mapping key: strings become an exact detached `str`
    (extracted via the built-in `str` slice, bypassing any overridden
    `__str__`/`__getitem__`), anything else is replaced by an immutable
    `_UnsupportedKey` marker so the caller's key object is never retained.
    """
    if isinstance(key, str):
        return str.__getitem__(key, slice(None))
    return _UnsupportedKey(type(key).__name__)


def _canonicalize(value: Any, _memo: dict[int, Any], _active: set[int]) -> Any:
    """Build an immutable canonical graph from caller input.

    Mappings become `_MappingNode` (ordered tuple of canonical key/value
    entries), sequences become `_SequenceNode` (ordered tuple of items, tuples
    canonicalized to lists), and JSON scalars pass through. Non-string keys and
    unencodable leaves become immutable markers so no caller-owned mutable
    object is ever retained. Memoized by object identity for shared substructure;
    a container re-entered while still on the active stack becomes `_CYCLE`,
    keeping the result a finite tree. Validates nothing and performs no I/O —
    strict JSON checks stay lazy in `to_dict()`.
    """
    scalar = _canonical_scalar(value)
    if scalar is not NotImplemented:
        return scalar
    if isinstance(value, Mapping):
        vid = id(value)
        if vid in _active:
            return _CYCLE
        if vid in _memo:
            return _memo[vid]
        _active.add(vid)
        entries = tuple(
            (_canonical_key(key), _canonicalize(item, _memo, _active))
            for key, item in value.items()
        )
        _active.discard(vid)
        node: Any = _MappingNode(entries)
        _memo[vid] = node
        return node
    if isinstance(value, (list, tuple)):
        vid = id(value)
        if vid in _active:
            return _CYCLE
        if vid in _memo:
            return _memo[vid]
        _active.add(vid)
        items = tuple(_canonicalize(item, _memo, _active) for item in value)
        _active.discard(vid)
        node = _SequenceNode(items)
        _memo[vid] = node
        return node
    return _UnsupportedValue(type(value).__name__)


def _materialize(node: Any) -> Any:
    """Materialize a canonical node into a fresh plain `dict`/`list`/scalar.

    Raises `ArtifactMetadataSerializationError` on an unsupported-value marker,
    a non-string (marker) key, or a cycle marker — never exposing the immutable
    canonical graph or returning a retained caller object. The canonical graph
    is a finite tree, so this always terminates.
    """
    if isinstance(node, _MappingNode):
        out: dict[str, Any] = {}
        for key, value in node.entries:
            if isinstance(key, _UnsupportedKey):
                raise ArtifactMetadataSerializationError(
                    f"metadata: mapping key of type {key.original_type} is not a string"
                )
            out[key] = _materialize(value)
        return out
    if isinstance(node, _SequenceNode):
        return [_materialize(item) for item in node.items]
    if isinstance(node, _UnsupportedValue):
        raise ArtifactMetadataSerializationError(
            f"metadata: value of type {node.original_type} is not JSON-serializable"
        )
    if node is _CYCLE:
        raise ArtifactMetadataSerializationError(
            "metadata contains a reference cycle"
        )
    return node


class _ReadOnlyMetadata(Mapping):
    """Read-only, deeply immutable `Mapping` view over an immutable canonical
    metadata graph.

    The graph root is an immutable `_MappingNode`; the view exposes no mutation
    API (so `ref.metadata[k] = v` / `del ref.metadata[k]` raise `TypeError`) and
    `_root` itself cannot be reassigned. Even reached directly, `_root` yields
    only immutable canonical nodes — there is no `dict`/`list` to edit.
    `__getitem__` materializes a fresh plain `dict`/`list` (or scalar), so
    mutating a returned value cannot reach the reference. Equality is value-based
    and finite (cycles are markers, never real cycles); the view is unhashable,
    exactly as the plain `dict` it replaces was.
    """

    __slots__ = ("_root",)

    def __init__(self, root: "_MappingNode"):
        object.__setattr__(self, "_root", root)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("_ReadOnlyMetadata is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("_ReadOnlyMetadata is immutable")

    def __getitem__(self, key: Any) -> Any:
        for entry_key, entry_value in self._root.entries:
            if entry_key == key:
                return _materialize(entry_value)
        raise KeyError(key)

    def __contains__(self, key: Any) -> bool:
        return any(entry_key == key for entry_key, _ in self._root.entries)

    def __iter__(self):
        return (entry_key for entry_key, _ in self._root.entries)

    def __len__(self) -> int:
        return len(self._root.entries)

    def __eq__(self, other: Any) -> Any:
        if isinstance(other, _ReadOnlyMetadata):
            return self._root == other._root
        if isinstance(other, Mapping):
            return self._root == _canonicalize(dict(other), {}, set())
        return NotImplemented

    def __ne__(self, other: Any) -> Any:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    __hash__ = None

    def __repr__(self) -> str:
        return f"_ReadOnlyMetadata({self._root!r})"


def _strict_jsonable(node: Any, *, where: str) -> Any:
    """Return a fresh JSON-compatible copy of a canonical metadata node, or raise.

    Rejects non-string (marker) keys, non-finite floats, unsupported-value
    markers, and cycle markers, each with an actionable metadata location. The
    canonical graph is a finite tree, so this always terminates; shared
    substructure is serialized independently under each path.
    """
    if isinstance(node, _MappingNode):
        out: dict[str, Any] = {}
        for key, value in node.entries:
            if isinstance(key, _UnsupportedKey):
                raise ArtifactMetadataSerializationError(
                    f"{where}: dict keys must be strings, got {key.original_type}"
                )
            out[key] = _strict_jsonable(value, where=f"{where}.{key}")
        return out
    if isinstance(node, _SequenceNode):
        return [
            _strict_jsonable(item, where=f"{where}[{index}]")
            for index, item in enumerate(node.items)
        ]
    if isinstance(node, _UnsupportedValue):
        raise ArtifactMetadataSerializationError(
            f"{where}: value of type {node.original_type} is not JSON-serializable"
        )
    if node is _CYCLE:
        raise ArtifactMetadataSerializationError(
            f"{where}: metadata contains a reference cycle"
        )
    if node is None or isinstance(node, (bool, int, str)):
        return node
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ArtifactMetadataSerializationError(
                f"{where}: non-finite float {node!r} is not valid JSON"
            )
        return node
    raise ArtifactMetadataSerializationError(
        f"{where}: value of type {type(node).__name__} is not JSON-serializable"
    )


def _nonempty_str(value: Any) -> bool:
    """True when `value` is a string with at least one non-whitespace char."""
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True, kw_only=True)
class ArtifactRef:
    """Immutable, versioned identity for one workflow artifact.

    Required: `artifact_type` (nonempty) and at least one of `path` / `uri`.
    All other fields are optional and default to `None` / empty.

    Construction is pure — no filesystem, subprocess, network, or env access.
    `path` strings are coerced to `Path` without resolution; `checksum_sha256`
    is validated but never computed. `consumer_stages` is a tri-state:
    `None` (unknown), `()` (explicitly no consumers), or an ordered tuple of
    stage names. `metadata` is canonicalized into an immutable graph and exposed
    as a read-only, deeply immutable view; its JSON-ability is checked lazily at
    serialization time.
    """

    artifact_type: str
    path: Path | None = None
    uri: str | None = None
    producer_stage: str | None = None
    consumer_stages: tuple[str, ...] | None = None
    artifact_schema: str | None = None
    artifact_schema_version: str | None = None
    validation_status: ArtifactValidationStatus | None = None
    checksum_sha256: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    artifact_ref_schema_version: str = ARTIFACT_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []

        # 1. Contract version. Guard isinstance(str) first: a non-str (e.g. an
        # unhashable list) must not reach the `in frozenset` membership test.
        if not isinstance(self.artifact_ref_schema_version, str):
            errors.append(
                "artifact_ref_schema_version: must be a string, got "
                f"{type(self.artifact_ref_schema_version).__name__}"
            )
        elif self.artifact_ref_schema_version not in SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS:
            errors.append(
                "artifact_ref_schema_version: unsupported version "
                f"{self.artifact_ref_schema_version!r}; supported: "
                f"{sorted(SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS)}"
            )

        # 2. artifact_type — required, nonempty; stored verbatim (not trimmed).
        if not _nonempty_str(self.artifact_type):
            errors.append(
                f"artifact_type: must be a nonempty string, got {self.artifact_type!r}"
            )

        # 3. path — None | str | Path. Check outer type before coercing so a
        # non-str/Path never reaches `Path(...)`, and an empty/whitespace str
        # never becomes `Path('.')`.
        if self.path is not None:
            if isinstance(self.path, Path):
                pass
            elif isinstance(self.path, str):
                if not _nonempty_str(self.path):
                    errors.append(
                        f"path: must be a nonempty string or Path, got {self.path!r}"
                    )
                else:
                    object.__setattr__(self, "path", Path(self.path))
            else:
                errors.append(
                    f"path: must be a str, Path, or None, got {type(self.path).__name__}"
                )

        # 4. uri — None | nonempty str.
        if self.uri is not None and not _nonempty_str(self.uri):
            errors.append(f"uri: must be a nonempty string or None, got {self.uri!r}")

        # 5. At least one addressing form present (after the above coercions).
        if self.path is None and self.uri is None:
            errors.append("path: at least one of path or uri must be supplied")

        # 6. producer_stage — None | nonempty str.
        if self.producer_stage is not None and not _nonempty_str(self.producer_stage):
            errors.append(
                f"producer_stage: must be a nonempty string or None, got {self.producer_stage!r}"
            )

        # 7. consumer_stages — None | list | tuple (never str/bytes). Coerce to
        # a defensive tuple, require nonempty-str items, reject duplicates,
        # preserve order.
        if self.consumer_stages is not None:
            if isinstance(self.consumer_stages, (str, bytes)) or not isinstance(
                self.consumer_stages, (list, tuple)
            ):
                errors.append(
                    "consumer_stages: must be a list/tuple of strings or None, got "
                    f"{type(self.consumer_stages).__name__}"
                )
            else:
                coerced = tuple(self.consumer_stages)
                seen: set[str] = set()
                for index, item in enumerate(coerced):
                    if not _nonempty_str(item):
                        errors.append(
                            f"consumer_stages[{index}]: must be a nonempty string, got {item!r}"
                        )
                    elif item in seen:
                        errors.append(f"consumer_stages: duplicate stage {item!r}")
                    else:
                        seen.add(item)
                object.__setattr__(self, "consumer_stages", coerced)

        # 8. artifact_schema & artifact_schema_version — both-or-neither; each
        # nonempty str when present. Distinct from artifact_ref_schema_version.
        if self.artifact_schema is not None and not _nonempty_str(self.artifact_schema):
            errors.append(
                f"artifact_schema: must be a nonempty string or None, got {self.artifact_schema!r}"
            )
        if self.artifact_schema_version is not None and not _nonempty_str(
            self.artifact_schema_version
        ):
            errors.append(
                "artifact_schema_version: must be a nonempty string or None, got "
                f"{self.artifact_schema_version!r}"
            )
        if (self.artifact_schema is None) != (self.artifact_schema_version is None):
            errors.append(
                "artifact_schema/artifact_schema_version: must both be present or both absent"
            )

        # 9. validation_status — None | ArtifactValidationStatus | resolvable str.
        # Absence stays None (never coerced to NOT_VALIDATED); garbage errors.
        if self.validation_status is not None:
            if isinstance(self.validation_status, ArtifactValidationStatus):
                pass
            elif isinstance(self.validation_status, str):
                try:
                    object.__setattr__(
                        self,
                        "validation_status",
                        ArtifactValidationStatus(self.validation_status),
                    )
                except ValueError:
                    errors.append(
                        "validation_status: not a valid ArtifactValidationStatus, got "
                        f"{self.validation_status!r}"
                    )
            else:
                errors.append(
                    "validation_status: must be an ArtifactValidationStatus, str, or None, got "
                    f"{type(self.validation_status).__name__}"
                )

        # 10. checksum_sha256 — None | exactly 64 lowercase hex chars. Never
        # computed. fullmatch (not match) so a 64-char prefix of a longer
        # string is rejected.
        if self.checksum_sha256 is not None:
            if not isinstance(self.checksum_sha256, str):
                errors.append(
                    "checksum_sha256: must be a string or None, got "
                    f"{type(self.checksum_sha256).__name__}"
                )
            elif not _HEX64.fullmatch(self.checksum_sha256):
                errors.append(
                    "checksum_sha256: must be 64 lowercase hex characters, got "
                    f"{self.checksum_sha256!r}"
                )

        # 11. metadata — must be a Mapping; canonicalized into an immutable
        # graph (frozen mapping/sequence nodes, tuples -> lists, non-string keys
        # and unencodable leaves captured as immutable markers, cycles as a
        # marker) so no caller-owned object is retained and nothing reachable is
        # mutable. The public field holds a read-only Mapping view over that
        # graph; JSON-ability is verified lazily in to_dict().
        if not isinstance(self.metadata, Mapping):
            errors.append(
                f"metadata: must be a mapping, got {type(self.metadata).__name__}"
            )
        else:
            object.__setattr__(
                self,
                "metadata",
                _ReadOnlyMetadata(_canonicalize(self.metadata, {}, set())),
            )

        if errors:
            raise ArtifactRefValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Always includes `artifact_type` and `artifact_ref_schema_version`.
        `None` optionals are omitted; observed empty collections are preserved
        (`consumer_stages == ()` -> `[]`). `Path` -> `str`, enum -> `.value`.
        `metadata` is included only when non-empty, and it is strictly
        re-serialized to a fresh nested structure so a serialization failure
        raises before any partial output is produced and the returned dict
        never aliases the ref's own metadata.
        """
        result: dict[str, Any] = {
            "artifact_type": self.artifact_type,
            "artifact_ref_schema_version": self.artifact_ref_schema_version,
        }
        if self.path is not None:
            result["path"] = str(self.path)
        if self.uri is not None:
            result["uri"] = self.uri
        if self.producer_stage is not None:
            result["producer_stage"] = self.producer_stage
        if self.consumer_stages is not None:
            result["consumer_stages"] = list(self.consumer_stages)
        if self.artifact_schema is not None:
            result["artifact_schema"] = self.artifact_schema
        if self.artifact_schema_version is not None:
            result["artifact_schema_version"] = self.artifact_schema_version
        if self.validation_status is not None:
            result["validation_status"] = self.validation_status.value
        if self.checksum_sha256 is not None:
            result["checksum_sha256"] = self.checksum_sha256
        if self.metadata:
            result["metadata"] = _strict_jsonable(
                self.metadata._root, where="ArtifactRef.metadata"
            )
        return result

    def to_json(self) -> str:
        """Serialize to a deterministic, sorted-keys, strict JSON string."""
        data = self.to_dict()
        try:
            return json.dumps(data, sort_keys=True, allow_nan=False)
        except ValueError as exc:
            raise ArtifactMetadataSerializationError(
                f"ArtifactRef.metadata: {exc}"
            ) from exc

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArtifactRef":
        """Reconstruct from a plain mapping; unknown top-level keys are ignored.

        Raw known values are passed straight to the constructor without
        pre-coercing `path`, `consumer_stages`, or `validation_status`.
        `__post_init__` is the single place that validates and normalizes them
        — this is what lets `path=""` be rejected instead of becoming
        `Path('.')`, and makes `validation_status="bad"` /
        `consumer_stages="qa"` surface the aggregate contract error rather than
        a bare `ValueError` or a silent character split.
        """
        errors = cls.validate_payload(data, where="ArtifactRef")
        if errors:
            raise ArtifactRefValidationError(errors)
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "artifact_type" not in kwargs:
            # Invalid sentinel: __post_init__ aggregates the missing-required
            # error with any other field problems instead of TypeError leaking.
            kwargs["artifact_type"] = None
        return cls(**kwargs)

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        """Structural validation of a raw payload, without constructing.

        At minimum rejects a non-`Mapping` payload (including `None`) with an
        actionable error. Mirrors `telemetry.events.TraceEvent.validate_payload`
        and is available for reuse by loaders.
        """
        if not isinstance(data, Mapping):
            return [f"{where}: expected a mapping, got {type(data).__name__}"]
        return []

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
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

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


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _ensure_jsonable(value: Any, *, where: str) -> None:
    """Recursively verify `value` is made only of JSON-compatible types.

    Never stringifies unsupported values as a fallback — raises
    `ArtifactMetadataSerializationError` naming the offending location instead.
    """
    if isinstance(value, _JSON_SCALAR_TYPES):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ArtifactMetadataSerializationError(
                    f"{where}: dict keys must be strings, got {type(key).__name__}"
                )
            _ensure_jsonable(item, where=f"{where}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _ensure_jsonable(item, where=f"{where}[{index}]")
        return
    raise ArtifactMetadataSerializationError(
        f"{where}: value of type {type(value).__name__} is not JSON-serializable"
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
    stage names. `metadata` is defensively copied; its JSON-ability is checked
    lazily at serialization time.
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

        # 11. metadata — must be a Mapping; defensively shallow-copied to a dict.
        # JSON-ability is verified lazily in to_dict().
        if not isinstance(self.metadata, Mapping):
            errors.append(
                f"metadata: must be a mapping, got {type(self.metadata).__name__}"
            )
        else:
            object.__setattr__(self, "metadata", dict(self.metadata))

        if errors:
            raise ArtifactRefValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Always includes `artifact_type` and `artifact_ref_schema_version`.
        `None` optionals are omitted; observed empty collections are preserved
        (`consumer_stages == ()` -> `[]`). `Path` -> `str`, enum -> `.value`.
        `metadata` is included only when non-empty, and its JSON-ability is
        verified first so a serialization failure raises before any partial
        output is produced.
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
            _ensure_jsonable(self.metadata, where="ArtifactRef.metadata")
            result["metadata"] = dict(self.metadata)
        return result

    def to_json(self) -> str:
        """Serialize to a deterministic, sorted-keys JSON string."""
        return json.dumps(self.to_dict(), sort_keys=True)

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
        return cls(**{k: v for k, v in data.items() if k in known})

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

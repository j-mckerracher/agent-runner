"""Prompt 18 — Versioned `ArtifactRef` contract (public surface).

A stdlib-only leaf package: importing `artifacts` loads nothing from `core`,
`workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, or any vendor
SDK. It re-exports the `ArtifactRef` data contract and its supporting types
from `artifacts.models`.
"""

from __future__ import annotations

from artifacts.models import (
    ARTIFACT_REF_SCHEMA_VERSION,
    SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS,
    ArtifactMetadataSerializationError,
    ArtifactRef,
    ArtifactRefValidationError,
    ArtifactValidationStatus,
)

__all__ = [
    "ARTIFACT_REF_SCHEMA_VERSION",
    "SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS",
    "ArtifactMetadataSerializationError",
    "ArtifactRef",
    "ArtifactRefValidationError",
    "ArtifactValidationStatus",
]

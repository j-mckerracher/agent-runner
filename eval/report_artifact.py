"""Prompt 21 — EvalReportArtifact: typed loader + ArtifactRef adapter.

Wraps an :class:`~eval.report_schema.EvalReport` with the standard
``load_with_validation`` / ``to_artifact_ref`` contract so callers can load
and identify an eval-report file through the same interface used by the
planning and implementation-report artifact adapters.

**No duplicate schema**: structural validation is delegated entirely to
:func:`~eval.report_schema.validate_report_payload` and
:meth:`~eval.report_schema.EvalReport.from_dict`; this module adds only an
explicit schema-version gate (the artifact adapter concern) and the
:class:`~artifacts.models.ArtifactRef` factory.

Allowed import directions:
- ``eval`` → ``artifacts`` ✓ (artifacts is a stdlib-only leaf)
- ``artifacts`` → ``eval`` ✗ (would create a cycle)

This module therefore lives in ``eval/``, not ``artifacts/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from artifacts.models import ArtifactRef, ArtifactValidationStatus
from artifacts.validation import (
    ArtifactLoadError,
    ArtifactValidationError,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from eval.report_schema import (
    REPORT_SCHEMA_VERSION,
    EvalReport,
    ReportValidationError,
    validate_report_payload,
)


def _err(code: str, message: str, location: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, message=message, severity=ValidationSeverity.ERROR, location=location
    )


class EvalReportArtifact:
    """Immutable, read-only wrapper for a v0.2 eval-report JSON file.

    Construction: use :meth:`load_with_validation`.

    The underlying :class:`~eval.report_schema.EvalReport` is accessible
    via :attr:`report`.  Serialization delegates back to
    :meth:`~eval.report_schema.EvalReport.to_dict` /
    :meth:`~eval.report_schema.EvalReport.to_json` so the source file is
    never mutated.
    """

    # ------------------------------------------------------------------
    # Class-level contract metadata
    # ------------------------------------------------------------------

    ARTIFACT_TYPE: ClassVar[str] = "eval_report"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.eval-report"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = REPORT_SCHEMA_VERSION
    PRODUCER_STAGE: ClassVar[str] = "eval"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("reporting", "comparison")
    CONTENT_FORMAT: ClassVar[str] = "json"

    # ------------------------------------------------------------------
    # Slots / construction
    # ------------------------------------------------------------------

    __slots__ = ("_report",)

    def __init__(self, report: EvalReport) -> None:
        object.__setattr__(self, "_report", report)

    def __setattr__(self, name: str, value: object) -> None:  # pragma: no cover
        raise AttributeError("EvalReportArtifact is immutable")

    def __delattr__(self, name: str) -> None:  # pragma: no cover
        raise AttributeError("EvalReportArtifact is immutable")

    def __repr__(self) -> str:
        return (
            f"EvalReportArtifact(eval_run_id={self.report.eval_run_id!r}, "
            f"benchmark_suite_id={self.report.benchmark_suite_id!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EvalReportArtifact):
            return NotImplemented
        return self._report == other._report  # type: ignore[operator]

    # ------------------------------------------------------------------
    # Public payload
    # ------------------------------------------------------------------

    @property
    def report(self) -> EvalReport:
        """The underlying :class:`~eval.report_schema.EvalReport`."""
        return self._report  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        """Delegate to :meth:`~eval.report_schema.EvalReport.to_dict`."""
        return self._report.to_dict()  # type: ignore[union-attr]

    def to_json(self, *, indent: int | None = 2) -> str:
        """Delegate to :meth:`~eval.report_schema.EvalReport.to_json`."""
        return self._report.to_json(indent=indent)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Loader
    # ------------------------------------------------------------------

    @classmethod
    def load_with_validation(cls, path: Path | str) -> "EvalReportArtifact":
        """Read, parse, and validate an eval-report JSON file.

        Raises
        ------
        ArtifactLoadError
            File not found, unreadable, or not valid JSON.
        ArtifactValidationError
            Unsupported ``report_schema_version`` or structural validation
            failure from :func:`~eval.report_schema.validate_report_payload`.
        """
        p = Path(path)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            raise ArtifactLoadError(f"eval report not found: {p}")
        except OSError as exc:
            raise ArtifactLoadError(f"could not read {p}: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactLoadError(
                f"eval report at {p} is not valid JSON: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ArtifactLoadError(
                f"eval report at {p}: expected a JSON object, got {type(data).__name__}"
            )

        # Explicit schema-version gate (additive — does not modify EvalReport).
        version = data.get("report_schema_version")
        if version != REPORT_SCHEMA_VERSION:
            result = ValidationResult(
                (
                    _err(
                        "invalid_value",
                        f"unsupported report_schema_version {version!r} "
                        f"(expected {REPORT_SCHEMA_VERSION!r})",
                        location="report_schema_version",
                    ),
                )
            )
            raise ArtifactValidationError(result, "EvalReportArtifact")

        # Structural validation via the canonical schema.
        errors = validate_report_payload(data)
        if errors:
            issues = tuple(
                _err("invalid_value", msg) for msg in errors
            )
            raise ArtifactValidationError(
                ValidationResult(issues), "EvalReportArtifact"
            )

        try:
            report = EvalReport.from_dict(data)
        except ReportValidationError as exc:
            issues = tuple(_err("invalid_value", msg) for msg in exc.errors)
            raise ArtifactValidationError(
                ValidationResult(issues), "EvalReportArtifact"
            ) from exc

        return cls(report)

    # ------------------------------------------------------------------
    # ArtifactRef conversion
    # ------------------------------------------------------------------

    def to_artifact_ref(
        self,
        *,
        path: Path | str | None = None,
        uri: str | None = None,
        checksum_sha256: str | None = None,
        validation_status: ArtifactValidationStatus | None = None,
        eval_run_id: str | None = None,
        benchmark_suite_id: str | None = None,
        candidate_version: str | None = None,
    ) -> ArtifactRef:
        """Build an :class:`~artifacts.models.ArtifactRef` for this artifact.

        *eval_run_id*, *benchmark_suite_id*, and *candidate_version* are
        optional identity fields placed in ``metadata``; if not supplied
        they are taken from :attr:`report`.
        """
        rpt = self._report  # type: ignore[union-attr]
        meta: dict[str, Any] = {
            "eval_run_id": eval_run_id if eval_run_id is not None else rpt.eval_run_id,
            "benchmark_suite_id": (
                benchmark_suite_id if benchmark_suite_id is not None
                else rpt.benchmark_suite_id
            ),
            "candidate_version": (
                candidate_version if candidate_version is not None
                else rpt.candidate_version
            ),
        }
        kwargs: dict[str, Any] = {
            "artifact_type": self.ARTIFACT_TYPE,
            "artifact_schema": self.ARTIFACT_SCHEMA,
            "artifact_schema_version": self.ARTIFACT_SCHEMA_VERSION,
            "producer_stage": self.PRODUCER_STAGE,
            "consumer_stages": self.CONSUMER_STAGES,
            "metadata": meta,
        }
        if path is not None:
            kwargs["path"] = path
        if uri is not None:
            kwargs["uri"] = uri
        if checksum_sha256 is not None:
            kwargs["checksum_sha256"] = checksum_sha256
        if validation_status is not None:
            kwargs["validation_status"] = validation_status
        return ArtifactRef(**kwargs)

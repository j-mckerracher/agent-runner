"""Prompt 21 — FinalDiffArtifact: evidence leaf for final.diff files.

A stdlib-only leaf that fits within the ``artifacts`` package import-isolation
contract (no ``core``, ``workflow``, ``runners``, ``eval``, ``server``,
``telemetry``, ``opik``, PyYAML, or any vendor SDK).

:class:`FinalDiffArtifact` wraps the raw text of a ``final.diff`` evidence
file produced by ``eval/runner.py`` at the end of a benchmark trial.  It is
intentionally *not* a subclass of :class:`~artifacts.payloads.PlanningArtifact`
because the payload is raw unified-diff text, not a YAML/JSON mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from artifacts.models import ArtifactRef, ArtifactValidationStatus
from artifacts.validation import ArtifactLoadError


class FinalDiffArtifact:
    """Immutable, read-only wrapper for the ``final.diff`` evidence file.

    Construction: use :meth:`load_with_validation` or :meth:`load`.

    The diff text is decoded from raw bytes explicitly so line-ending
    normalization does not silently alter the content.  An empty diff is
    valid and distinct from a missing file.

    Attributes
    ----------
    text:
        Full unified-diff text exactly as stored on disk (line endings
        preserved; may be empty).
    """

    # ------------------------------------------------------------------
    # Class-level contract metadata
    # ------------------------------------------------------------------

    ARTIFACT_TYPE: ClassVar[str] = "final_diff"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.final-diff"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "eval"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("reporting",)
    CONTENT_FORMAT: ClassVar[str] = "diff"

    # ------------------------------------------------------------------
    # Slots / construction
    # ------------------------------------------------------------------

    __slots__ = ("_text", "_byte_length")

    def __init__(self, text: str, *, _byte_length: int) -> None:
        # Private constructor — callers use load() / load_with_validation().
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_byte_length", _byte_length)

    def __setattr__(self, name: str, value: object) -> None:  # pragma: no cover
        raise AttributeError("FinalDiffArtifact is immutable")

    def __delattr__(self, name: str) -> None:  # pragma: no cover
        raise AttributeError("FinalDiffArtifact is immutable")

    def __repr__(self) -> str:
        return (
            f"FinalDiffArtifact(char_length={self.char_length!r}, "
            f"byte_length={self.byte_length!r}, "
            f"is_empty={self.is_empty!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FinalDiffArtifact):
            return NotImplemented
        return self._text == other._text

    def __hash__(self) -> int:
        return hash(self._text)

    # ------------------------------------------------------------------
    # Public payload
    # ------------------------------------------------------------------

    @property
    def text(self) -> str:
        """Full diff text exactly as stored on disk."""
        return self._text  # type: ignore[return-value]

    @property
    def char_length(self) -> int:
        """Number of Unicode code points in :attr:`text`."""
        return len(self._text)  # type: ignore[arg-type]

    @property
    def byte_length(self) -> int:
        """Number of UTF-8 bytes in the original file."""
        return self._byte_length  # type: ignore[return-value]

    @property
    def is_empty(self) -> bool:
        """``True`` when the diff text is the empty string."""
        return len(self._text) == 0  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def load_with_validation(cls, path: Path | str) -> "FinalDiffArtifact":
        """Read *path* and return a :class:`FinalDiffArtifact`.

        Raises
        ------
        ArtifactLoadError
            If the file is missing or its bytes cannot be decoded as UTF-8.
            An empty file is valid and returns an artifact with ``text=""``.
        """
        p = Path(path)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            raise ArtifactLoadError(f"final.diff not found: {p}")
        except OSError as exc:
            raise ArtifactLoadError(f"could not read {p}: {exc}") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactLoadError(
                f"final.diff at {p} is not valid UTF-8: {exc}"
            ) from exc
        return cls(text, _byte_length=len(raw))

    @classmethod
    def load(cls, path: Path | str) -> "FinalDiffArtifact":
        """Alias for :meth:`load_with_validation`."""
        return cls.load_with_validation(path)

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
    ) -> ArtifactRef:
        """Build an :class:`~artifacts.models.ArtifactRef` for this artifact.

        The caller must supply at least one of *path* / *uri* (same contract
        as :class:`~artifacts.models.ArtifactRef`).  Checksum is never
        computed here — pass it in if available; otherwise omit.

        The ``metadata`` dict carries ``char_length``, ``byte_length``, and
        ``is_empty`` so downstream consumers can reason about the diff
        without re-reading the file.
        """
        kwargs: dict[str, Any] = {
            "artifact_type": self.ARTIFACT_TYPE,
            "artifact_schema": self.ARTIFACT_SCHEMA,
            "artifact_schema_version": self.ARTIFACT_SCHEMA_VERSION,
            "producer_stage": self.PRODUCER_STAGE,
            "consumer_stages": self.CONSUMER_STAGES,
            "metadata": {
                "char_length": self.char_length,
                "byte_length": self.byte_length,
                "is_empty": self.is_empty,
            },
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

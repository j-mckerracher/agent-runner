"""Centralized, pure evidence-path contract for eval runs.

This module is the single source of truth for:
- The ID-sanitization algorithm used to build filesystem-safe path segments.
- The seven canonical evidence filenames produced per trial.
- The :class:`TrialEvidencePaths` dataclass that computes (but never creates)
  the evidence directory tree for one trial.
- Helpers for building the two report filenames (timestamped + ``latest.json``).

Pure module: no filesystem side-effects on import; no subprocess, network,
environment, or heavy-SDK dependencies. :meth:`TrialEvidencePaths.ensure_dir`
is the only method that touches the filesystem — callers opt in explicitly.

``eval/runner.py`` re-exports :func:`_sanitize_path_segment` from here so
existing ``runner._sanitize_path_segment`` references continue to resolve.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Sanitization (byte-identical to the original runner.py implementation)
# --------------------------------------------------------------------------

_ID_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


def _sanitize_path_segment(raw: str) -> str:
    """Make *raw* safe as a single filesystem path segment.

    Collisions between two different raw ids that sanitize to the same
    string are disambiguated with a short hash suffix so evidence from
    unrelated benchmarks never silently overwrites each other.

    Byte-identical to the original ``eval/runner.py`` implementation;
    moved here so callers outside the runner can share the same algorithm.
    """
    cleaned = (
        "".join(ch if ch in _ID_SAFE else "-" for ch in (raw or "unknown"))
        .strip("-.")
        or "unknown"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}-{digest}"


# --------------------------------------------------------------------------
# Canonical filenames — single source of truth
# --------------------------------------------------------------------------

STORY_JSON = "story.json"
WORKFLOW_STDOUT_LOG = "workflow.stdout.log"
WORKFLOW_STDERR_LOG = "workflow.stderr.log"
WORKFLOW_RESULT_JSON = "workflow_result.json"
HIDDEN_TESTS_XML = "hidden_tests.xml"
FINAL_DIFF = "final.diff"
TRACE_JSONL = "trace.jsonl"


# --------------------------------------------------------------------------
# TrialEvidencePaths — computed layout for one trial
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialEvidencePaths:
    """Immutable, computed path layout for the evidence of one benchmark trial.

    Construction is pure — no filesystem access. Call :meth:`ensure_dir`
    explicitly when you want to create the directory tree.

    All path properties are read-only and derive from :attr:`dir` by
    joining the canonical filename constants.
    """

    dir: Path

    # ------------------------------------------------------------------
    # Classmethod constructor
    # ------------------------------------------------------------------

    @classmethod
    def for_trial(
        cls,
        reports_dir: Path | str,
        eval_run_id: str,
        benchmark_id: str,
        trial_id: str,
    ) -> "TrialEvidencePaths":
        """Build the canonical evidence directory path for one trial.

        ``reports_dir / "evidence" / san(eval_run_id) / san(benchmark_id) / san(trial_id)``

        Pure — no mkdir.
        """
        base = (
            Path(reports_dir)
            / "evidence"
            / _sanitize_path_segment(eval_run_id)
            / _sanitize_path_segment(benchmark_id)
            / _sanitize_path_segment(trial_id)
        )
        return cls(dir=base)

    # ------------------------------------------------------------------
    # Filesystem mutation (opt-in)
    # ------------------------------------------------------------------

    def ensure_dir(self) -> Path:
        """Create :attr:`dir` (and any parents) if it does not already exist.

        Returns :attr:`dir` so callers can write
        ``paths = TrialEvidencePaths.for_trial(...).ensure_dir()``
        when they want both at once (though ``ensure_dir`` returns the
        *Path*, not a ``TrialEvidencePaths``, so chain carefully).
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    # ------------------------------------------------------------------
    # Read-only path properties
    # ------------------------------------------------------------------

    @property
    def story_json(self) -> Path:
        return self.dir / STORY_JSON

    @property
    def workflow_stdout_log(self) -> Path:
        return self.dir / WORKFLOW_STDOUT_LOG

    @property
    def workflow_stderr_log(self) -> Path:
        return self.dir / WORKFLOW_STDERR_LOG

    @property
    def workflow_result_json(self) -> Path:
        return self.dir / WORKFLOW_RESULT_JSON

    @property
    def hidden_tests_xml(self) -> Path:
        return self.dir / HIDDEN_TESTS_XML

    @property
    def final_diff(self) -> Path:
        return self.dir / FINAL_DIFF

    @property
    def trace_jsonl(self) -> Path:
        return self.dir / TRACE_JSONL


# --------------------------------------------------------------------------
# Report-path helpers
# --------------------------------------------------------------------------


def report_stamp(created_at: str) -> str:
    """Derive the filename timestamp from an ISO-8601 ``created_at`` string.

    Matches the formula used in ``eval/live_report.py::write_eval_report``:
    strip colons and hyphens from the datetime portion so the result is a
    safe filesystem component (e.g. ``"20240315T143022.123456Z"``).
    """
    return created_at.replace(":", "").replace("-", "")


def timestamped_report_path(reports_dir: Path | str, stamp: str, difficulty: str) -> Path:
    """Return ``reports_dir / "{stamp}-{difficulty}.json"``."""
    return Path(reports_dir) / f"{stamp}-{difficulty}.json"


def latest_report_path(reports_dir: Path | str) -> Path:
    """Return ``reports_dir / "latest.json"``."""
    return Path(reports_dir) / "latest.json"

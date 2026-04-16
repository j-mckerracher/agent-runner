"""Baseline regression classifier.

Classifies a current pass rate against a stored band as regressed,
improved, unchanged, or insufficient_data.
"""
from __future__ import annotations

from typing import Literal

from agent_runner_shared.models import BaselineBand


def classify(
    current_pass_rate: float,
    band: BaselineBand | None,
) -> Literal["regressed", "improved", "unchanged", "insufficient_data"]:
    """Classify a current pass rate against a baseline band.

    Args:
        current_pass_rate: The measured pass rate for the current run set.
        band: The stored baseline band. If None or sample_size < 2, returns
              'insufficient_data'.

    Returns:
        One of: 'regressed', 'improved', 'unchanged', 'insufficient_data'.
    """
    if band is None or band.sample_size < 2:
        return "insufficient_data"
    if current_pass_rate < band.low:
        return "regressed"
    if current_pass_rate > band.high:
        return "improved"
    return "unchanged"

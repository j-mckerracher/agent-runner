#!/usr/bin/env python3
"""Lightweight tests for generate_final_recap.py (adjusted sys.path so this can be run directly)"""
import json
import os
import sys

# Ensure repo root is on sys.path so 'scripts' package is importable when running this file directly
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from generate_final_recap import generate_concise_recap


def test_basic():
    session = {
        "final_recap": {
            "outcome": "FAIL: behavior tests missing",
            "changed_files": ["libs/shared/core/src/lib/services/error-classification.service.ts"],
            "commands_tests": ["npm ci", "npx nx test core"],
            "decisions_rationale": ["Gate-first policy — avoid guessing test framework"],
            "open_loops": ["Update DoD to accept Jest or add pytest tests"]
        }
    }
    recap = generate_concise_recap(session)
    assert "outcome" in recap and recap["outcome"].startswith("FAIL")
    assert isinstance(recap["changed_files"], list) and len(recap["changed_files"]) == 1
    assert "npx nx test" in " ".join(recap["commands_tests"]) or "npm ci" in " ".join(recap["commands_tests"]) 


def test_redact_and_truncate():
    session = {
        "final_recap": {
            "outcome": "OK",
            "decisions_rationale": [
                "Used token=ABC123SECRET which should be redacted",
                "A very long text\n" + "x\n" * 20
            ]
        }
    }
    recap = generate_concise_recap(session)
    # token should be redacted
    assert "<REDACTED>" in recap["decisions_rationale"][0]
    # long text truncated
    assert "(truncated)" in recap["decisions_rationale"][1]


if __name__ == '__main__':
    try:
        test_basic()
        test_redact_and_truncate()
    except AssertionError as e:
        print("TESTS FAILED", e)
        sys.exit(1)
    print("ALL TESTS PASSED")

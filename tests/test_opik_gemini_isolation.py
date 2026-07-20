"""Optional Gemini SDK import-isolation checks (Prompt 18R-B).

The optional ``google.genai`` SDK must be loaded only inside the Gemini
evaluator path. Importing core workflow modules (``core.steps``,
``core.opik_integration``), ``workflow``, ``artifacts``, or ``eval`` must never
require the SDK, even when a partial ``google`` namespace exists but
``google.genai`` is absent (which raises a plain ``ImportError`` from
``from google import genai``).

Subprocess probes are used for import / ``sys.modules`` assertions so in-process
``sys.modules`` pollution cannot make the checks unreliable. The loader
classification and runtime behavior are exercised in-process with injected
import behavior and fake modules — no real Gemini SDK or network call.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.opik_integration import (
    OptionalIntegrationUnavailableError,
    _load_google_genai,
    call_evaluator_sdk,
)


# ---------------------------------------------------------------------------
# Subprocess probe helpers
# ---------------------------------------------------------------------------

# A meta-path finder that makes selected module names unimportable, raising
# ModuleNotFoundError exactly as CPython would for a genuinely missing module.
_BLOCKER = (
    "import sys\n"
    "class _Blocker:\n"
    "    def __init__(self, names):\n"
    "        self.names = set(names)\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name in self.names or any(name.startswith(n + '.') for n in self.names):\n"
    "            raise ModuleNotFoundError(f'blocked {name}', name=name)\n"
    "        return None\n"
)


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=90,
    )


def _block(names: str) -> str:
    return _BLOCKER + f"sys.meta_path.insert(0, _Blocker({names}))\n"


# ---------------------------------------------------------------------------
# 1. Core / unrelated imports must not require google.genai
# ---------------------------------------------------------------------------

def test_core_steps_imports_without_google_genai():
    code = _block("('google', 'google.genai')") + (
        "import core.steps\n"
        "assert 'google.genai' not in sys.modules\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_core_opik_integration_imports_without_google_genai():
    code = _block("('google', 'google.genai')") + (
        "import core.opik_integration\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_workflow_artifacts_eval_import_without_google_genai():
    code = _block("('google', 'google.genai')") + (
        "import workflow\n"
        "import artifacts\n"
        "import eval\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 2. Partial namespace: google present, google.genai absent
# ---------------------------------------------------------------------------

def test_partial_google_namespace_does_not_break_core_steps():
    # A real, importable `google` namespace package but no `genai` child. This
    # is the exact defect state: `from google import genai` -> ImportError.
    code = (
        "import sys, types\n"
        "google = types.ModuleType('google')\n"
        "google.__path__ = []\n"
        "sys.modules['google'] = google\n"
        + _BLOCKER
        + "sys.meta_path.insert(0, _Blocker(('google.genai',)))\n"
        "import core.steps\n"
        "import core.opik_integration as oi\n"
        "assert 'google.genai' not in sys.modules\n"
        # The loader must classify this as an optional-dependency error.\n
        "try:\n"
        "    oi._load_google_genai()\n"
        "except oi.OptionalIntegrationUnavailableError as exc:\n"
        "    assert isinstance(exc.__cause__, ModuleNotFoundError), exc.__cause__\n"
        "else:\n"
        "    raise AssertionError('expected OptionalIntegrationUnavailableError')\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_opik_integration_does_not_add_google_genai_to_sys_modules():
    code = _block("('google.genai',)") + (
        "import core.opik_integration\n"
        "assert 'google.genai' not in sys.modules, sorted(\n"
        "    m for m in sys.modules if m.startswith('google'))\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_selected_gemini_path_without_sdk_raises_actionable_error():
    # Exercise the real loader (no mocks) under a genuine missing-SDK state.
    code = _block("('google', 'google.genai')") + (
        "import core.opik_integration as oi\n"
        "try:\n"
        "    oi._load_google_genai()\n"
        "except oi.OptionalIntegrationUnavailableError as exc:\n"
        "    assert 'google.genai' in str(exc), str(exc)\n"
        "    assert isinstance(exc.__cause__, ModuleNotFoundError), exc.__cause__\n"
        "    print('OK')\n"
        "else:\n"
        "    raise AssertionError('expected OptionalIntegrationUnavailableError')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 3. Loader classification (in-process, injected import behavior)
# ---------------------------------------------------------------------------

def test_broken_installed_sdk_modulenotfound_is_not_misclassified():
    # An internal dependency of an installed SDK is missing. This must NOT be
    # relabeled as an optional-dependency error; the original error propagates.
    internal_error = ModuleNotFoundError("No module named 'some_internal_dependency'")
    internal_error.name = "some_internal_dependency"

    with patch("core.opik_integration.import_module", side_effect=internal_error):
        with pytest.raises(ModuleNotFoundError) as exc_info:
            _load_google_genai()

    assert exc_info.value is internal_error


def test_installed_sdk_plain_importerror_propagates():
    # A non-ModuleNotFoundError ImportError raised by installed SDK code must
    # propagate unchanged rather than be masked as a missing dependency.
    boom = ImportError("cannot import name 'thing' from partially initialized module")

    with patch("core.opik_integration.import_module", side_effect=boom):
        with pytest.raises(ImportError) as exc_info:
            _load_google_genai()

    assert exc_info.value is boom
    assert not isinstance(exc_info.value, OptionalIntegrationUnavailableError)


def test_missing_sdk_error_message_contains_no_secrets():
    missing = ModuleNotFoundError("No module named 'google.genai'")
    missing.name = "google.genai"

    with patch("core.opik_integration.import_module", side_effect=missing):
        with pytest.raises(OptionalIntegrationUnavailableError) as exc_info:
            _load_google_genai()

    message = str(exc_info.value)
    lowered = message.lower()
    for forbidden in ("api_key", "gemini_api_key", "sk-", "prompt", "secret", "password"):
        assert forbidden not in lowered, message
    assert exc_info.value.__cause__ is missing


# ---------------------------------------------------------------------------
# 4. Installed / fake-installed SDK compatibility and exception discipline
# ---------------------------------------------------------------------------

def _fake_genai(mock_client) -> SimpleNamespace:
    return SimpleNamespace(Client=MagicMock(return_value=mock_client))


def test_fake_installed_sdk_follows_success_path():
    mock_response = MagicMock()
    mock_response.text = "PASS"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    fake = _fake_genai(mock_client)

    with (
        patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
        patch("core.opik_integration.inject_file_contents", return_value=""),
        patch("core.opik_integration._load_google_genai", return_value=fake) as loader,
    ):
        result = call_evaluator_sdk(
            context="Evaluate the report.",
            agent_name="qa-evaluator",
            model="gemini-3.1-flash-lite-preview",
            runner="gemini",
        )

    assert result == "PASS"
    loader.assert_called_once()  # SDK loaded only when the Gemini path runs.
    mock_client.models.generate_content.assert_called_once()
    assert (
        mock_client.models.generate_content.call_args.kwargs.get("model")
        == "gemini-3.1-flash-lite-preview"
    )


def test_keyboardinterrupt_propagates_through_integration_op():
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = KeyboardInterrupt()
    fake = _fake_genai(mock_client)

    with (
        patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
        patch("core.opik_integration.inject_file_contents", return_value=""),
        patch("core.opik_integration._load_google_genai", return_value=fake),
    ):
        with pytest.raises(KeyboardInterrupt):
            call_evaluator_sdk(
                context="Evaluate the report.",
                agent_name="qa-evaluator",
                model="gemini-3.1-flash-lite-preview",
                runner="gemini",
            )


def test_systemexit_propagates_through_integration_op():
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = SystemExit(1)
    fake = _fake_genai(mock_client)

    with (
        patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
        patch("core.opik_integration.inject_file_contents", return_value=""),
        patch("core.opik_integration._load_google_genai", return_value=fake),
    ):
        with pytest.raises(SystemExit):
            call_evaluator_sdk(
                context="Evaluate the report.",
                agent_name="qa-evaluator",
                model="gemini-3.1-flash-lite-preview",
                runner="gemini",
            )

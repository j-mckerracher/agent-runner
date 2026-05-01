"""Plugin import and validation helpers for eval checks."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterable, Sequence, Union

from .models import CheckDefinition, EvalStory
from .plugin_api import PLUGIN_API_VERSION

PathLike = Union[str, Path]

_ALLOWED_EVAL_IMPORTS = {"eval.check_helpers", "eval.plugin_api", "eval.models"}
_ALLOWED_EVAL_IMPORT_NAMES = {"check_helpers", "plugin_api", "models"}
_VALIDATED_PLUGIN_IDS: set[int] = set()


class PluginLoadError(RuntimeError):
    """Raised when a plugin cannot be imported or fails validation."""


def import_plugin_module(path: PathLike) -> ModuleType:
    plugin_path = Path(path)
    if not plugin_path.exists():
        raise PluginLoadError(f"Plugin file not found: {plugin_path}")
    _enforce_dependency_boundary(plugin_path)
    module_name = f"eval_plugin_{plugin_path.stem}_{abs(hash(plugin_path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"Unable to load plugin spec: {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginLoadError(f"Plugin import failed for {plugin_path}: {exc}") from exc
    return module


def load_plugin(path: PathLike) -> object:
    module = import_plugin_module(path)
    plugin = _find_plugin(module)
    validate_plugin(plugin)
    return plugin


def validate_plugin(plugin: object) -> None:
    plugin_id = id(plugin)
    if plugin_id in _VALIDATED_PLUGIN_IDS:
        return
    api_version = getattr(plugin, "api_version", None)
    if api_version != PLUGIN_API_VERSION:
        raise PluginLoadError(
            f"Plugin API mismatch: expected {PLUGIN_API_VERSION}, got {api_version!r}"
        )
    get_checks = getattr(plugin, "get_checks", None)
    if not callable(get_checks):
        raise PluginLoadError("Plugin must define callable get_checks(story)")
    validate = getattr(plugin, "validate", None)
    if not callable(validate):
        raise PluginLoadError("Plugin must define callable validate()")
    try:
        validate()
    except Exception as exc:
        raise PluginLoadError(f"Plugin validate() failed: {exc}") from exc
    _VALIDATED_PLUGIN_IDS.add(plugin_id)


def plugin_checks(
    plugin: object,
    story: EvalStory,
    built_in_checks: Iterable[CheckDefinition] = (),
) -> Sequence[CheckDefinition]:
    validate_plugin(plugin)
    plugin_story_id = getattr(plugin, "story_id", None)
    if plugin_story_id is not None and plugin_story_id != story.story_id:
        raise PluginLoadError(
            f"Plugin story_id mismatch: expected {story.story_id!r}, got {plugin_story_id!r}"
        )
    get_checks = getattr(plugin, "get_checks", None)
    if not callable(get_checks):
        raise PluginLoadError("Plugin must define callable get_checks(story)")
    try:
        checks = list(get_checks(story))
    except Exception as exc:
        raise PluginLoadError(f"Plugin get_checks() failed: {exc}") from exc
    seen = {check.id for check in built_in_checks}
    for check in checks:
        if not isinstance(check, CheckDefinition):
            raise PluginLoadError("Plugin get_checks() must return CheckDefinition instances")
        if check.id in seen:
            raise PluginLoadError(f"Duplicate check id from plugin: {check.id}")
        seen.add(check.id)
    return checks


def _find_plugin(module: ModuleType) -> object:
    if hasattr(module, "get_plugin"):
        get_plugin = getattr(module, "get_plugin")
        if not callable(get_plugin):
            raise PluginLoadError("get_plugin must be callable")
        plugin = get_plugin()
    elif hasattr(module, "plugin"):
        plugin = getattr(module, "plugin")
    elif _looks_like_plugin(module):
        plugin = module
    else:
        plugin = _instantiate_plugin_class(module)
    if inspect.isclass(plugin):
        plugin = plugin()
    if plugin is None:
        raise PluginLoadError("Plugin factory returned None")
    return plugin


def _instantiate_plugin_class(module: ModuleType) -> object:
    candidates = []
    for _, value in vars(module).items():
        if inspect.isclass(value) and value.__module__ == module.__name__ and _looks_like_plugin(value):
            candidates.append(value)
    if len(candidates) == 1:
        return candidates[0]()
    if len(candidates) > 1:
        raise PluginLoadError("Multiple plugin classes found; export get_plugin() or plugin")
    raise PluginLoadError("Plugin must export get_plugin(), plugin, a plugin class, or module-level API")


def _looks_like_plugin(value: object) -> bool:
    return hasattr(value, "api_version") and callable(getattr(value, "get_checks", None))


def _enforce_dependency_boundary(plugin_path: Path) -> None:
    try:
        tree = ast.parse(plugin_path.read_text(encoding="utf-8"), filename=str(plugin_path))
    except SyntaxError as exc:
        raise PluginLoadError(f"Plugin syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _validate_import(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise PluginLoadError("Plugin relative imports are not allowed")
            _validate_import_from(node.module or "", [alias.name for alias in node.names])


def _validate_import(module_name: str) -> None:
    if not module_name:
        return
    if module_name in _ALLOWED_EVAL_IMPORTS:
        return
    if module_name.startswith("eval."):
        raise PluginLoadError(
            "Plugin imports are limited to stdlib plus eval.check_helpers, eval.plugin_api, and eval.models"
        )
    root = module_name.split(".", 1)[0]
    stdlib = getattr(sys, "stdlib_module_names", set())
    if root in stdlib or root == "__future__":
        return
    raise PluginLoadError(
        f"Plugin import {module_name!r} is outside the allowed stdlib/eval.check_helpers boundary"
    )


def _validate_import_from(module_name: str, imported_names: Sequence[str]) -> None:
    if module_name == "eval" and all(name in _ALLOWED_EVAL_IMPORT_NAMES for name in imported_names):
        return
    _validate_import(module_name)

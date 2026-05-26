from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def gui_root() -> Path:
    return repo_root() / "gui"


def read_gui_markup() -> str:
    return (gui_root() / "index.html").read_text(encoding="utf-8")


def read_gui_styles() -> str:
    return (gui_root() / "assets" / "css" / "app.css").read_text(encoding="utf-8")


def read_gui_script(name: str) -> str:
    return (gui_root() / "assets" / "js" / name).read_text(encoding="utf-8")


def read_gui_scripts() -> str:
    js_dir = gui_root() / "assets" / "js"
    load_order = [
        "app-core.js",
        "telemetry.js",
        "app-shell.js",
        "runs.js",
        "content-views.js",
        "background.js",
    ]
    return "\n\n".join((js_dir / name).read_text(encoding="utf-8") for name in load_order)


def read_gui_sources() -> str:
    return "\n\n".join([read_gui_markup(), read_gui_styles(), read_gui_scripts()])

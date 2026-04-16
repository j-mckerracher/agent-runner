.PHONY: test lint install clean

PY := python3
PYTHONPATH := packages/shared:packages/runner:packages/registry:packages/harness

install:
$(PY) -m venv .venv
.venv/bin/pip install -e .[dev]

test:
PYTHONPATH=$(PYTHONPATH) .venv/bin/pytest -q

lint:
.venv/bin/lint-imports -c pyproject.toml

clean:
rm -rf runs/* cassettes/* .pytest_cache
find . -type d -name __pycache__ -exec rm -rf {} +

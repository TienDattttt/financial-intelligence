PYTHON ?= python

.PHONY: spike test lint run-mvp

spike:
	$(PYTHON) scripts/spike_vnstock6.py
	git status --porcelain CLAUDE.md AGENTS.md

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check
	$(PYTHON) -m mypy --strict src/finintel scripts/spike_vnstock6.py tests/unit

run-mvp:
	$(PYTHON) -m finintel.pipeline.cli run --source file

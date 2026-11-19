PYTHON ?= python

.PHONY: spike test lint run-mvp

spike:
	$(PYTHON) scripts/spike_vnstock6.py
	git status --porcelain AGENT.md

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff format --check src scripts tests dashboard
	$(PYTHON) -m ruff check src scripts tests dashboard
	$(PYTHON) -m mypy --strict src/finintel scripts/spike_vnstock6.py scripts/spike_option_b.py tests/unit

run-mvp:
	$(PYTHON) -m finintel.pipeline.cli run --source file

PYTHON ?= python

.PHONY: spike test lint

spike:
	$(PYTHON) scripts/spike_vnstock6.py

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check
	$(PYTHON) -m mypy src/finintel scripts/spike_vnstock6.py

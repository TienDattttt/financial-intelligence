PYTHON ?= python

.PHONY: spike test lint run-mvp

spike:
	$(PYTHON) scripts/spike_vnstock6.py

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check
	$(PYTHON) -m mypy src/finintel scripts/spike_vnstock6.py

run-mvp: export PYTHONPATH := src
run-mvp:
	$(PYTHON) -m finintel.pipeline.cli run $(PIPELINE_ARGS)

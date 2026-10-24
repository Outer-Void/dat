PYTHON ?= python3

.PHONY: install fmt lint types test wheel selfaudit ci

install:
	$(PYTHON) -m pip install -e '.[dev]'

fmt:
	ruff format .

lint:
	ruff format --check .
	ruff check .

types:
	mypy

test:
	$(PYTHON) -m pytest

wheel:
	PYTHON=$(PYTHON) bash scripts/verify_wheel.sh

# DAT gates itself: blocking findings in this repository fail CI.
selfaudit:
	$(PYTHON) -m dat audit . --gate --no-target-policy --sarif dat.sarif --quiet

ci: lint types test wheel selfaudit

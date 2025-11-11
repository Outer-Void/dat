# ──────────────────────────────────────────────────────────────────────────────
# DAT (Dev Audit Tool) — Cross-Platform Makefile
# v3.0.0
# 
# Targets:
# make help        # Show this help
# make setup       # Install dev tools
# make setup-dev   # Install dev dependencies from requirements-dev.txt
# make fmt         # Format with ruff
# make lint        # Lint with ruff and mypy
# make test        # Run tests with coverage
# make clean       # Remove build artifacts
# make build       # Build sdist+wheel
# make verify      # Test built package
# make dist        # Build and verify package
# make publish     # Upload to PyPI
# make tag         # Create git tag
# make install     # Editable install
# make uninstall   # Uninstall package
# make version     # Print version info
#
# Environment:
#   PYTHON=python3    # Python interpreter
#   TWINE_*          # PyPI credentials
#   REPOSITORY_URL   # Optional repository URL
#
# Cross-platform: Linux, macOS, Windows (with make), Termux
# ──────────────────────────────────────────────────────────────────────────────

# Shell detection
ifeq (,$(shell which bash 2>/dev/null))
SHELL := cmd
.SHELLFLAGS := /c
else
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
endif

# Python configuration
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= ruff
TWINE ?= twine
BUILD ?= $(PYTHON) -m build

PYPROJECT := pyproject.toml

# Read package info from pyproject.toml
define TOML_PY
import sys, pathlib
pp = pathlib.Path("$(PYPROJECT)")
if not pp.exists():
    print("", end="")
    sys.exit(0)
try:
    import tomllib as toml
except ImportError:
    try:
        import tomli as toml
    except ImportError:
        print("", end="")
        sys.exit(1)
data = toml.loads(pp.read_bytes())
print(data.get("project",{}).get("name","dat") + "|" + data.get("project",{}).get("version","0.0.0"))
endef

PKG_VER := $(shell $(PYTHON) -c "$(TOML_PY)")
PKG := $(firstword $(subst |, ,$(PKG_VER)))
VERSION := $(word 2,$(subst |, ,$(PKG_VER)))
TAG := v$(VERSION)

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@echo ""
	@echo "\033[1mDAT Cross-Platform Makefile\033[0m (v3.0.0)"
	@echo ""
	@echo "Targets:"
	@awk -F: -v width=20 '/^[a-zA-Z0-9_\-]+:.*##/ {printf "  \033[36m%-*s\033[0m %s\n", width, $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Current: $(PKG) $(VERSION)"

.PHONY: version
version: ## Print project name and version
	@echo "$(PKG) $(VERSION)"

.PHONY: setup
setup: ## Install dev tools (ruff, pytest, build, twine)
	$(PIP) install -U pip
	$(PIP) install -U ruff pytest pytest-cov build twine mypy
	@echo "✓ Development tools installed"

.PHONY: setup-dev
setup-dev: ## Install all dev dependencies from requirements-dev.txt
	$(PIP) install -r requirements-dev.txt
	@echo "✓ Development dependencies installed"

.PHONY: fmt
fmt: ## Format with ruff
	$(RUFF) format .

.PHONY: lint
lint: ## Lint with ruff and mypy
	$(RUFF) check .
	@echo "→ Running mypy..."
	@mypy src/dat || echo "⚠ mypy check completed with issues"

.PHONY: test
test: ## Run tests with coverage
	$(PYTEST) --cov=src/dat --cov-report=term-missing

.PHONY: test-verbose
test-verbose: ## Run tests with verbose output
	$(PYTEST) -v --cov=src/dat --cov-report=html

.PHONY: clean
clean: ## Clean build artifacts
	@echo "→ Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	@if command -v find >/dev/null 2>&1; then \
		find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true; \
		find . -name "*.pyc" -delete 2>/dev/null || true; \
	fi

.PHONY: build
build: clean ## Build sdist and wheel
	$(BUILD) .

.PHONY: verify
verify: ## Install built wheel and smoke test
	@echo "→ Verifying package installation..."
	@tmp=$$(mktemp -d 2>/dev/null || mktemp -d -t dat-verify) ; \
	$(PYTHON) -m venv $$tmp/venv ; \
	. $$tmp/venv/bin/activate ; \
	$(PYTHON) -m pip install -U pip >/dev/null ; \
	$(PYTHON) -m pip install dist/*.whl >/dev/null ; \
	if command -v $(PKG) >/dev/null 2>&1 ; then \
		echo "✓ $(PKG) installed successfully"; \
		$(PKG) --version || $(PKG) -V || true ; \
	else \
		echo "⚠ Note: No console script named '$(PKG)' found"; \
	fi ; \
	deactivate ; rm -rf $$tmp

.PHONY: dist
dist: build verify ## Build and verify package

.PHONY: publish
publish: dist ## Upload to PyPI (set TWINE_* env vars)
	@if [ -z "$$TWINE_USERNAME" ] || [ -z "$$TWINE_PASSWORD" ]; then \
	 echo "ERROR: TWINE_USERNAME/TWINE_PASSWORD not set"; \
	 echo "Export these environment variables with your PyPI token"; \
	 exit 2; \
	fi
	@if [ -n "$$REPOSITORY_URL" ]; then \
	 $(TWINE) upload --repository-url "$$REPOSITORY_URL" dist/* ; \
	else \
	 $(TWINE) upload dist/* ; \
	fi
	@echo "✓ Package published to PyPI"

.PHONY: tag
tag: ## Create git tag v<version> and push
	@if [ -z "$(VERSION)" ] || [ "$(VERSION)" = "0.0.0" ]; then \
	 echo "ERROR: could not read version from $(PYPROJECT)"; exit 2; \
	fi
	git tag -a "$(TAG)" -m "Release $(TAG)"
	git push origin "$(TAG)"
	@echo "✓ Git tag $(TAG) created and pushed"

.PHONY: install
install: ## Editable install for local development
	$(PIP) install -e .[dev]

.PHONY: uninstall
uninstall: ## Uninstall package
	-$(PIP) uninstall -y $(PKG) || true
	@echo "✓ Package uninstalled"

.PHONY: dev
dev: setup install ## Setup complete development environment

.PHONY: all
all: fmt lint test build ## Format, lint, test, and build

.PHONY: security-scan
security-scan: ## Run security scans (requires bandit and safety)
	@if command -v bandit >/dev/null 2>&1; then \
		echo "→ Running bandit security scan..."; \
		bandit -r src/dat || true; \
	else \
		echo "⚠ bandit not installed, run: pip install bandit"; \
	fi
	@if command -v safety >/dev/null 2>&1; then \
		echo "→ Running safety dependency check..."; \
		safety check || true; \
	else \
		echo "⚠ safety not installed, run: pip install safety"; \
	fi

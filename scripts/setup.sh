#!/usr/bin/env bash
# Development environment setup for pyrtl_433 (uv-first).
#
# Installs the project plus its dev dependency group (pytest, ruff, mypy, mutmut,
# ...) into a uv-managed virtual environment from the locked uv.lock.
set -euo pipefail

uv sync --locked --dev

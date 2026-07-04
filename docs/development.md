# Development

## Testing & mutation contract

Tests follow a three-tier naming convention:

- `test_*` — behavioural unit tests of the public API.
- `test_mut_*` — mutation-killing tests written to pin specific mutants.
- `test_mut_*_floor` — the floor tests that hold a module's mutation score at or
  above its ratchet baseline.

The library holds a per-module mutation-score **floor** (killed / total mutants),
enforced by the ratchet:

| Module | Killed / total | Score |
| --- | --- | --- |
| `client.py` | 453 / 459 | 0.987 |
| `normalizer.py` | 74 / 75 | 0.987 |
| `replay.py` | 100 / 101 | 0.990 |
| `sdr.py` | 68 / 70 | 0.971 |
| `_urls.py` | 22 / 22 | 1.000 |
| **Overall** | **717 / 727** | **0.986** |

## Local commands

uv-first — there are no `requirements*.txt` files; dev/test tooling lives in
`pyproject.toml`'s `[dependency-groups]` and is locked in `uv.lock`:

```sh
uv sync --dev            # create the venv + install dev/test tooling from uv.lock
# or: bash scripts/setup.sh

uv run pytest -n auto                                   # run the test suite (parallel)
uv run ruff check . && uv run ruff format --check .     # lint + format
uv run mypy pyrtl_433/                                  # strict type check
uv run mutmut run                                       # mutation testing
uv run python scripts/mutation_stats.py > stats.json    # collect per-module stats
uv run python scripts/mutation_ratchet.py --mode floor --stats stats.json  # enforce the floor
```

## Continuous integration

CI runs the same gates (lint, format, strict mypy, tests with a 95% coverage
floor, and the mutation-score ratchet) on every push and pull request via GitHub
Actions — see
[`.github/workflows/`](https://github.com/rtl-433-hass/pyrtl_433/tree/main/.github/workflows).

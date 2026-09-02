# Development

## Testing & mutation contract

Tests follow a three-tier naming convention:

- `test_*` — behavioural unit tests of the public API.
- `test_mut_*` — mutation-killing tests written to pin specific mutants.
- `test_mut_*_floor` — the floor tests that hold a module's mutation score at or
  above its ratchet baseline.

The library holds a per-module mutation-score **floor** (killed / total mutants),
enforced by the ratchet. The gate itself is
[`mutmut-ratchet`](https://github.com/rtl-433-hass/mutmut-ratchet), a shared
package: it scopes a PR's mutation run to the modules the diff could affect,
fans the work across time-balanced shards, and fails only on a real per-file
regression against the committed `scripts/mutation_baseline.json`. Its per-repo
settings — the package path, the escalation triggers, and the test -> source
overrides for tests whose name does not map 1:1 to a module — live in
`[tool.mutmut_ratchet]` in `pyproject.toml`.

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
uv run mutmut-ratchet stats > stats.json                # collect per-module stats
uv run mutmut-ratchet ratchet --mode floor --stats stats.json   # enforce the floor
uv run mutmut-ratchet ratchet --mode strict --stats stats.json  # check the baseline is current
```

After a *full* `mutmut run`, `--update` ratchets the committed baseline upward
and `mutmut-ratchet timings` refreshes the shard weights:

```sh
uv run mutmut-ratchet ratchet --mode floor --stats stats.json --update
uv run mutmut-ratchet timings
```

## Continuous integration

CI runs the same gates (lint, format, strict mypy, tests with a 95% coverage
floor, and the mutation-score ratchet) on every push and pull request via GitHub
Actions — see
[`.github/workflows/`](https://github.com/rtl-433-hass/pyrtl_433/tree/main/.github/workflows).

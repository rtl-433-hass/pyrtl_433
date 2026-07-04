"""Tests for ``scripts/mutation_targets.py`` — the PR mutation-target resolver.

The mutation job uses this script to decide which source modules a PR should
mutate. The mapping is name-based with an explicit override table; a wrong entry
silently escalates every touching PR to a full run (or, worse, under-scopes and
misses a floor regression), so these tests keep the table honest and guard
against the mis-mapping class of bug (e.g. a test whose name points at a module
that does not exist).

Some library modules / test files are produced by sibling tasks; the checks that
need a real file on disk skip or restrict themselves to what exists, then
strengthen naturally once the layout is complete.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PKG = "pyrtl_433"
_SCRIPT = _REPO_ROOT / "scripts" / "mutation_targets.py"

# mutmut copies only the package, tests/, and pyproject into its ``mutants/``
# sandbox — not scripts/ — so this meta-test cannot load the script there. It
# adds no mutation coverage anyway (it exercises no package source), so skip the
# module in that environment; the normal pytest job (where scripts/ exists) runs
# it in full.
if not _SCRIPT.is_file():
    pytest.skip(
        "scripts/mutation_targets.py absent (mutmut sandbox); this meta-test "
        "runs in the normal pytest job only",
        allow_module_level=True,
    )


def _load_targets_module():
    """Load the standalone script (it lives in ``scripts/``, not a package)."""
    spec = importlib.util.spec_from_file_location("mutation_targets", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mt = _load_targets_module()

# Tests with no 1:1 package module by design, so escalating to a full run when
# they change is correct: broad tests and tooling/meta tests. Kept here (not in
# the script) so adding one is a deliberate, reviewed edit.
_NO_SINGLE_MODULE = {
    "tests/test_mutation_targets.py",  # meta: tests this very script
    "tests/test_mutation_shards.py",  # meta: tests scripts/mutation_shards.py
}


@pytest.fixture(autouse=True)
def _chdir_repo_root(monkeypatch):
    """``source_for_test`` probes relative paths; run from the repo root."""
    monkeypatch.chdir(_REPO_ROOT)


def test_source_module_change_scopes_to_itself():
    full, sources = mt.resolve([f"{_PKG}/normalizer.py"])
    assert full is False
    assert sources == {f"{_PKG}/normalizer.py"}


def test_conforming_test_maps_to_its_module(tmp_path, monkeypatch):
    """``test[_mut]_<name>.py`` resolves to ``<name>.py`` when the module exists.

    Driven against a throwaway package so it exercises the resolver's happy path
    without depending on which library modules sibling tasks have landed yet.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / _PKG).mkdir()
    (tmp_path / _PKG / "normalizer.py").write_text("")
    full, sources = mt.resolve(["tests/test_mut_normalizer.py"])
    assert full is False
    assert sources == {f"{_PKG}/normalizer.py"}


def test_full_run_trigger_escalates():
    full, sources = mt.resolve(["tests/conftest.py"])
    assert full is True
    assert sources == set()


def test_docs_only_change_scopes_with_no_sources():
    full, sources = mt.resolve(["README.md"])
    assert full is False
    assert sources == set()


def test_unmappable_test_escalates():
    # A test whose name maps to no source module must escalate to a full run;
    # under-scoping would silently skip a floor check, so escalating is correct.
    full, _ = mt.resolve(["tests/test_totally_unknown_thing.py"])
    assert full is True


@pytest.mark.parametrize("test_file, modules", sorted(mt.EXPLICIT_TEST_SOURCES.items()))
def test_explicit_map_entry_scopes_to_its_modules(test_file, modules):
    full, sources = mt.resolve([test_file])
    assert full is False, f"{test_file} should scope, not trigger a full run"
    assert sources == {f"{_PKG}/{module}" for module in modules}


def test_explicit_map_targets_exist_when_test_is_present():
    """Every override key that already exists maps to real modules.

    Sibling tasks create the test files and their modules asynchronously, so an
    entry whose test file is not on disk yet is skipped (it will be re-checked
    once created); an entry whose test file *is* present must map to real modules
    — that catches the half-rotted-mapping class of bug.
    """
    for test_file, modules in mt.EXPLICIT_TEST_SOURCES.items():
        if not (_REPO_ROOT / test_file).is_file():
            continue
        for module in modules:
            target = _REPO_ROOT / _PKG / module
            assert target.is_file(), f"{test_file} maps to missing module: {module}"


def test_no_test_file_silently_escalates():
    """Every ``tests/test_*.py`` resolves, is explicitly mapped, or is declared broad.

    This is the guard for the original bug: a test whose name maps to a
    non-existent module silently escalates every touching PR to a full run. A new
    such test now fails here until it is added to ``EXPLICIT_TEST_SOURCES`` or
    ``_NO_SINGLE_MODULE``.
    """
    offenders = []
    for path in sorted((_REPO_ROOT / "tests").glob("test_*.py")):
        rel = f"tests/{path.name}"
        if rel in mt.EXPLICIT_TEST_SOURCES or rel in _NO_SINGLE_MODULE:
            continue
        if mt.source_for_test(path.stem) is None:
            offenders.append(rel)
    assert not offenders, (
        "these tests escalate to a full mutation run but are neither in "
        f"EXPLICIT_TEST_SOURCES nor declared broad in _NO_SINGLE_MODULE: {offenders}"
    )

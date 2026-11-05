"""Unit tests for scripts/acceptance_checksum.py."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "acceptance_checksum.py"
_spec = importlib.util.spec_from_file_location("acceptance_checksum", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    spec = tmp_path / "acceptance"
    (spec / "features").mkdir(parents=True)
    (spec / "features" / "a.feature").write_text("Feature: a\n")
    (spec / "conftest.py").write_text("x = 1\n")
    return spec, tmp_path / "acceptance.sha256"


def test_acceptance_checksum_equal_passes(layout: tuple[Path, Path]) -> None:
    spec, manifest = layout
    assert mod.record(spec, manifest) == 0
    assert mod.verify(spec, manifest) == 0


def test_acceptance_checksum_ignores_pycache(layout: tuple[Path, Path]) -> None:
    spec, manifest = layout
    mod.record(spec, manifest)
    (spec / "__pycache__").mkdir()
    (spec / "__pycache__" / "c.cpython-311.pyc").write_bytes(b"x")
    assert mod.verify(spec, manifest) == 0


def test_acceptance_checksum_changed_file_fails(
    layout: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    spec, manifest = layout
    mod.record(spec, manifest)
    (spec / "features" / "a.feature").write_text("Feature: edited\n")
    assert mod.verify(spec, manifest) == 1
    assert "changed: features/a.feature" in capsys.readouterr().err


def test_acceptance_checksum_added_file_fails(
    layout: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    spec, manifest = layout
    mod.record(spec, manifest)
    (spec / "new.feature").write_text("Feature: new\n")
    assert mod.verify(spec, manifest) == 1
    assert "added: new.feature" in capsys.readouterr().err


def test_acceptance_checksum_missing_manifest_fails(
    layout: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    spec, manifest = layout
    assert mod.verify(spec, manifest) == 1
    assert "manifest missing" in capsys.readouterr().err

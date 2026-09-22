"""Shared fixtures: an isolated MINICA_DATA per test and a cached root CA."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "run"))

import ca_core  # noqa: E402
from init_ca import init_ca  # noqa: E402


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point mini-ca at an empty data directory."""
    monkeypatch.setenv("MINICA_DATA", str(tmp_path))
    for var in ("MINICA_CA_NAME", "MINICA_ROOT_DAYS", "MINICA_LEAF_DAYS", "MINICA_RENEW_DAYS"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture(scope="session")
def _root_ca(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the RSA-4096 root once per session; tests copy it."""
    root = tmp_path_factory.mktemp("rootCA")
    init_ca(root)
    return root


@pytest.fixture
def ca(data: Path, _root_ca: Path) -> Path:
    """A data directory that already holds a root CA."""
    target = ca_core.ca_dir()
    target.mkdir(parents=True)
    for name in (ca_core.CA_KEY_NAME, ca_core.CA_CERT_NAME):
        shutil.copyfile(_root_ca / name, target / name)
    return target

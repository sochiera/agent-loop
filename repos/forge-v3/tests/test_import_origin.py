"""The imported forge package must be this candidate's src layout."""

import importlib.util
from pathlib import Path

import forge

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (ROOT / "src" / "forge" / "__init__.py").resolve()


def test_import_origin_is_candidate_src():
    assert Path(forge.__file__).resolve() == EXPECTED
    spec = importlib.util.find_spec("forge")
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).resolve() == EXPECTED
    assert not (ROOT / "forge").exists()
    assert "site-packages" not in Path(forge.__file__).resolve().parts

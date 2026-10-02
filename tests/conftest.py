import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("QMC_UPSTREAM_DIR", "/home/user/moruku36/qwen-multimodal-colab"))
sys.path.insert(0, str(ROOT))
if (UPSTREAM / "src" / "qmc").is_dir():
    sys.path.insert(0, str(UPSTREAM / "src"))

needs_upstream = pytest.mark.skipif(not (UPSTREAM / "src" / "qmc").is_dir(), reason="upstream checkout not found")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("QMC_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("QMC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QMC_LOCAL_DB", str(tmp_path / "db" / "history.db"))
    return tmp_path

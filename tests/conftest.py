import shutil
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "sample.eml"


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    shutil.copy(FIX, d / "sample.eml")
    return d


@pytest.fixture
def store(tmp_path):
    from dealradar.store import Store
    return Store(str(tmp_path / "t.db"))

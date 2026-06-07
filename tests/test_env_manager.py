import pytest
from pathlib import Path
from app.core.env_manager import EnvManager


@pytest.fixture
def env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text('EXISTING_KEY="old_value"\nOTHER_KEY="keep"\n')
    return f


def test_read_env_file(env_file):
    mgr = EnvManager(env_file)
    data = mgr.read()
    assert data["EXISTING_KEY"] == "old_value"
    assert data["OTHER_KEY"] == "keep"


def test_read_missing_file(tmp_path):
    mgr = EnvManager(tmp_path / "missing.env")
    assert mgr.read() == {}


def test_update_existing_key(env_file):
    mgr = EnvManager(env_file)
    mgr.update({"EXISTING_KEY": "new_value"})
    data = mgr.read()
    assert data["EXISTING_KEY"] == "new_value"
    assert data["OTHER_KEY"] == "keep"


def test_add_new_key(env_file):
    mgr = EnvManager(env_file)
    mgr.update({"NEW_KEY": "new_value"})
    data = mgr.read()
    assert data["NEW_KEY"] == "new_value"
    assert data["EXISTING_KEY"] == "old_value"


def test_file_permissions_set_to_600(env_file):
    mgr = EnvManager(env_file)
    mgr.update({"KEY": "val"})
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_comments_preserved(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# This is a comment\nKEY=value\n")
    mgr = EnvManager(f)
    mgr.update({"KEY": "new"})
    content = f.read_text()
    assert "# This is a comment" in content

"""Settings, secrets and backups must survive updates: git never tracks them."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
USER_DATA = [
    ".env",
    "config/services.json",
    "config/google-tokens.json",
    "config/google-credentials.json",
    "config/anything-new.json",
    "state/destinations/sftp_id",
    "backups/wikijs/wikijs_20261005.tar.gz",
    "logs/backup.log",
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="needs the git checkout")
def test_user_data_is_ignored_and_untracked():
    not_ignored = [p for p in USER_DATA if _git("check-ignore", "-q", p).returncode != 0]
    assert not not_ignored, f"git would track (and a pull could overwrite): {not_ignored}"
    tracked = [
        p
        for p in _git("ls-files", "config", "state", "backups", "logs", ".env").stdout.split()
        if p != "config/.gitkeep"
    ]
    assert not tracked, f"tracked user-data files: {tracked}"

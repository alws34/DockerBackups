"""Thread-safe reader and writer for a ``.env``-style key/value file."""

from __future__ import annotations

from pathlib import Path
from threading import Lock


def _key_of(line: str) -> str | None:
    """The key a ``KEY=value`` line sets, or None for blanks, comments and other lines."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.partition("=")[0].strip()


class EnvManager:
    """Read and update a ``.env`` file while preserving comments and layout."""

    def __init__(self, env_file: Path) -> None:
        self.env_file = env_file
        self._lock = Lock()

    def read(self) -> dict[str, str]:
        """Parse the env file into a dict, ignoring blanks and comment lines."""
        if not self.env_file.exists():
            return {}
        result: dict[str, str] = {}
        for line in self.env_file.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                result[key.strip()] = value.strip().strip('"').strip("'")
        return result

    def update(self, updates: dict[str, str]) -> None:
        """Apply key/value updates, rewriting existing keys and appending new ones.

        Raises ``ValueError`` for a value with a line break or NUL: written as-is it
        would end the line and could smuggle another setting into the file.
        """
        for key, value in updates.items():
            if any(c in value for c in "\r\n\0"):
                raise ValueError(f"{key} can't contain line breaks.")
        with self._lock:
            existing_lines = (
                self.env_file.read_text().splitlines() if self.env_file.exists() else []
            )
            updated_keys: set[str] = set()
            new_lines: list[str] = []
            for line in existing_lines:
                key = _key_of(line)
                if key in updates:
                    new_lines.append(f'{key}="{updates[key]}"')
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            for key, value in updates.items():
                if key not in updated_keys:
                    new_lines.append(f'{key}="{value}"')
            self.env_file.write_text("\n".join(new_lines) + "\n")
            self.env_file.chmod(0o600)

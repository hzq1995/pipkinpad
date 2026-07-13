"""Encrypted local settings. API keys never appear in plaintext on disk."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring
from cryptography.fernet import Fernet

SERVICE_NAME = "pipkinpad"
KEY_NAME = "settings-encryption-key"


@dataclass
class AISettings:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    temperature: float | None = None
    password_salt: str = ""
    password_hash: str = ""
    auth_secret: str = ""

    def public(self) -> dict:
        return {"base_url": self.base_url, "model": self.model, "temperature": self.temperature,
                "api_key_configured": bool(self.api_key)}


def _keyring_available() -> bool:
    """Check if a functional keyring backend is available."""
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".config" / "pipkinpad" / "settings.json"
        self._key_path = self.path.parent / ".encryption-key"
        self._use_keyring = _keyring_available()

    def _get_key(self) -> str:
        """Get encryption key from keyring or fallback to local file."""
        if self._use_keyring:
            try:
                key = keyring.get_password(SERVICE_NAME, KEY_NAME)
                if not key:
                    key = Fernet.generate_key().decode("ascii")
                    keyring.set_password(SERVICE_NAME, KEY_NAME, key)
                return key
            except Exception:
                # Fallback to file-based key if keyring fails
                self._use_keyring = False

        # File-based fallback (for headless Linux servers without keyring)
        if self._key_path.exists():
            return self._key_path.read_text(encoding="utf-8").strip()
        key = Fernet.generate_key().decode("ascii")
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_text(key, encoding="utf-8")
        # Restrict permissions to owner only (Unix)
        try:
            os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return key

    def _fernet(self) -> Fernet:
        return Fernet(self._get_key().encode("ascii"))

    def load(self) -> AISettings:
        if not self.path.exists():
            return AISettings()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        decrypted = self._fernet().decrypt(payload["ciphertext"].encode("ascii"))
        return AISettings(**json.loads(decrypted))

    def save(self, settings: AISettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet().encrypt(json.dumps(asdict(settings)).encode("utf-8"))
        self.path.write_text(json.dumps({"version": 1, "ciphertext": encrypted.decode("ascii")}), encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        if self._use_keyring:
            try:
                keyring.delete_password(SERVICE_NAME, KEY_NAME)
            except keyring.errors.PasswordDeleteError:
                pass
        else:
            self._key_path.unlink(missing_ok=True)

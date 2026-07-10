"""Encrypted local settings. API keys never appear in plaintext on disk."""

from __future__ import annotations

import base64
import json
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

    def public(self) -> dict:
        return {"base_url": self.base_url, "model": self.model, "temperature": self.temperature,
                "api_key_configured": bool(self.api_key)}


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".config" / "pipkinpad" / "settings.json"

    def _fernet(self) -> Fernet:
        key = keyring.get_password(SERVICE_NAME, KEY_NAME)
        if not key:
            key = Fernet.generate_key().decode("ascii")
            keyring.set_password(SERVICE_NAME, KEY_NAME, key)
        return Fernet(key.encode("ascii"))

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
        try:
            keyring.delete_password(SERVICE_NAME, KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass

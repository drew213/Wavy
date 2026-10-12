"""Credential vault: envelope encryption for connected-account secrets.

Each secret gets a fresh 256-bit data key (AES-GCM). The data key is wrapped by the key
provider's master key. The ciphertext is bound (as AES-GCM associated data) to the row it
belongs to, so a ciphertext copied onto another client's connection won't decrypt.

Providers:
  LocalKeyProvider  a 32-byte master key in a 0600 file (dev, single-box installs)
  EnvKeyProvider    the master key from the host's secret store (cloud hosting)
  (later)           a cloud KMS provider implementing wrap/unwrap; nothing else changes

Plaintext secrets exist only in memory, inside `with vault.open(...)`. Never log them.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import stat
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = 1


class KeyProvider(Protocol):
    def wrap(self, data_key: bytes, aad: bytes) -> tuple[bytes, str]: ...
    def unwrap(self, wrapped: bytes, key_ref: str, aad: bytes) -> bytes: ...
    def derive(self, label: str) -> bytes: ...


class VaultError(Exception):
    pass


class _MasterKey:
    _key: bytes
    key_ref: str

    def wrap(self, data_key: bytes, aad: bytes) -> tuple[bytes, str]:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, data_key, aad), self.key_ref

    def derive(self, label: str) -> bytes:
        """A 32-byte key for one purpose (signing links, say), derived from the master key and never stored."""
        return hmac.new(self._key, b"wavyos/derive|" + label.encode(), hashlib.sha256).digest()

    def unwrap(self, wrapped: bytes, key_ref: str, aad: bytes) -> bytes:
        if key_ref != self.key_ref:
            raise VaultError(f"secret was wrapped by {key_ref}, not this vault's key ({self.key_ref})")
        return AESGCM(self._key).decrypt(wrapped[:12], wrapped[12:], aad)


class EnvKeyProvider(_MasterKey):
    """The master key as a secret string from the host's secret store (WAVYOS_MASTER_KEY).

    A base64 value that decodes to 32 bytes is used as is; any other string of at least 32
    characters is stretched with SHA-256. Changing the value makes stored secrets unreadable.
    """

    def __init__(self, secret: str):
        secret = secret.strip()
        if len(secret) < 32:
            raise VaultError("WAVYOS_MASTER_KEY must be at least 32 characters")
        try:
            key = base64.b64decode(secret, validate=True)
        except (binascii.Error, ValueError):
            key = b""
        self._key = key if len(key) == 32 else hashlib.sha256(secret.encode()).digest()
        self.key_ref = "env:" + hashlib.sha256(self._key).hexdigest()[:16]


def key_provider(settings) -> KeyProvider:
    if settings.master_key:
        return EnvKeyProvider(settings.master_key)
    return LocalKeyProvider(settings.master_key_file)


class LocalKeyProvider(_MasterKey):
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(AESGCM.generate_key(bit_length=256))
        mode = stat.S_IMODE(self.path.stat().st_mode)
        if mode & 0o077:
            raise VaultError(f"master key file {self.path} is readable by others (mode {oct(mode)}); chmod 600 it")
        self._key = self.path.read_bytes()
        if len(self._key) != 32:
            raise VaultError("master key file must hold exactly 32 bytes")
        self.key_ref = "local:" + hashlib.sha256(self._key).hexdigest()[:16]


def binding(org_id: str, client_id: str, provider: str, external_account_id: str) -> bytes:
    return f"wavyos/v{FORMAT_VERSION}|{org_id}|{client_id}|{provider}|{external_account_id}".encode()


class Vault:
    def __init__(self, provider: KeyProvider):
        self.provider = provider

    def derive(self, label: str) -> bytes:
        return self.provider.derive(label)

    def seal(self, secret: str, aad: bytes) -> tuple[bytes, str]:
        """Encrypt a secret. Returns (ciphertext, key_ref)."""
        if not secret:
            raise VaultError("empty secret")
        data_key = AESGCM.generate_key(bit_length=256)
        wrapped, key_ref = self.provider.wrap(data_key, aad)
        nonce = os.urandom(12)
        body = AESGCM(data_key).encrypt(nonce, secret.encode(), aad)
        return struct.pack(">BH", FORMAT_VERSION, len(wrapped)) + wrapped + nonce + body, key_ref

    def _unseal(self, blob: bytes, key_ref: str, aad: bytes) -> str:
        try:
            version, n = struct.unpack(">BH", blob[:3])
            if version != FORMAT_VERSION:
                raise VaultError(f"unknown secret format v{version}")
            wrapped, rest = blob[3:3 + n], blob[3 + n:]
            data_key = self.provider.unwrap(wrapped, key_ref, aad)
            return AESGCM(data_key).decrypt(rest[:12], rest[12:], aad).decode()
        except VaultError:
            raise
        except Exception as e:  # InvalidTag, struct errors: never echo the blob
            raise VaultError("secret could not be decrypted for this connection") from e

    @contextmanager
    def open(self, blob: bytes, key_ref: str, aad: bytes) -> Iterator[str]:
        """Yield the plaintext for the duration of the block only."""
        secret = self._unseal(blob, key_ref, aad)
        try:
            yield secret
        finally:
            del secret

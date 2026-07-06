"""Simple AES encryption for API keys in .env file."""

import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


def _get_key(secret: str) -> bytes:
    """Derive 32-byte AES key from secret string."""
    return hashlib.sha256(secret.encode()).digest()


def encrypt(plaintext: str, secret: str) -> str:
    """AES-256-GCM encrypt, return base64-encoded ciphertext."""
    key = _get_key(secret)
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    # Prepend IV + tag to ciphertext, encode as base64
    return base64.b64encode(iv + encryptor.tag + ciphertext).decode("ascii")


def decrypt(encoded: str, secret: str) -> str:
    """Decrypt base64-encoded AES-256-GCM ciphertext."""
    key = _get_key(secret)
    raw = base64.b64decode(encoded)
    iv, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
    decryptor = cipher.decryptor()
    return (decryptor.update(ciphertext) + decryptor.finalize()).decode("utf-8")


def encrypt_if_needed(value: str, secret: str) -> str:
    """Encrypt value if it's a plaintext API key (not already encrypted)."""
    if not value or value.startswith("ENC:"):
        return value
    return f"ENC:{encrypt(value, secret)}"


def decrypt_if_needed(value: str, secret: str) -> str:
    """Decrypt if value has ENC: prefix, otherwise return as-is."""
    if value and value.startswith("ENC:"):
        return decrypt(value[4:], secret)
    return value
